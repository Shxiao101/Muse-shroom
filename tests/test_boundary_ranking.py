import tempfile
import unittest

from muse_shroom.boundary import annotate_candidate_mechanisms, build_boundary
from muse_shroom.boundary_score import (
    contribution_score, gated_boundary_value, redundancy_penalty,
)
from muse_shroom.models import ContractError, SearchRequest
from muse_shroom.ranking import rank_search
from muse_shroom.selection import shortlist_select
from muse_shroom.storage import Store

from tests.helpers import repo


def _item(full_name: str, stars: int, description: str, *, topics=None, kinds=None):
    item = repo(full_name, stars, description=description, topics=topics or [])
    item["readme"] = f"# Tool\n{description}\n## Installation\nInstall it.\n## Usage\nRun it."
    item["matched_kinds"] = kinds or ["core"]
    item["discovery_paths"] = [{
        "kind": "query", "query": description, "query_kind": "core", "position": 1,
        "concept_id": "core:0", "term": "focus",
    }]
    item["evidence"] = [
        {"id": f"repo:{full_name.lower()}:metadata", "kind": "github_metadata",
         "source": item["html_url"], "facts": {"stars": stars, "license": "MIT"}},
        {"id": f"repo:{full_name.lower()}:readme", "kind": "readme",
         "source": item["html_url"] + "#readme",
         "facts": {"has_install": True, "has_usage": True}},
        {"id": f"repo:{full_name.lower()}:readme:overview", "kind": "readme_excerpt",
         "source": item["html_url"] + "#readme",
         "facts": {"snippet_type": "overview", "text": description}},
    ]
    return item


FOCUS_REQUEST = SearchRequest.from_dict({
    "request": "stay focused",
    "problem_concepts": ["focus"],
    "mechanisms": ["pomodoro", "blocking"],
    "exploration_directions": ["commitment device", "biofeedback"],
})


def _assess(item, *, relevance=85, uniqueness=70, usability=80, transferability=None,
            category=None, adjacent=False, mechanism=None, boundary_value=None):
    name = item["full_name"]
    payload = {
        "repo": name, "relevance": relevance, "uniqueness": uniqueness, "usability": usability,
        "difficulty": "easy", "use_case": "Documented workflow",
        "category": category or (item.get("topics") or ["focus"])[0],
        "artifact_type": "application",
        "reasons": [{"text": "Documented workflow", "evidence_ids": [f"repo:{name.lower()}:readme:overview"]}],
        "risks": [{"text": "Check scope", "evidence_ids": [f"repo:{name.lower()}:metadata"]}],
    }
    if transferability is not None:
        payload["transferability"] = transferability
    if mechanism is not None:
        payload["mechanism"] = mechanism
    if boundary_value is not None:
        payload["boundary_value"] = boundary_value
    return payload


