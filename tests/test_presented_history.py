import tempfile
import unittest

from muse_shroom.models import ContractError, SearchRequest
from muse_shroom.search import SearchEngine, _public_semantic_candidate, _quote_grade_candidate
from muse_shroom.services import MuseCore
from muse_shroom.storage import Store

from tests.helpers import FrozenGitHub, repo
from tests.test_sidecar import REQUEST, _selection


FOCUS_REPOS = [
    repo("seen/one", 500, description="focus timer"),
    repo("seen/two", 400, description="focus blocker"),
    repo("new/three", 50, description="focus journal"),
    repo("new/four", 40, description="focus music"),
]
FOCUS_READMES = {
    item["full_name"]: f"# {item['full_name']}\nA {item['description']} app.\n## Usage\nRun it."
    for item in FOCUS_REPOS
}


def _ranking(*names):
    return {"display_order": list(names), "items": [{"repo": name} for name in names]}


def _readme_evidence(candidate):
    return next(item["id"] for item in candidate["evidence"] if item.get("kind") == "readme_excerpt")


class PresentedHistoryStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(directory.name)
        self.addCleanup(self.store.close)

    def test_history_counts_sessions_and_skips_the_current_one(self):
        self.store.save_ranking("s1", _ranking("Seen/One", "seen/two"))
        self.store.save_ranking("s2", _ranking("seen/one"))
        self.store.save_ranking("s3", {"display_order": [], "items": [], "no_recommendation": {"reason": "none"}})
        everything = self.store.presented_history()
        self.assertEqual(set(everything), {"seen/one", "seen/two"})
        self.assertEqual(everything["seen/one"]["times"], 2)
        self.assertRegex(everything["seen/one"]["last_at"], r"^\d{4}-\d{2}-\d{2}$")
        others = self.store.presented_history(exclude_search_id="s2")
        self.assertEqual(others["seen/one"]["times"], 1)

    def test_empty_store_has_no_history(self):
        self.assertEqual(self.store.presented_history(), {})


class DeferPresentedTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(directory.name)
        self.addCleanup(self.store.close)
        self.store.save_ranking("earlier", _ranking("seen/one", "seen/two"))
        self.github = FrozenGitHub([("focus", FOCUS_REPOS)], readmes=FOCUS_READMES)

    def _search(self, *, enrich_limit, constraints=None):
        request = {"request": "专注工具", "problem_concepts": ["focus"], "artifact_types": ["application"]}
        if constraints:
            request["constraints"] = constraints
        engine = SearchEngine(self.store, self.github, enrich_limit=enrich_limit, semantic_sidecar=False)
        return engine.search(SearchRequest.from_dict(request), "quick")

    def _pool(self, search_id):
        return {item["full_name"]: item for item in self.store.load_search(search_id)["candidates"]}

    def test_unseen_candidates_take_the_readme_and_shortlist_places(self):
        output = self._search(enrich_limit=2)
        self.assertEqual({item["full_name"] for item in output["candidates"]}, {"new/three", "new/four"})
        self.assertEqual(output["coverage"]["previously_presented_in_pool"], 2)
        pool = self._pool(output["search_id"])
        self.assertEqual(pool["seen/one"]["previously_presented"]["times"], 1)
        self.assertNotIn("previously_presented", pool["new/three"])

    def test_seen_candidates_fill_places_the_unseen_leave_empty(self):
        output = self._search(enrich_limit=3)
        flagged = [item for item in output["candidates"] if item.get("previously_presented")]
        self.assertEqual(len(output["candidates"]), 3)
        self.assertEqual(len(flagged), 1)
        self.assertTrue(flagged[0]["full_name"].startswith("seen/"))

    def test_include_previously_presented_marks_without_deferring(self):
        output = self._search(enrich_limit=2, constraints={"include_previously_presented": True})
        self.assertTrue(any(item.get("previously_presented") for item in output["candidates"]))


class PresentedAcrossSessionsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data_dir = directory.name
        timer = repo("tools/timer", 300, description="pomodoro timer for focus")
        github = FrozenGitHub(
            [
                ("focus", [timer]),
                ("pomodoro", [timer]),
                ("distraction", [repo("labs/blocker", 60, description="distraction blocking for focus")]),
            ],
            readmes={
                "tools/timer": "# Timer\nA pomodoro timer for focus.\n## Usage\nStart it.",
                "labs/blocker": "# Blocker\nDistraction blocking for focus.\n## Usage\nBlock.",
            },
            repos={"tools/timer": timer},
        )
        self.core = MuseCore(data_dir=self.data_dir, github=github)

    def _rank_timer(self, search):
        candidate = next(item for item in search["candidates"] if item["full_name"] == "tools/timer")
        return self.core.rank(search["search_id"], {
            "selection": [_selection(candidate, "pomodoro", _readme_evidence(candidate))],
        })

    def test_unranked_search_is_reused_and_ranked_search_is_not(self):
        first = self.core.search(REQUEST, "quick")
        self.assertEqual(self.core.search(REQUEST, "quick")["search_id"], first["search_id"])
        self.assertEqual(self._rank_timer(first)["next_action"], "done")
        again = self.core.search(REQUEST, "quick")
        self.assertNotEqual(again["search_id"], first["search_id"])
        timer = next(item for item in again["candidates"] if item["full_name"] == "tools/timer")
        self.assertEqual(timer["previously_presented"]["times"], 1)

    def test_supply_and_rank_carry_the_mark(self):
        first = self.core.search(REQUEST, "quick")
        ranked = self._rank_timer(first)
        self.assertNotIn("previously_presented", ranked["items"][0])
        self.assertEqual(ranked["coverage"]["previously_presented_count"], 0)

        again = self.core.search(REQUEST, "quick")
        supplied = self.core.supply(again["search_id"], ["tools/timer"], "draft anchor")
        self.assertEqual(supplied["supplied"][0]["previously_presented"]["times"], 1)
        # Allowed when the user asks to see it again, and marked so the Agent can tell.
        ranked_again = self._rank_timer(again)
        self.assertEqual(ranked_again["next_action"], "done")
        self.assertEqual(ranked_again["items"][0]["previously_presented"]["times"], 1)
        self.assertEqual(ranked_again["coverage"]["previously_presented_count"], 1)


class PresentedFieldTests(unittest.TestCase):
    MARK = {"times": 2, "last_at": "2026-09-08"}

    def test_compact_projections_keep_the_mark(self):
        candidate = {
            "full_name": "seen/one", "html_url": "https://github.com/seen/one",
            "evidence": [], "previously_presented": self.MARK,
        }
        self.assertEqual(_public_semantic_candidate(candidate)["previously_presented"], self.MARK)
        self.assertEqual(_quote_grade_candidate(candidate)["previously_presented"], self.MARK)

    def test_constraint_must_be_a_boolean(self):
        request = {
            "request": "专注工具", "problem_concepts": ["focus"],
            "constraints": {"include_previously_presented": "yes"},
        }
        with self.assertRaises(ContractError):
            SearchRequest.from_dict(request, strict=True)
        request["constraints"]["include_previously_presented"] = True
        parsed = SearchRequest.from_dict(request, strict=True)
        self.assertTrue(parsed.constraints["include_previously_presented"])


if __name__ == "__main__":
    unittest.main()
