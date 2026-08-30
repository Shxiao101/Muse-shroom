import tempfile
import unittest
from pathlib import Path

from evaluation.cassette import CassetteGitHub
from muse_shroom import github as github_module
from muse_shroom.boundary import annotate_candidate_mechanisms, build_boundary
from muse_shroom.models import SearchRequest
from muse_shroom.queries import build_queries
from muse_shroom.ranking import rank_search
from muse_shroom.search import SearchEngine, public_candidate
from muse_shroom.storage import Store

from tests.helpers import FrozenGitHub, repo


class BoundaryTests(unittest.TestCase):
    def test_old_and_new_request_schemas_share_a_canonical_shape(self):
        old = SearchRequest.from_dict({
            "request": "stay focused", "core_concepts": ["focus"],
            "adjacent_concepts": ["biofeedback"],
        })
        new = SearchRequest.from_dict({
            "request": "stay focused", "problem_concepts": ["focus"],
            "mechanisms": ["pomodoro"], "exploration_directions": ["biofeedback"],
        })

        self.assertEqual(old.problem_concepts[0].term, "focus")
        self.assertEqual(old.exploration_directions[0].term, "biofeedback")
        self.assertNotIn("core_concepts", old.to_dict())
        self.assertEqual(new.mechanisms[0].term, "pomodoro")

    def test_new_query_sources_use_multiple_mechanism_aliases(self):
        request = SearchRequest.from_dict({
            "request": "stay focused", "problem_concepts": ["focus management"],
            "mechanisms": [{
                "term": "distraction blocking",
                "aliases": ["website blocker", "app blocker"],
            }],
            "exploration_directions": ["commitment device"],
            "artifact_types": ["application"],
        })

        queries = build_queries(request)

        self.assertLessEqual(len(queries), 12)
        self.assertTrue({"problem", "mechanism", "exploration", "typed", "gem"} <= {
            item["kind"] for item in queries
        })
        mechanism_terms = {item["term"] for item in queries if item["kind"] == "mechanism"}
        self.assertGreaterEqual(len(mechanism_terms), 2)

    def test_mechanism_matching_requires_description_topics_or_readme_evidence(self):
        request = SearchRequest.from_dict({
            "request": "focus", "problem_concepts": ["focus"],
            "mechanisms": [{"term": "pomodoro", "aliases": ["focus timer"]}],
            "exploration_directions": ["biofeedback"],
        })
        item = repo("owner/pomodoro", 3, description="A productivity utility")
        item["readme"] = "# Utility\nA configurable focus timer with biofeedback for deep work."
        item["readme_sha"] = "abc123"
        item["evidence"] = []

        annotate_candidate_mechanisms(item, request)

        self.assertEqual(
            [value["name"] for value in item["mechanisms"]],
            ["pomodoro", "biofeedback"],
        )
        fact = next(value for value in item["evidence"] if value["kind"] == "mechanism_match")
        match = fact["facts"]["mechanisms"][0]
        self.assertEqual(match["source_field"], "readme")
        self.assertEqual(match["sha"], "abc123")
        self.assertEqual(len(fact["facts"]["mechanisms"]), 2)
        public = public_candidate(item)
        public_ids = {value["id"] for value in public["evidence"]}
        self.assertTrue(all(
            set(mechanism["evidence_ids"]) <= public_ids
            for mechanism in public["mechanisms"]
        ))
        self.assertTrue(any(value["kind"] == "mechanism_match" for value in public["evidence"]))
        self.assertTrue(all("evidence" not in mechanism for mechanism in public["mechanisms"]))
        annotate_candidate_mechanisms(item, request)
        self.assertEqual(
            sum(value["kind"] == "mechanism_match" for value in item["evidence"]), 1
        )

        name_only = repo("owner/pomodoro", 3, description="A productivity utility")
        name_only["evidence"] = []
        annotate_candidate_mechanisms(name_only, request)
        self.assertEqual(name_only["mechanisms"], [])

    def test_boundary_deduplicates_mechanisms_and_separates_recalled_from_presented(self):
        request = SearchRequest.from_dict({
            "request": "focus", "problem_concepts": ["focus"],
            "mechanisms": ["pomodoro"],
            "exploration_directions": ["biofeedback"],
        })
        first = repo("one/timer", 3, description="Pomodoro timer", topics=["digital-wellbeing"])
        second = repo("two/timer", 4, description="Pomodoro workflow")
        third = repo("three/sensor", 4, description="Biofeedback for focus")
        for item in (first, second, third):
            item["evidence"] = []
            annotate_candidate_mechanisms(item, request)
        first["evidence"].insert(0, {
            "id": "repo:one/timer:metadata",
            "kind": "github_metadata",
            "facts": {"topics": ["digital-wellbeing"]},
        })

        boundary = build_boundary([first, second, third], [first], request)

        self.assertEqual(boundary.recalled_mechanisms, ["biofeedback", "pomodoro"])
        self.assertEqual(boundary.presented_mechanisms, ["pomodoro"])
        self.assertEqual(boundary.mechanism_origins, {
            "requested_mechanisms": ["pomodoro"],
            "confirmed_exploration_directions": ["biofeedback"],
        })
        self.assertEqual(boundary.explored_directions, ["biofeedback"])
        self.assertEqual(boundary.unexplored_directions, [])
        self.assertEqual(boundary.discovered_terms, ["digital wellbeing"])
        self.assertEqual(
            boundary.discovered_term_evidence[0]["sources"][0]["evidence_id"],
            "repo:one/timer:metadata",
        )
        self.assertNotIn("digital wellbeing", boundary.recalled_mechanisms)
        rejected = build_boundary(
            [first, second, third], [first], request,
            rejected_directions=["biofeedback"],
        )
        self.assertEqual(rejected.rejected_directions, ["biofeedback"])
        self.assertNotIn("biofeedback", rejected.explored_directions)

    def test_search_and_expand_save_snapshots_and_compute_delta(self):
        pomodoro = repo("focus/timer", 4, description="Focus helper")
        tracker = repo("focus/tracker", 3, description="Focus helper")
        github = FrozenGitHub(
            [("usage logger", [tracker]), ("focus", [pomodoro]), ("pomodoro", [pomodoro])],
            readmes={
                "focus/timer": "# Timer\nA Pomodoro workflow.\n## Usage\nRun it.",
                "focus/tracker": "# Tracker\nUsage tracking for digital wellbeing.\n## Usage\nRun it.",
            },
        )
        request = SearchRequest.from_dict({
            "request": "focus", "problem_concepts": ["focus"],
            "mechanisms": ["pomodoro"],
            "exploration_directions": ["usage tracking", "biofeedback"],
        })

        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                engine = SearchEngine(store, github, relation_budget=0)
                first = engine.search(request, "deep")
                expanded = engine.expand(first["search_id"], {"concepts": ["usage logger"]})
                stored = store.get_candidate("focus/timer", first["search_id"])
                excerpt = next(
                    item["id"] for item in stored["evidence"]
                    if item["kind"] == "readme_excerpt"
                )
                ranked = rank_search(store, first["search_id"], [{
                    "repo": "focus/timer", "relevance": 90, "uniqueness": 75,
                    "usability": 80, "difficulty": "easy", "use_case": "Pomodoro workflow",
                    "category": "focus", "artifact_type": "application",
                    "reasons": [{"text": "Documented workflow", "evidence_ids": [excerpt]}],
                    "risks": [{
                        "text": "Check project metadata",
                        "evidence_ids": ["repo:focus/timer:metadata"],
                    }],
                }])
                snapshots = store.boundary_snapshots(first["search_id"])
            finally:
                store.close()

        self.assertEqual([item["stage"] for item in snapshots], ["search", "expand", "rank"])
        self.assertEqual(first["boundary"]["recalled_mechanisms"], ["pomodoro"])
        self.assertIn("usage tracking", expanded["boundary_delta"]["new_mechanisms"])
        self.assertEqual(expanded["boundary"]["unexplored_directions"], ["biofeedback"])
        self.assertIn("pomodoro", ranked["boundary"]["presented_mechanisms"])

    def test_cassette_replay_reproduces_the_same_boundary(self):
        class Delegate:
            rate_limits = {}

            def search_repositories(self, query, per_page=10, sort="stars"):
                return github_module.ApiResult({"items": [repo(
                    "focus/timer", 4, description="Pomodoro focus timer",
                    topics=["digital-wellbeing"],
                )]})

            def readme(self, full_name):
                return github_module.ApiResult("# Timer\nA Pomodoro focus timer.\n## Usage\nRun it.")

            def latest_release(self, full_name):
                raise github_module.GitHubNotFoundError("missing")

        request = SearchRequest.from_dict({
            "request": "focus", "problem_concepts": ["focus"],
            "mechanisms": ["pomodoro"],
            "exploration_directions": ["biofeedback"],
        })
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cassette = root / "boundary.json.gz"
            record_store = Store(root / "record")
            try:
                recorder = CassetteGitHub(github_module, cassette, delegate=Delegate())
                recorded = SearchEngine(record_store, recorder).search(request, "quick")
                recorder.save()
            finally:
                record_store.close()
            replay_store = Store(root / "replay")
            try:
                replay = CassetteGitHub(github_module, cassette, delegate=None)
                replayed = SearchEngine(replay_store, replay).search(request, "quick")
            finally:
                replay_store.close()

        self.assertEqual(replayed["boundary"], recorded["boundary"])
        self.assertEqual(replayed["boundary_delta"], recorded["boundary_delta"])


if __name__ == "__main__":
    unittest.main()
