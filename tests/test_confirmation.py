import tempfile
import unittest

from muse_shroom.boundary import build_boundary
from muse_shroom.confirmation import (
    confirmation_metrics,
    evaluate_confirmation,
    pending_confirmation_candidates,
)
from muse_shroom.iteration import session_loop_diagnostics
from muse_shroom.models import SearchRequest
from muse_shroom.queries import confirmation_queries, query_fingerprint
from muse_shroom.search import SearchEngine
from muse_shroom.storage import Store

from tests.helpers import FrozenGitHub, repo


def source(repo_name: str, *, stage: str = "discovery", anchored: bool = True,
           mechanism_anchored: bool = True, score: int = 70) -> dict:
    return {
        "repo": repo_name,
        "source_field": "readme_features",
        "evidence_id": f"repo:{repo_name}:readme:features",
        "evidence_text": "Decision log supports meeting efficiency.",
        "core_use_case": True,
        "request_anchored": anchored,
        "mechanism_anchored": mechanism_anchored,
        "evidence_relevance_score": score,
        "retrieval_stage": stage,
    }


class ConfirmationTests(unittest.TestCase):
    def test_medium_candidate_enters_queue_while_high_candidate_direct_promotes(self):
        request = SearchRequest.from_dict({
            "request": "improve meeting efficiency",
            "problem_concepts": ["meeting efficiency"],
        })
        high = repo(
            "meeting/high", 10,
            description="Decision monitoring for meeting efficiency",
        )
        high["evidence"] = [{
            "id": "repo:meeting/high:metadata", "kind": "github_metadata", "facts": {},
        }]
        medium = repo("meeting/medium", 5, description="Meeting efficiency helper")
        medium["evidence"] = [
            {"id": "repo:meeting/medium:metadata", "kind": "github_metadata", "facts": {}},
            {"id": "repo:meeting/medium:readme:features", "kind": "readme_excerpt", "facts": {
                "snippet_type": "features",
                "text": "A decision log records outcomes near the meeting workflow.",
            }},
        ]

        boundary = build_boundary([high, medium], [], request).to_dict()
        by_term = {
            item["term"]: item for item in boundary["discovered_term_evidence"]
        }

        self.assertEqual(by_term["decision monitoring"]["disposition"], "direct_promote")
        self.assertTrue(by_term["decision monitoring"]["promotable"])
        self.assertEqual(by_term["decision log"]["disposition"], "needs_confirmation")
        self.assertFalse(by_term["decision log"]["promotable"])
        self.assertIn(
            "decision log", {item["candidate"] for item in boundary["confirmation_queue"]}
        )

    def test_explicit_incidental_evidence_is_rejected_before_confirmation(self):
        request = SearchRequest.from_dict({
            "request": "improve meeting efficiency",
            "problem_concepts": ["meeting efficiency"],
        })
        candidate = repo("meeting/changelog", 5, description="Meeting efficiency helper")
        candidate["evidence"] = [
            {"id": "repo:meeting/changelog:metadata", "kind": "github_metadata", "facts": {}},
            {"id": "repo:meeting/changelog:readme:features", "kind": "readme_excerpt", "facts": {
                "snippet_type": "features",
                "text": "Changelog v2.1: add browser automation dependency.",
            }},
        ]

        boundary = build_boundary([candidate], [], request).to_dict()
        item = next(
            value for value in boundary["discovered_term_evidence"]
            if value["term"] == "browser automation"
        )

        self.assertEqual(item["disposition"], "reject")
        self.assertNotIn(
            "browser automation",
            {value["candidate"] for value in boundary["confirmation_queue"]},
        )

    def test_confirmation_requires_new_independent_core_use_case_evidence(self):
        queue = {
            "candidate": "decision log",
            "discovery_evidence": [source("one/meeting")],
        }
        refreshed = {
            "evidence_relevance_score": 82,
            "sources": [
                source("one/meeting"),
                source("two/meeting", stage="confirmation", score=82),
            ],
        }

        record = evaluate_confirmation(
            queue, refreshed, [{"query": '"decision log" "meeting efficiency"'}]
        )

        self.assertEqual(record["confirmation_status"], "confirmed")
        self.assertEqual(record["confirmation_reason"], "new_independent_core_use_case")
        self.assertEqual(record["discovery_evidence"], queue["discovery_evidence"])
        self.assertEqual(len(record["confirmation_evidence"]), 1)

    def test_same_repo_repetition_does_not_confirm(self):
        queue = {
            "candidate": "decision log",
            "discovery_evidence": [source("one/meeting")],
        }
        refreshed = {
            "evidence_relevance_score": 90,
            "sources": [source("one/meeting", stage="confirmation", score=90)],
        }

        record = evaluate_confirmation(
            queue, refreshed, [{"query": '"decision log" "meeting efficiency"'}]
        )

        self.assertEqual(record["confirmation_status"], "rejected")
        self.assertEqual(record["confirmation_reason"], "same_repo_repetition")

    def test_two_independent_discovery_repos_can_confirm_after_recheck(self):
        discovery = [
            source("one/meeting", anchored=False),
            source("two/meeting", anchored=False, mechanism_anchored=False),
        ]
        queue = {
            "candidate": "decision log",
            "discovery_evidence": discovery,
        }
        refreshed = {
            "evidence_relevance_score": 76,
            "sources": discovery,
        }

        record = evaluate_confirmation(
            queue, refreshed, [{"query": '"decision log" "meeting efficiency"'}]
        )

        self.assertEqual(record["confirmation_status"], "confirmed")
        self.assertEqual(record["confirmation_reason"], "multi_repo_independent_support")
        self.assertEqual(record["confirmation_evidence"], [])

    def test_cross_domain_confirmation_cannot_borrow_request_relevance(self):
        queue = {
            "candidate": "identity verification",
            "discovery_evidence": [
                source(
                    "one/photos", anchored=True, mechanism_anchored=False,
                ),
            ],
        }
        refreshed = {
            "evidence_relevance_score": 90,
            "sources": [
                *queue["discovery_evidence"],
                source(
                    "two/attendance", stage="confirmation", anchored=False,
                    mechanism_anchored=True,
                ),
            ],
        }

        record = evaluate_confirmation(
            queue, refreshed,
            [{"query": '"identity verification" "photo organization"'}],
        )

        self.assertEqual(record["confirmation_status"], "rejected")
        self.assertEqual(record["confirmation_reason"], "no_independent_core_use_case_evidence")

    def test_hyphen_modifier_fragment_is_not_queued(self):
        request = SearchRequest.from_dict({
            "request": "preserve family files",
            "problem_concepts": ["family digital preservation"],
            "mechanisms": ["cloud backup"],
        })
        candidate = repo(
            "backup/dedup", 8,
            description="Cloud backup for family digital preservation",
        )
        candidate["evidence"] = [
            {"id": "repo:backup/dedup:metadata", "kind": "github_metadata", "facts": {}},
            {"id": "repo:backup/dedup:readme:features", "kind": "readme_excerpt", "facts": {
                "snippet_type": "features",
                "text": "Lock-Free Deduplication makes cloud backup efficient.",
            }},
        ]

        boundary = build_boundary([candidate], [], request).to_dict()

        self.assertNotIn(
            "free deduplication",
            {item["term"] for item in boundary["discovered_term_evidence"]},
        )

    def test_request_known_mechanism_is_not_queued_as_new(self):
        request = SearchRequest.from_dict({
            "request": "improve meeting efficiency",
            "problem_concepts": ["meeting efficiency"],
            "mechanisms": ["decision log"],
        })
        candidate = repo(
            "meeting/log", 5,
            description="Decision log for meeting efficiency",
        )
        candidate["evidence"] = [{
            "id": "repo:meeting/log:metadata", "kind": "github_metadata", "facts": {},
        }]

        boundary = build_boundary([candidate], [], request).to_dict()

        self.assertNotIn("decision log", boundary["discovered_terms"])
        self.assertEqual(boundary["confirmation_queue"], [])

    def test_confirmation_queries_always_combine_candidate_and_context(self):
        request = SearchRequest.from_dict({
            "request": "improve meeting efficiency",
            "problem_concepts": ["meeting efficiency"],
        })

        queries, skipped = confirmation_queries(
            "decision log", request,
            anchors=["agenda template"], seed_repos=["one/meeting"], limit=3,
        )

        self.assertEqual(skipped, [])
        self.assertGreaterEqual(len(queries), 2)
        self.assertTrue(all("decision log" in item["query"] for item in queries))
        self.assertTrue(all(item["kind"].startswith("confirmation_") for item in queries))

    def test_confirmation_metrics_do_not_inflate_iteration_query_metrics(self):
        request = SearchRequest.from_dict({
            "request": "improve meeting efficiency",
            "problem_concepts": ["meeting efficiency"],
        })
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                store.create_search("confirmation-metrics", request.to_dict(), "deep")
                normal = '"meeting efficiency" in:readme'
                confirmation = '"decision log" "meeting efficiency" in:readme'
                store.add_query_history(
                    "confirmation-metrics", normal, "refinement", 2,
                    iteration=1, fingerprint=query_fingerprint(normal), skipped=False,
                )
                store.add_query_history(
                    "confirmation-metrics", confirmation, "confirmation_problem", 1,
                    iteration=1, fingerprint=query_fingerprint(confirmation), skipped=False,
                )
                state = store.get_session_state("confirmation-metrics")
                state["confirmation_records"] = [{
                    "candidate": "decision log",
                    "confirmation_queries": [confirmation],
                    "confirmation_status": "confirmed",
                }]
                store.save_session_state("confirmation-metrics", state)
                diagnostics = session_loop_diagnostics(store, "confirmation-metrics")
            finally:
                store.close()

        self.assertEqual(diagnostics["queries_per_iteration"], [0, 1])
        self.assertEqual(diagnostics["confirmation_query_count"], 1)
        self.assertEqual(diagnostics["confirmation_confirmed_count"], 1)
        self.assertEqual(confirmation_metrics(state["confirmation_records"])["confirmation_executed_count"], 1)

    def test_confirmed_candidate_is_promoted_with_separate_trace(self):
        discovery = repo(
            "one/meeting", 20,
            description="Meeting efficiency helper",
        )
        confirmation = repo(
            "two/meeting", 10,
            description="Decision log for meeting efficiency",
        )
        github = FrozenGitHub(
            [
                ("decision log", [confirmation]),
                ("meeting followup", []),
                ("meeting efficiency", [discovery]),
            ],
            readmes={
                "one/meeting": (
                    "# Meetings\n## Features\nA decision log records outcomes near each meeting."
                ),
                "two/meeting": (
                    "# Decisions\n## Features\nA decision log improves meeting efficiency by recording outcomes."
                ),
            },
        )
        request = SearchRequest.from_dict({
            "request": "improve meeting efficiency",
            "problem_concepts": ["meeting efficiency"],
        })

        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                engine = SearchEngine(store, github, relation_budget=0)
                first = engine.search(request, "deep")
                result = engine.iterate(first["search_id"], {
                    "decision": "continue",
                    "concepts": ["meeting followup"],
                    "strategies": ["keyword"],
                    "reason": "exercise the confirmation sub-stage",
                })
                diagnostics = session_loop_diagnostics(store, first["search_id"])
            finally:
                store.close()

        records = result["boundary"]["mechanism_confirmations"]
        decision = next(item for item in records if item["candidate"] == "decision log")
        self.assertEqual(decision["confirmation_status"], "confirmed")
        self.assertTrue(decision["discovery_evidence"])
        self.assertTrue(decision["confirmation_evidence"])
        self.assertIn("decision log", result["boundary"]["recalled_mechanisms"])
        self.assertIn("decision log", result["boundary_delta"]["new_mechanisms"])
        self.assertEqual(diagnostics["executed_iteration_count"], 1)
        self.assertGreater(diagnostics["confirmation_query_count"], 0)

    def test_queue_selection_deduplicates_surface_synonyms(self):
        boundary = {"confirmation_queue": [
            {"candidate": "pomodoro technique", "evidence_relevance_score": 60,
             "support_count": 1, "mechanism_specificity": "mechanism"},
            {"candidate": "pomodoro workflow technique", "evidence_relevance_score": 55,
             "support_count": 1, "mechanism_specificity": "mechanism"},
        ]}

        selected = pending_confirmation_candidates(boundary, limit=3)

        self.assertEqual(len(selected), 1)


if __name__ == "__main__":
    unittest.main()