class BoundaryRankingTests(unittest.TestCase):
    def test_shortlist_is_not_filled_by_one_mechanism(self):
        items = [
            _item(f"pomo/timer{index}", 20 + index, "Pomodoro timer")
            for index in range(8)
        ]
        items.extend(
            _item(f"block/site{index}", 15 + index, "Website blocking")
            for index in range(4)
        )
        items.append(_item("habit/commit", 8, "Commitment device for self-control"))
        items.append(_item("sense/bio", 6, "Biofeedback focus sensor"))
        for item in items:
            annotate_candidate_mechanisms(item, FOCUS_REQUEST)

        selected, _ = shortlist_select(items, FOCUS_REQUEST, mode="deep")
        names = [name for item in selected for name in (
            value.get("name") for value in item.get("mechanisms") or []
        ) if name]
        unique = {str(name).casefold() for name in names}

        self.assertLessEqual(len(selected), 12)
        self.assertGreaterEqual(len(unique), 3)
        self.assertLessEqual(sum(name.casefold() == "pomodoro" for name in names), 4)

    def test_second_pomodoro_loses_value_after_the_first_is_presented(self):
        first = _item("pomo/one", 40, "Pomodoro timer")
        second = _item("pomo/two", 38, "Pomodoro timer")
        blocking = _item("block/one", 12, "Website blocking")
        commit = _item("habit/commit", 9, "Commitment device")
        for item in (first, second, blocking, commit):
            annotate_candidate_mechanisms(item, FOCUS_REQUEST)
        presented = {"pomodoro"}
        self.assertGreater(
            contribution_score(blocking, presented),
            contribution_score(second, presented),
        )
        self.assertGreater(
            contribution_score(commit, presented),
            contribution_score(second, presented),
        )
        self.assertGreater(redundancy_penalty(second, presented), redundancy_penalty(blocking, presented))

    def test_low_relevance_novelty_does_not_rank(self):
        useful = _item("pomo/one", 80, "Pomodoro timer")
        shiny = _item("odd/leap", 3, "Commitment device")
        annotate_candidate_mechanisms(useful, FOCUS_REQUEST)
        annotate_candidate_mechanisms(shiny, FOCUS_REQUEST)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.create_search("s", FOCUS_REQUEST.to_dict(), "deep")
            store.save_candidate("s", useful)
            store.save_candidate("s", shiny)
            result = rank_search(store, "s", [
                _assess(useful, relevance=88),
                _assess(shiny, relevance=20, uniqueness=99, transferability=95),
            ])
            store.close()
        returned = {item["repo"].lower() for bucket in result["buckets"].values() for item in bucket}
        self.assertIn("pomo/one", returned)
        self.assertNotIn("odd/leap", returned)

    def test_evidence_gate_caps_novelty_bonus(self):
        named = _item("name/only", 10, "A productivity utility")
        named["readme"] = ""
        named["evidence"] = [named["evidence"][0]]
        named["mechanisms"] = []
        self.assertEqual(gated_boundary_value(90, named, relevance=80, evidence_completeness=0), 15)

    def test_wildcard_enters_without_displacing_the_anchor(self):
        anchor = _item("big/timer", 20000, "Pomodoro timer", kinds=["core"])
        wildcard = _item(
            "lab/bind", 40, "Commitment device used outside focus tools",
            kinds=["adjacent"],
        )
        annotate_candidate_mechanisms(anchor, FOCUS_REQUEST)
        annotate_candidate_mechanisms(wildcard, FOCUS_REQUEST)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.create_search("s", FOCUS_REQUEST.to_dict(), "deep")
            store.save_candidate("s", anchor)
            store.save_candidate("s", wildcard)
            result = rank_search(store, "s", [
                _assess(anchor, relevance=90, category="pomodoro"),
                _assess(wildcard, relevance=62, uniqueness=88, transferability=86, category="commitment"),
            ])
            store.close()
        popular = {item["repo"].lower() for item in result["buckets"]["popular"]}
        all_items = [item for bucket in result["buckets"].values() for item in bucket]
        self.assertIn("big/timer", popular)
        self.assertTrue(any(item["repo"].lower() == "lab/bind" for item in all_items))
        wildcard_row = next(item for item in all_items if item["repo"].lower() == "lab/bind")
        self.assertIn(wildcard_row["boundary_role"], {"leap", "wildcard", "edge"})
        self.assertIn("boundary_summary", result)
        self.assertIn("newly_presented_mechanisms", result)

    def test_rank_is_deterministic(self):
        items = [
            _item("pomo/one", 900, "Pomodoro timer"),
            _item("block/one", 80, "Website blocking", kinds=["adjacent"]),
            _item("habit/commit", 20, "Commitment device", kinds=["adjacent"]),
        ]
        for item in items:
            annotate_candidate_mechanisms(item, FOCUS_REQUEST)
        assessments = [
            _assess(items[0], relevance=88),
            _assess(items[1], relevance=80, transferability=60),
            _assess(items[2], relevance=70, uniqueness=90, transferability=84),
        ]
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.create_search("s", FOCUS_REQUEST.to_dict(), "deep")
            for item in items:
                store.save_candidate("s", item)
            first = rank_search(store, "s", assessments)
            second = rank_search(store, "s", assessments)
            store.close()
        self.assertEqual(first["buckets"], second["buckets"])
        self.assertEqual(
            [item.get("boundary_role") for bucket in first["buckets"].values() for item in bucket],
            [item.get("boundary_role") for bucket in second["buckets"].values() for item in bucket],
        )
        self.assertEqual(first["boundary_summary"], second["boundary_summary"])

    def test_explanations_follow_sequential_presented_state(self):
        first = _item("habit/one", 50, "Commitment device", kinds=["adjacent"])
        second = _item("habit/two", 40, "Commitment device", kinds=["adjacent"])
        third = _item("sense/bio", 30, "Biofeedback focus sensor", kinds=["adjacent"])
        for item in (first, second, third):
            annotate_candidate_mechanisms(item, FOCUS_REQUEST)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.create_search("s", FOCUS_REQUEST.to_dict(), "deep")
            for item in (first, second, third):
                store.save_candidate("s", item)
            result = rank_search(store, "s", [
                _assess(first, relevance=82, uniqueness=80, transferability=70),
                _assess(second, relevance=80, uniqueness=78, transferability=68),
                _assess(third, relevance=78, uniqueness=88, transferability=72),
            ])
            store.close()
        by_name = {
            item["repo"].lower(): item
            for bucket in result["buckets"].values() for item in bucket
        }
        commit_new = [
            "commitment device" in [name.casefold() for name in by_name[key]["new_mechanisms"]]
            for key in ("habit/one", "habit/two")
        ]
        self.assertEqual(sum(commit_new), 1)
        self.assertTrue(any(
            "biofeedback" in name.casefold()
            for name in by_name["sense/bio"]["new_mechanisms"]
        ))
        repeat = by_name["habit/two" if not commit_new[1] else "habit/one"]
        self.assertEqual(repeat["new_mechanisms"], [])
        self.assertIn("already presented", repeat["why_different"])

    def test_assessment_mechanism_must_match_evidence(self):
        item = _item("pomo/one", 40, "Pomodoro timer")
        annotate_candidate_mechanisms(item, FOCUS_REQUEST)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.create_search("s", FOCUS_REQUEST.to_dict(), "deep")
            store.save_candidate("s", item)
            with self.assertRaises(ContractError):
                rank_search(store, "s", [_assess(item, mechanism="quantum focus")])
            result = rank_search(store, "s", [
                _assess(item, mechanism="pomodoro", boundary_value=40),
            ])
            extra = _assess(item, mechanism="pomodoro")
            extra["mechanism_novelty"] = 99
            ignored = rank_search(store, "s", [extra])
            store.close()
        self.assertEqual(result["coverage"]["returned"], 1)
        self.assertEqual(ignored["coverage"]["returned"], 1)

    def test_display_order_explanations_ignore_internal_selection_order_and_iteration_history(self):
        adjacent = _item("adj/commit", 80, "Commitment device", kinds=["adjacent"])
        popular = _item("big/commit", 25000, "Commitment device", kinds=["core"])
        gem = _item("tiny/bio", 12, "Biofeedback focus sensor", kinds=["core"])
        for item in (adjacent, popular, gem):
            annotate_candidate_mechanisms(item, FOCUS_REQUEST)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.create_search("s", FOCUS_REQUEST.to_dict(), "deep")
            for item in (adjacent, popular, gem):
                store.save_candidate("s", item)
            store.save_boundary_snapshot(
                "s", "iterate",
                build_boundary(
                    [adjacent, popular, gem], [adjacent, popular, gem], FOCUS_REQUEST,
                ).to_dict(),
                iteration=1,
            )
            result = rank_search(store, "s", [
                _assess(adjacent, relevance=78, uniqueness=80, transferability=70),
                _assess(popular, relevance=90, uniqueness=60),
                _assess(gem, relevance=76, uniqueness=85, transferability=60),
            ])
            store.close()
        buckets = result["buckets"]
        display = [name.lower() for name in result["display_order"]]
        expected = [
            item["repo"].lower()
            for name in ("popular", "gems", "adjacent")
            for item in buckets[name]
        ]
        self.assertEqual(display, expected)
        self.assertEqual(result["next_action"], "done")
        self.assertTrue(result["selection_order"])
        by_name = {
            item["repo"].lower(): item
            for bucket in buckets.values() for item in bucket
        }
        self.assertTrue(any(
            "commitment" in name.casefold()
            for name in by_name["big/commit"]["new_mechanisms"]
        ))
        self.assertEqual(by_name["adj/commit"]["new_mechanisms"], [])
        self.assertEqual(
            [name.casefold() for name in result["newly_presented_mechanisms"]],
            [name.casefold() for name in result["boundary_summary"]["new_mechanisms_introduced"]],
        )

    def test_boundary_composition_is_independent_of_compatibility_bucket_order(self):
        anchor = _item("big/timer", 25000, "Pomodoro timer", kinds=["core"])
        leap = _item("sense/bio", 40, "Biofeedback focus sensor", kinds=["adjacent"])
        gem = _item("small/block", 20, "Website blocking", kinds=["core"])
        for item in (anchor, leap, gem):
            annotate_candidate_mechanisms(item, FOCUS_REQUEST)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.create_search("s", FOCUS_REQUEST.to_dict(), "deep")
            for item in (anchor, leap, gem):
                store.save_candidate("s", item)
            result = rank_search(store, "s", [
                _assess(anchor, relevance=91, uniqueness=60),
                _assess(leap, relevance=88, uniqueness=94, transferability=88),
                _assess(gem, relevance=64, uniqueness=62, transferability=45),
            ])
            store.close()
        primary = [item["repo"] for item in result["items"]]
        compatibility_order = [
            item["repo"] for name in ("popular", "gems", "adjacent")
            for item in result["buckets"][name]
        ]
        self.assertEqual(primary, result["display_order"])
        self.assertNotEqual(primary, compatibility_order)
        without_buckets = dict(result)
        without_buckets.pop("buckets")
        self.assertEqual([item["repo"] for item in without_buckets["items"]], primary)

    def test_first_display_item_without_mechanisms_does_not_claim_prior_presentation(self):
        unlabeled = _item("plain/tool", 25000, "Useful focus application", kinds=["core"])
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.create_search("s", FOCUS_REQUEST.to_dict(), "deep")
            store.save_candidate("s", unlabeled)
            result = rank_search(
                store, "s", [_assess(unlabeled, relevance=90, uniqueness=60)],
            )
            store.close()

        item = next(
            item for bucket in result["buckets"].values() for item in bucket
            if item["repo"].lower() == "plain/tool"
        )
        self.assertEqual(item["new_mechanisms"], [])
        self.assertTrue(item["why_different"].startswith("has no labeled mechanism"))

    def test_rank_keeps_mixed_candidate_without_presenting_rejected_mechanism(self):
        request = SearchRequest.from_dict({
            "request": "stay focused",
            "problem_concepts": ["focus"],
            "mechanisms": [
                {"term": "pomodoro", "aliases": ["timer"]},
                "blocking",
            ],
            "exploration_directions": ["commitment device", "biofeedback"],
        })
        mixed = _item(
            "block/mixed", 5000, "Pomodoro timer with website blocking", kinds=["core"],
        )
        annotate_candidate_mechanisms(mixed, request)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.create_search("s", request.to_dict(), "deep")
            store.save_candidate("s", mixed)
            store.save_boundary_snapshot(
                "s", "iterate",
                build_boundary(
                    [mixed], [mixed], request,
                    rejected_directions=["timer"],
                ).to_dict(),
                iteration=1,
            )
            result = rank_search(
                store, "s", [_assess(mixed, relevance=92, mechanism="timer")],
            )
            store.close()

        item = next(item for bucket in result["buckets"].values() for item in bucket)
        mechanisms = {value["name"].casefold() for value in item["mechanisms"]}
        self.assertIn("blocking", mechanisms)
        self.assertNotIn("pomodoro", mechanisms)
        self.assertNotIn("pomodoro", {name.casefold() for name in item["new_mechanisms"]})
        self.assertNotIn("pomodoro", item["why_different"].casefold())
        self.assertEqual(item["assessment"]["mechanism"], "")
        self.assertIn("pomodoro", {
            name.casefold() for name in result["boundary"]["recalled_mechanisms"]
        })
        self.assertNotIn("pomodoro", {
            name.casefold() for name in result["boundary"]["presented_mechanisms"]
        })
        self.assertNotIn("pomodoro", {
            name.casefold() for name in result["newly_presented_mechanisms"]
        })


if __name__ == "__main__":
    unittest.main()
