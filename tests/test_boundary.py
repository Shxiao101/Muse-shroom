import tempfile
import unittest
from pathlib import Path

from evaluation.cassette import CassetteGitHub
from muse_shroom import github as github_module
from muse_shroom.boundary import (
    annotate_candidate_mechanisms, boundary_delta, build_boundary,
    normalize_mechanism_surfaces,
)
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

    def test_discovered_directions_use_curated_structured_sources_without_mechanism_gate(self):
        request = SearchRequest.from_dict({
            "request": "focus", "problem_concepts": ["focus"],
        })
        description = repo(
            "one/friction", 4,
            description="Adds behavioral friction before distracting actions",
        )
        description["evidence"] = [{
            "id": "repo:one/friction:metadata", "kind": "github_metadata", "facts": {},
        }]
        readme = repo("two/sensor", 3, description="Focus companion")
        readme["evidence"] = [
            {"id": "repo:two/sensor:metadata", "kind": "github_metadata", "facts": {}},
            {
                "id": "repo:two/sensor:readme:features", "kind": "readme_excerpt",
                "facts": {"snippet_type": "features", "text": "Biofeedback guides each focus session."},
            },
        ]
        related = repo("three/minimal", 2, description="Attention helper")
        related["discovery_paths"] = [{
            "kind": "relationship", "from": "seed/tool", "relation": "readme_link",
            "detail": "digital minimalism companion",
        }]
        related["evidence"] = [
            {"id": "repo:three/minimal:metadata", "kind": "github_metadata", "facts": {}},
            {
                "id": "relation:seed/tool:readme_link:three/minimal",
                "kind": "discovery_relation", "facts": {"detail": "digital minimalism companion"},
            },
        ]

        boundary = build_boundary([description, readme, related], [], request)
        by_term = {item["term"]: item for item in boundary.discovered_term_evidence}

        self.assertIn("behavioral friction", by_term)
        self.assertIn("biofeedback", by_term)
        self.assertIn("digital minimalism", by_term)
        self.assertEqual(by_term["behavioral friction"]["sources"][0]["source_field"], "description")
        self.assertEqual(by_term["biofeedback"]["sources"][0]["source_field"], "readme_features")
        self.assertEqual(by_term["digital minimalism"]["kind"], "project_category")
        self.assertFalse(by_term["digital minimalism"]["promotable"])
        self.assertEqual(by_term["biofeedback"]["promotion_confidence"], "high")
        self.assertTrue(by_term["biofeedback"]["promotable"])
        self.assertGreater(by_term["biofeedback"]["confidence"], 0.8)

    def test_discovered_directions_filter_generic_and_explicitly_excluded_topics(self):
        request = SearchRequest.from_dict({
            "request": "focus", "problem_concepts": ["focus"],
            "exclusions": ["awesome list"],
        })
        candidate = repo(
            "one/focus", 4, description="Adaptive pacing for focus",
            topics=["agent", "llm", "awesome-list", "adaptive-pacing"],
        )
        candidate["evidence"] = [{
            "id": "repo:one/focus:metadata", "kind": "github_metadata", "facts": {},
        }]
        boundary = build_boundary([candidate], [], request)
        terms = {item["term"] for item in boundary.discovered_term_evidence}
        self.assertEqual(terms, {"adaptive pacing"})

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
                expanded = engine.iterate(first["search_id"], {
                    "decision": "continue",
                    "reason": "test the still-unconfirmed usage direction",
                    "target_direction": "usage tracking",
                    "concepts": ["usage logger"],
                })
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

        self.assertEqual([item["stage"] for item in snapshots], ["search", "iterate", "rank"])
        self.assertEqual(first["boundary"]["recalled_mechanisms"], ["pomodoro"])
        self.assertNotIn("usage tracking", expanded["boundary_delta"]["new_mechanisms"])
        self.assertIn("usage tracking", expanded["boundary_delta"]["new_directions"])
        self.assertEqual(expanded["boundary"]["unexplored_directions"], ["biofeedback"])
        self.assertIn("pomodoro", ranked["boundary"]["presented_mechanisms"])

    def test_lexical_direction_match_is_not_exploration_confirmation(self):
        candidate = repo("focus/timer", 4, description="Pomodoro attention workflow")
        github = FrozenGitHub(
            [("focus", [candidate]), ("attention workflow", [candidate])],
            readmes={"focus/timer": "# Timer\nAttention workflow for focused work."},
        )
        request = SearchRequest.from_dict({
            "request": "focus", "problem_concepts": ["focus"],
            "exploration_directions": ["attention workflow"],
        })
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                output = SearchEngine(store, github, relation_budget=0).search(request, "deep")
            finally:
                store.close()

        self.assertEqual(output["boundary"]["explored_directions"], [])
        self.assertEqual(output["boundary"]["unexplored_directions"], ["attention workflow"])

    def test_discovery_avoids_sentence_fragments_and_governance_boilerplate(self):
        request = SearchRequest.from_dict({
            "request": "organize photos", "problem_concepts": ["photo organization"],
        })
        candidate = repo("photos/tool", 4, description="Photo organizer")
        candidate["evidence"] = [
            {"id": "repo:photos/tool:metadata", "kind": "github_metadata", "facts": {}},
            {"id": "repo:photos/tool:readme:features", "kind": "readme_excerpt", "facts": {
                "snippet_type": "features",
                "text": "Jupyter-based. Instant feedback. Duplicate detection uses perceptual hashing.",
            }},
            {"id": "repo:photos/tool:readme:philosophy", "kind": "readme_excerpt", "facts": {
                "snippet_type": "philosophy", "text": "Accountability and monitoring principles.",
            }},
        ]

        boundary = build_boundary([candidate], [], request)
        terms = {item["term"] for item in boundary.discovered_term_evidence}

        self.assertIn("instant feedback", terms)
        self.assertIn("perceptual hashing", terms)
        self.assertNotIn("based instant feedback", terms)
        self.assertNotIn("and monitoring", terms)
        self.assertNotIn("accountability", terms)
        hashing = next(
            item for item in boundary.discovered_term_evidence
            if item["term"] == "perceptual hashing"
        )
        self.assertIn("perceptual hashing", hashing["sources"][0]["evidence_text"].casefold())

    def test_discovery_prefers_mechanism_compounds_and_rejects_generic_fragments(self):
        request = SearchRequest.from_dict({
            "request": "make meetings useful", "problem_concepts": ["meeting efficiency"],
            "mechanisms": ["agenda template"],
        })
        candidate = repo("meetings/tool", 4, description="Meeting efficiency helper")
        candidate["selected_for_assessment"] = True
        candidate["discovery_paths"] = [{
            "kind": "query", "term": "agenda template", "query": "agenda template",
        }]
        candidate["evidence"] = [
            {"id": "repo:meetings/tool:metadata", "kind": "github_metadata", "facts": {}},
            {"id": "repo:meetings/tool:readme:concept_match", "kind": "readme_excerpt", "facts": {
                "snippet_type": "concept_match",
                "text": (
                    "assets/decision_log_template.md - Bilingual decision log template. "
                    "Every contribution includes a full description and an ultimate solution."
                ),
            }},
        ]

        boundary = build_boundary([candidate], [], request)
        by_term = {item["term"]: item for item in boundary.discovered_term_evidence}

        self.assertIn("decision log", by_term)
        self.assertEqual(by_term["decision log"]["kind"], "project_category")
        self.assertEqual(by_term["decision log"]["disposition"], "needs_confirmation")
        self.assertNotIn("bilingual decision", by_term)
        self.assertNotIn("each contribution", by_term)
        self.assertNotIn("full description", by_term)
        self.assertNotIn("ultimate solution", by_term)

    def test_discovery_does_not_transfer_relevance_across_use_case_boundaries(self):
        request = SearchRequest.from_dict({
            "request": "organize photos",
            "problem_concepts": ["personal photo organization"],
            "mechanisms": ["folder organization"],
        })
        candidate = repo("photos/faces", 4, description="Face recognition examples")
        candidate["selected_for_assessment"] = True
        candidate["evidence"] = [
            {"id": "repo:photos/faces:metadata", "kind": "github_metadata", "facts": {}},
            {"id": "repo:photos/faces:readme:concept_match", "kind": "readme_excerpt", "facts": {
                "snippet_type": "concept_match",
                "text": (
                    "Personal photo organization. "
                    "Educational platforms: student attendance tracking."
                ),
            }},
        ]

        boundary = build_boundary([candidate], [], request)
        by_term = {item["term"]: item for item in boundary.discovered_term_evidence}

        self.assertEqual(by_term["attendance tracking"]["kind"], "project_category")

    def test_evidence_relevance_and_specificity_gate_keep_low_quality_terms_traceable(self):
        request = SearchRequest.from_dict({
            "request": "reduce coding agent overthinking",
            "problem_concepts": ["coding agent overthinking"],
            "mechanisms": ["reasoning budget", "stop condition"],
            "exploration_directions": ["agent workflow"],
        })
        unrelated = repo(
            "browser/tool", 4,
            description="Token-efficient browser automation for AI agents",
            topics=["browser-automation"],
        )
        unrelated["selected_for_assessment"] = True
        unrelated["evidence"] = [{
            "id": "repo:browser/tool:metadata", "kind": "github_metadata", "facts": {},
        }]
        relevant = repo(
            "agents/guard", 4,
            description=(
                "A stop-condition guard for coding agent overthinking with "
                "decision monitoring"
            ),
        )
        relevant["selected_for_assessment"] = True
        relevant["evidence"] = [{
            "id": "repo:agents/guard:metadata", "kind": "github_metadata", "facts": {},
        }]

        boundary = build_boundary([unrelated, relevant], [], request)
        by_term = {item["term"]: item for item in boundary.discovered_term_evidence}

        self.assertIn("browser automation", by_term)
        self.assertFalse(by_term["browser automation"]["promotable"])
        self.assertEqual(by_term["browser automation"]["promotion_confidence"], "low")
        self.assertLess(by_term["browser automation"]["evidence_relevance_score"], 50)
        self.assertIn("decision monitoring", by_term)
        self.assertTrue(by_term["decision monitoring"]["promotable"])
        self.assertEqual(by_term["decision monitoring"]["mechanism_specificity"], "behavioral_signal")
        self.assertIn("local_problem", by_term["decision monitoring"]["evidence_relevance_reason"])

    def test_umbrella_category_is_not_promotable_even_when_request_relevant(self):
        request = SearchRequest.from_dict({
            "request": "keep long projects motivating",
            "problem_concepts": ["long project motivation"],
            "mechanisms": ["progress chart"],
        })
        candidate = repo(
            "project/dashboard", 4,
            description="Data visualization and progress chart for long project motivation",
        )
        candidate["selected_for_assessment"] = True
        candidate["evidence"] = [{
            "id": "repo:project/dashboard:metadata", "kind": "github_metadata", "facts": {},
        }]

        boundary = build_boundary([candidate], [], request)
        item = next(
            value for value in boundary.discovered_term_evidence
            if value["term"] == "data visualization"
        )

        self.assertEqual(item["mechanism_specificity"], "domain_category")
        self.assertFalse(item["promotable"])
        self.assertEqual(item["promotion_confidence"], "low")

    def test_token_equivalent_mechanisms_do_not_create_boundary_gain(self):
        previous = {
            "recalled_mechanisms": ["browser automation"],
            "presented_mechanisms": [],
        }
        current = {
            "recalled_mechanisms": ["browser automation", "web automation"],
            "presented_mechanisms": [],
        }

        delta = boundary_delta(current, previous).to_dict()

        self.assertEqual(delta["new_mechanisms"], [])
        self.assertIn({
            "surface_term": "web automation",
            "canonical_term": "browser automation",
            "normalization_reason": "token_equivalence",
        }, delta["mechanism_normalizations"])

    def test_mechanism_normalization_preserves_surfaces_without_merging_distinct_terms(self):
        evidence = [
            {"term": "recognition beat tracking", "sources": [{"repo": "music/tool"}]},
            {"term": "beat tracking", "sources": [{"repo": "music/tool"}]},
        ]
        canonical, mappings = normalize_mechanism_surfaces(
            ["based instant feedback", "instant feedback", "recognition beat tracking",
             "beat tracking", "usage tracking"],
            evidence,
        )

        self.assertEqual(
            canonical,
            ["beat tracking", "instant feedback", "usage tracking"],
        )
        self.assertIn({
            "surface_term": "based instant feedback",
            "canonical_term": "instant feedback",
            "normalization_reason": "fragment_prefix",
        }, mappings)
        self.assertIn({
            "surface_term": "recognition beat tracking",
            "canonical_term": "beat tracking",
            "normalization_reason": "shared_evidence_containment",
        }, mappings)

    def test_boundary_delta_uses_canonical_gain_and_retains_surface_trace(self):
        evidence = [
            {"term": "recognition beat tracking", "sources": [{"repo": "music/tool"}]},
            {"term": "beat tracking", "sources": [{"repo": "music/tool"}]},
        ]
        previous = {
            "recalled_mechanisms": ["beat tracking"],
            "presented_mechanisms": [],
            "discovered_term_evidence": evidence,
        }
        current = {
            "recalled_mechanisms": ["beat tracking", "recognition beat tracking"],
            "presented_mechanisms": [],
            "discovered_term_evidence": evidence,
        }

        delta = boundary_delta(current, previous).to_dict()

        self.assertEqual(delta["new_mechanisms"], [])
        self.assertEqual(delta["new_mechanism_surfaces"], ["recognition beat tracking"])
        self.assertEqual(delta["mechanism_normalizations"], [{
            "surface_term": "recognition beat tracking",
            "canonical_term": "beat tracking",
            "normalization_reason": "shared_evidence_containment",
        }])

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
