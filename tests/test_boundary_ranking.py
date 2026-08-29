import tempfile
import unittest

from muse_shroom.boundary import annotate_candidate_mechanisms
from muse_shroom.boundary_score import (
    contribution_score, gated_boundary_value, redundancy_penalty,
)
from muse_shroom.models import SearchRequest
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
            category=None, adjacent=False):
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


if __name__ == "__main__":
    unittest.main()
