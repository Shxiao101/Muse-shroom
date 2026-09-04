import tempfile
import unittest

from muse_shroom.models import ContractError
from muse_shroom.ranking import rank_search
from muse_shroom.storage import Store

from tests.helpers import repo


def candidate(full_name, stars, text):
    item = repo(full_name, stars, description=text, topics=["focus"])
    item["readme"] = f"# Tool\n{text}\n"
    item["readme_sha"] = f"sha-{full_name}"
    item["evidence"] = [
        {
            "id": f"repo:{full_name.lower()}:metadata",
            "kind": "github_metadata",
            "facts": {
                "stars": stars,
                "forks": stars // 10,
                "open_issues": 2,
                "archived": False,
                "license": "MIT",
                "topics": ["focus"],
            },
        },
        {
            "id": f"repo:{full_name.lower()}:readme:overview",
            "kind": "readme_excerpt",
            "facts": {"text": text, "sha": f"sha-{full_name}"},
        },
    ]
    item["discovery_paths"] = [{"kind": "query", "query": "focus"}]
    return item


def selected(item, *, label, role="edge", evidence_id=None, quote=None):
    text = quote or item["evidence"][1]["facts"]["text"]
    return {
        "repo": item["full_name"],
        "rationale": "The mechanism transfers to the stated need.",
        "mechanism_label": label,
        "source_term": text.split()[0],
        "quote": text,
        "evidence_ids": [
            evidence_id or f"repo:{item['full_name'].lower()}:readme:overview"
        ],
        "boundary_role": role,
    }


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.search_id = "search-1"
        self.store.create_search(
            self.search_id,
            {"request": "focus tools", "problem_concepts": ["focus"]},
            "deep",
        )
        self.first = candidate("owner/first", 10, "Blocks distracting apps on a schedule.")
        self.second = candidate("owner/second", 100, "Shows attention changes as live color.")
        self.store.save_candidate(self.search_id, self.first)
        self.store.save_candidate(self.search_id, self.second)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_agent_order_is_preserved_and_raw_facts_replace_scores(self):
        result = rank_search(self.store, self.search_id, [
            selected(self.first, label="commitment device", role="wildcard"),
            selected(self.second, label="ambient biofeedback", role="anchor"),
        ])

        self.assertEqual(result["display_order"], ["owner/first", "owner/second"])
        self.assertEqual([item["repo"] for item in result["items"]], result["display_order"])
        self.assertNotIn("scores", result["items"][0])
        self.assertEqual(result["items"][0]["forks"], 1)
        self.assertEqual(result["items"][0]["open_issues"], 2)
        self.assertEqual(result["items"][0]["boundary_role"], "wildcard")

    def test_evidence_owned_by_another_candidate_rejects_only_that_item(self):
        wrong = f"repo:{self.second['full_name'].lower()}:readme:overview"
        result = rank_search(self.store, self.search_id, [
            selected(self.first, label="commitment device", evidence_id=wrong),
            selected(self.second, label="ambient display"),
        ])

        self.assertEqual(result["display_order"], ["owner/second"])
        self.assertEqual(result["rejected_items"][0]["repo"], "owner/first")
        self.assertIn(
            f"evidence_not_owned:{wrong}",
            result["rejected_items"][0]["reasons"],
        )

    def test_non_verbatim_quote_is_rejected(self):
        result = rank_search(self.store, self.search_id, [
            selected(self.first, label="commitment device", quote="Text not in the snapshot"),
        ])

        self.assertEqual(result["items"], [])
        self.assertEqual(
            result["rejected_items"][0]["reasons"],
            ["quote_not_verbatim_at_recorded_sha"],
        )

    def test_agent_label_need_not_appear_in_repository_text(self):
        result = rank_search(self.store, self.search_id, [
            selected(self.first, label="commitment device"),
        ])

        self.assertEqual(result["display_order"], ["owner/first"])
        self.assertNotIn("commitment device", self.first["readme"])
        self.assertEqual(result["items"][0]["mechanism_label"], "commitment device")

    def test_all_items_rejected_stays_open_for_a_corrected_selection(self):
        result = rank_search(self.store, self.search_id, [
            selected(self.first, label="commitment device", quote="Absent from the snapshot"),
        ])

        self.assertEqual(result["items"], [])
        self.assertEqual(result["next_action"], "rank")
        self.assertIsNone(self.store.get_ranking(self.search_id))
        self.assertEqual(
            result["rejected_items"][0]["evidence_ids_checked"],
            ["repo:owner/first:readme:overview"],
        )

        corrected = rank_search(self.store, self.search_id, [
            selected(self.first, label="commitment device"),
        ])
        self.assertEqual(corrected["next_action"], "done")
        self.assertEqual(corrected["display_order"], ["owner/first"])
        self.assertIsNotNone(self.store.get_ranking(self.search_id))

    def test_one_accepted_item_is_terminal_and_saved(self):
        result = rank_search(self.store, self.search_id, [
            selected(self.first, label="commitment device"),
            selected(self.second, label="ambient feedback", quote="Absent from the snapshot"),
        ])

        self.assertEqual(result["next_action"], "done")
        self.assertEqual(result["display_order"], ["owner/first"])
        self.assertEqual(len(result["rejected_items"]), 1)
        self.assertIsNotNone(self.store.get_ranking(self.search_id))

    def test_single_line_quote_verifies_against_wrapped_recorded_text(self):
        # The contract forbids multi-line quotes, so the wrap is always on the recorded
        # side: the Agent reads rendered README text and submits it as one line.
        wrapped = self.first["evidence"][1]["facts"]["text"].replace(
            " on a", chr(10) + "   on a",
        )
        self.first["evidence"][1]["facts"]["text"] = wrapped
        self.store.save_candidate(self.search_id, self.first)

        result = rank_search(self.store, self.search_id, [
            selected(self.first, label="commitment device",
                     quote="Blocks distracting apps on a schedule."),
        ])

        self.assertEqual(result["display_order"], ["owner/first"])

    def test_differing_word_or_letter_case_still_fails(self):
        for quote in (
            "Blocks distracting tabs on a schedule.",
            "blocks distracting apps on a schedule.",
        ):
            with self.subTest(quote=quote):
                result = rank_search(self.store, self.search_id, [
                    selected(self.first, label="commitment device", quote=quote),
                ])
                self.assertEqual(result["items"], [])
                self.assertEqual(
                    result["rejected_items"][0]["reasons"],
                    ["quote_not_verbatim_at_recorded_sha"],
                )

    def test_invalid_selection_shape_fails_at_the_contract_boundary(self):
        with self.assertRaises(ContractError):
            rank_search(
                self.store,
                self.search_id,
                [{"repo": "owner/first", "rationale": "missing fields"}],
                strict=True,
            )


if __name__ == "__main__":
    unittest.main()
