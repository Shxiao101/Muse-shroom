import json
import unittest
from pathlib import Path

from evaluation.boundary_eval import (
    FORMAL_METRICS, load_golden, load_labels, summarize, summarize_suites,
)
from evaluation.check_boundary_leakage import find_leaks


ROOT = Path(__file__).resolve().parents[1]


class BoundaryEvaluationTests(unittest.TestCase):
    def test_golden_cases_define_mechanism_spaces_not_repositories(self):
        payload = json.loads(
            (ROOT / "evaluation" / "boundary-golden-cases.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(len(payload["cases"]), 8)
        for case in payload["cases"]:
            self.assertTrue(case["mainstream_mechanisms"])
            self.assertTrue(case["acceptable_new_mechanisms"])
            self.assertTrue(case["repetition_groups"])
            self.assertTrue(case["cross_mechanism_directions"])
            self.assertNotIn("repos", case)
            for field in (
                "mainstream_mechanisms", "acceptable_new_mechanisms",
                "repetition_groups", "cross_mechanism_directions",
            ):
                self.assertTrue(all(item.get("id") and item.get("term") for item in case[field]))
        prompt_payload = json.loads(
            (ROOT / "evaluation" / "boundary-prompts.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {case["id"] for case in payload["cases"]},
            {prompt["id"] for prompt in prompt_payload["prompts"]},
        )

    def test_agentic_summary_enforces_trace_and_non_pagination(self):
        payload = {
            "policy": "host_in_loop",
            "results": [{
                "prompt_id": "focus",
                "boundary_diagnostics": {
                    "mechanism_redundancy": 0.2,
                    "direction_coverage": 1.0,
                    "presented_mechanism_count": 4,
                },
                "loop_diagnostics": {
                    "iterations_used": 1,
                    "new_mechanisms_per_iteration": [2],
                    "boundary_gain_per_iteration": [2],
                    "duplicate_query_rate": 0.0,
                    "unexplored_directions_at_stop": [],
                    "boundary_trace": [
                        {
                            "stage": "search", "queries": ["focus"],
                            "mechanisms_found": ["focus timer", "website blocker"],
                        },
                        {
                            "stage": "iterate",
                            "queries": ["digital wellbeing"],
                            "hypothesis": {
                                "promote_discovered_terms": ["digital wellbeing"]
                            },
                            "evidence_sources": [{"term": "digital wellbeing"}],
                            "new_mechanisms": ["biofeedback"],
                        },
                    ],
                },
                "recalled_candidates": [{
                    "mechanisms": [{"name": "focus timer"}, {"name": "biofeedback"}],
                }],
            }]
        }
        result = summarize(payload)
        self.assertEqual(set(result["formal_metrics"]), set(FORMAL_METRICS))
        self.assertEqual(result["verdict"], "pass")
        self.assertTrue(result["cases"][0]["queries_changed_after_initial"])
        self.assertTrue(result["cases"][0]["evidence_backed_promotions"])
        self.assertGreater(result["cases"][0]["meaningful_boundary_gain"], 0)
        self.assertTrue(result["cases"][0]["cross_mechanism_discovery"])

    def test_same_mechanism_rewording_is_not_meaningful_gain(self):
        payload = {"results": [{
            "prompt_id": "focus",
            "boundary_diagnostics": {"presented_mechanism_count": 2},
            "loop_diagnostics": {
                "iterations_used": 1,
                "boundary_gain_per_iteration": [1],
                "boundary_trace": [
                    {"stage": "search", "queries": ["focus timer"], "mechanisms_found": ["focus timer"]},
                    {"stage": "iterate", "queries": ["countdown timer"], "new_mechanisms": ["countdown timer"]},
                ],
            },
        }]}
        result = summarize(payload, load_golden())
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["cases"][0]["meaningful_boundary_gain"], 0)

    def test_single_pass_does_not_claim_boundary_success(self):
        result = summarize({"results": [{
            "prompt_id": "focus",
            "boundary_diagnostics": {},
            "loop_diagnostics": {"iterations_used": 0},
        }]})
        self.assertEqual(result["verdict"], "insufficient_data")
        self.assertIsNone(result["passed"])

    def test_retrieval_redundancy_does_not_fail_diverse_presentation(self):
        recalled = [
            {"repo": f"timer/{index}", "mechanisms": [{"name": "focus timer"}]}
            for index in range(6)
        ] + [{"repo": "well/bio", "mechanisms": [{"name": "biofeedback"}]}]
        payload = {"policy": "host_in_loop", "results": [{
            "prompt_id": "focus",
            "ranking": {"items": [{"repo": "timer/0"}, {"repo": "well/bio"}]},
            "candidates": recalled,
            "recalled_candidates": recalled,
            "loop_diagnostics": {
                "iterations_used": 1, "boundary_gain_per_iteration": [1],
                "boundary_trace": [
                    {"stage": "search", "queries": ["focus"], "mechanisms_found": ["focus timer", "task workflow"]},
                    {"stage": "iterate", "queries": ["biofeedback"], "new_mechanisms": ["biofeedback"]},
                ],
            },
        }]}
        result = summarize(payload)
        case = result["cases"][0]
        self.assertEqual(result["verdict"], "pass")
        self.assertGreater(case["retrieval_mechanism_redundancy"], 0.5)
        self.assertEqual(case["repetition_violations"], [])
        self.assertEqual(case["redundancy_scope"], "ranking_items")

    def test_deterministic_policy_reports_discovery_as_not_measured(self):
        payload = {"policy": "deterministic", "results": [{
            "prompt_id": "focus",
            "loop_diagnostics": {
                "iterations_used": 1,
                "boundary_gain_per_iteration": [0],
                "boundary_trace": [
                    {"stage": "search", "queries": ["focus"],
                     "mechanisms_found": ["focus timer", "website blocker"]},
                    {"stage": "iterate", "queries": ["attention management"],
                     "new_mechanisms": []},
                ],
            },
        }]}

        result = summarize(payload)
        case = result["cases"][0]

        self.assertEqual(result["policy"], "deterministic")
        self.assertEqual(result["mechanics_verdict"], "pass")
        self.assertEqual(result["discovery_verdict"], "not_measured")
        self.assertEqual(result["verdict"], "needs_review")
        self.assertTrue(case["boundary_quality_passed"])
        self.assertIsNone(case["discovery_quality_passed"])
        self.assertIsNone(case["cross_mechanism_discovery"])
        self.assertEqual(case["cross_mechanism_status"], "not_measured")

    def test_mechanics_failure_is_not_masked_by_unknown_review(self):
        payload = {"policy": "deterministic", "results": [
            {
                "prompt_id": "focus",
                "loop_diagnostics": {
                    "iterations_used": 1, "boundary_gain_per_iteration": [1],
                    "boundary_trace": [
                        {"stage": "search", "queries": ["focus"],
                         "mechanisms_found": ["focus timer"]},
                        {"stage": "iterate", "queries": ["countdown timer"],
                         "new_mechanisms": ["countdown timer"]},
                    ],
                },
            },
            {
                "prompt_id": "ai-music",
                "loop_diagnostics": {
                    "iterations_used": 1, "boundary_gain_per_iteration": [1],
                    "boundary_trace": [
                        {"stage": "search", "queries": ["music"],
                         "mechanisms_found": ["music generation"]},
                        {"stage": "iterate", "queries": ["quantum kitchen orchestra"],
                         "new_mechanisms": ["quantum kitchen orchestra"]},
                    ],
                },
            },
        ]}

        result = summarize(payload)

        self.assertGreater(result["aggregate"]["unknown_mechanism_review_count"], 0)
        self.assertEqual(result["mechanics_verdict"], "fail")
        self.assertEqual(result["verdict"], "fail")

    def test_repetitive_presentation_fails_even_with_diverse_recall(self):
        candidates = [
            {"repo": f"timer/{index}", "mechanisms": [{"name": "focus timer"}]}
            for index in range(4)
        ] + [
            {"repo": "well/bio", "mechanisms": [{"name": "biofeedback"}]},
            {"repo": "work/tasks", "mechanisms": [{"name": "task workflow"}]},
        ]
        payload = {"policy": "host_in_loop", "results": [{
            "prompt_id": "focus",
            "ranking": {"items": [{"repo": f"timer/{index}"} for index in range(4)]},
            "candidates": candidates, "recalled_candidates": candidates,
            "loop_diagnostics": {
                "iterations_used": 1, "boundary_gain_per_iteration": [1],
                "boundary_trace": [
                    {"stage": "search", "queries": ["focus"], "mechanisms_found": ["focus timer"]},
                    {"stage": "iterate", "queries": ["biofeedback"], "new_mechanisms": ["biofeedback"]},
                ],
            },
        }]}
        result = summarize(payload)
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["cases"][0]["repetition_violations"][0]["scope"], "ranking_items")

    def test_unknown_mechanism_is_reported_for_review(self):
        payload = {"results": [{
            "prompt_id": "focus",
            "loop_diagnostics": {
                "iterations_used": 1, "boundary_gain_per_iteration": [1],
                "boundary_trace": [
                    {"stage": "search", "queries": ["focus"], "mechanisms_found": ["focus timer"]},
                    {
                        "stage": "iterate", "queries": ["neuroadaptive cadence"],
                        "new_mechanisms": ["neuroadaptive cadence"],
                        "evidence_sources": [{
                            "term": "neuroadaptive cadence",
                            "sources": [{"repo": "lab/cadence", "evidence_id": "repo:lab/cadence:metadata"}],
                        }],
                    },
                ],
            },
        }]}
        result = summarize(payload)
        case = result["cases"][0]
        self.assertEqual(result["verdict"], "needs_review")
        self.assertEqual(case["unknown_boundary_gain"], 1)
        self.assertEqual(case["unknown_mechanisms"][0]["repos"], ["lab/cadence"])
        review = result["blind_unknown_review"]
        self.assertEqual(review["items"][0]["mechanism"], "neuroadaptive cadence")
        self.assertIsNone(review["items"][0]["label"])
        self.assertNotIn("acceptable_new_mechanisms", json.dumps(review))

    def test_committed_blind_labels_are_development_only(self):
        labels = load_labels(ROOT / "evaluation" / "blind-review-labels.json")
        self.assertEqual(len(labels), 40)
        self.assertTrue(all(key.startswith("development|") for key in labels))
        self.assertEqual(
            labels["development|learning-habit|spatial computing"], "wrong_domain",
        )

    def test_meaningful_blind_label_is_separate_from_golden_metrics(self):
        payload = {"policy": "host_in_loop", "results": [{
            "prompt_id": "focus",
            "loop_diagnostics": {
                "iterations_used": 1, "boundary_gain_per_iteration": [1],
                "boundary_trace": [
                    {"stage": "search", "queries": ["focus"],
                     "mechanisms_found": ["focus timer", "website blocker"]},
                    {
                        "stage": "iterate", "queries": ["neuroadaptive cadence"],
                        "new_mechanisms": ["neuroadaptive cadence"],
                        "confirmations": [{
                            "candidate": "neuroadaptive cadence",
                            "confirmation_status": "confirmed",
                        }],
                    },
                ],
            },
        }]}
        result = summarize(payload, labels={
            "development|focus|neuroadaptive cadence": "meaningful",
        })
        case = result["cases"][0]
        self.assertEqual(case["meaningful_boundary_gain"], 0)
        self.assertEqual(case["blind_meaningful_gain"], 1)
        self.assertEqual(case["total_meaningful_gain"], 1)
        self.assertEqual(case["unknown_boundary_gain"], 0)
        self.assertEqual(case["blind_meaningful_count"], 1)
        self.assertEqual(case["blind_precision"], 1.0)
        self.assertTrue(case["blind_labels_applied"])
        self.assertEqual(case["confirmed_meaningful_count"], 0)
        self.assertEqual(case["confirmation_precision"], 0.0)
        self.assertEqual(result["aggregate"]["blind_precision"], 1.0)
        self.assertTrue(result["aggregate"]["blind_labels_applied"])
        self.assertEqual(result["aggregate"]["confirmation_precision"], 0.0)

    def test_total_confirmation_precision_combines_golden_and_blind_matches(self):
        payload = {"policy": "host_in_loop", "results": [{
            "prompt_id": "focus",
            "loop_diagnostics": {
                "iterations_used": 1, "boundary_gain_per_iteration": [3],
                "boundary_trace": [
                    {"stage": "search", "queries": ["focus"],
                     "mechanisms_found": ["focus timer", "website blocker"]},
                    {
                        "stage": "iterate",
                        "queries": ["biofeedback", "neuroadaptive cadence", "adaptive lighting"],
                        "new_mechanisms": [
                            "biofeedback", "neuroadaptive cadence", "adaptive lighting",
                        ],
                        "confirmations": [
                            {"candidate": "biofeedback", "confirmation_status": "confirmed"},
                            {"candidate": "neuroadaptive cadence",
                             "confirmation_status": "confirmed"},
                            {"candidate": "adaptive lighting", "confirmation_status": "confirmed"},
                        ],
                    },
                ],
            },
        }]}
        result = summarize(payload, labels={
            "development|focus|neuroadaptive cadence": "meaningful",
            "development|focus|adaptive lighting": "meaningful",
        })
        case = result["cases"][0]

        self.assertEqual(case["confirmation_confirmed_count"], 3)
        self.assertEqual(case["confirmed_meaningful_count"], 1)
        self.assertEqual(case["blind_meaningful_count"], 2)
        self.assertEqual(case["confirmation_precision"], 0.333)
        self.assertEqual(case["blind_precision"], 0.667)
        self.assertEqual(case["confirmation_precision_total"], 1.0)
        self.assertEqual(result["aggregate"]["confirmation_precision"], 0.333)
        self.assertEqual(result["aggregate"]["blind_precision"], 0.667)
        self.assertEqual(result["aggregate"]["confirmation_precision_total"], 1.0)

    def test_total_confirmation_precision_is_null_without_confirmations(self):
        result = summarize({"results": [{
            "prompt_id": "focus",
            "loop_diagnostics": {"iterations_used": 0},
        }]})

        self.assertEqual(result["cases"][0]["confirmation_confirmed_count"], 0)
        self.assertIsNone(result["cases"][0]["confirmation_precision_total"])
        self.assertIsNone(result["aggregate"]["confirmation_precision_total"])

    def test_total_confirmation_precision_uses_golden_matches_on_holdout(self):
        payload = {"policy": "host_in_loop", "results": [{
            "prompt_id": "focus",
            "loop_diagnostics": {
                "iterations_used": 1, "boundary_gain_per_iteration": [1],
                "boundary_trace": [
                    {"stage": "search", "queries": ["focus"],
                     "mechanisms_found": ["focus timer", "website blocker"]},
                    {
                        "stage": "iterate", "queries": ["biofeedback"],
                        "new_mechanisms": ["biofeedback"],
                        "confirmations": [{
                            "candidate": "biofeedback", "confirmation_status": "confirmed",
                        }],
                    },
                ],
            },
        }]}
        result = summarize(payload, suite="holdout")

        self.assertFalse(result["aggregate"]["blind_labels_applied"])
        self.assertIsNone(result["aggregate"]["blind_precision"])
        self.assertEqual(result["cases"][0]["confirmation_precision_total"], 1.0)
        self.assertEqual(result["aggregate"]["confirmation_precision_total"], 1.0)

    def test_noise_blind_label_is_invalid_and_fails_quality(self):
        payload = {"results": [{
            "prompt_id": "focus",
            "loop_diagnostics": {
                "iterations_used": 1, "boundary_gain_per_iteration": [1],
                "boundary_trace": [
                    {"stage": "search", "queries": ["focus"],
                     "mechanisms_found": ["focus timer", "website blocker"]},
                    {"stage": "iterate", "queries": ["neuroadaptive cadence"],
                     "new_mechanisms": ["neuroadaptive cadence"]},
                ],
            },
        }]}
        result = summarize(payload, labels={
            "development|focus|neuroadaptive cadence": "noise",
        })
        case = result["cases"][0]
        self.assertEqual(case["unknown_boundary_gain"], 0)
        self.assertEqual(case["invalid_mechanisms"], ["neuroadaptive cadence"])
        self.assertEqual(case["invalid_boundary_gain"], 1)
        self.assertFalse(case["boundary_quality_passed"])
        self.assertEqual(result["verdict"], "fail")

    def test_partial_blind_labels_do_not_affect_scoring(self):
        payload = {"results": [
            {
                "prompt_id": "focus",
                "loop_diagnostics": {
                    "iterations_used": 1, "boundary_gain_per_iteration": [1],
                    "boundary_trace": [
                        {"stage": "search", "queries": ["focus"],
                         "mechanisms_found": ["focus timer"]},
                        {"stage": "iterate", "queries": ["neuroadaptive cadence"],
                         "new_mechanisms": ["neuroadaptive cadence"]},
                    ],
                },
            },
            {
                "prompt_id": "ai-music",
                "loop_diagnostics": {
                    "iterations_used": 1, "boundary_gain_per_iteration": [1],
                    "boundary_trace": [
                        {"stage": "search", "queries": ["music"],
                         "mechanisms_found": ["music generation"]},
                        {"stage": "iterate", "queries": ["quantum kitchen orchestra"],
                         "new_mechanisms": ["quantum kitchen orchestra"]},
                    ],
                },
            },
        ]}
        baseline = summarize(payload)
        partial = summarize(payload, labels={
            "development|focus|neuroadaptive cadence": "meaningful",
        })
        self.assertEqual(partial["verdict"], baseline["verdict"])
        self.assertEqual(partial["passed"], baseline["passed"])
        for partial_case, baseline_case in zip(partial["cases"], baseline["cases"]):
            for field in (
                "meaningful_boundary_gain", "blind_meaningful_gain",
                "total_meaningful_gain", "unknown_boundary_gain",
                "invalid_boundary_gain", "boundary_quality_passed",
                "blind_meaningful_count", "blind_precision",
            ):
                self.assertEqual(partial_case[field], baseline_case[field])
        self.assertEqual(partial["aggregate"]["blind_meaningful_gain"], 0)
        self.assertIsNone(partial["aggregate"]["blind_precision"])
        self.assertFalse(partial["aggregate"]["blind_labels_applied"])
        self.assertEqual(partial["aggregate"]["unknown_mechanism_review_count"], 2)
        self.assertEqual(partial["aggregate"]["labelled_unknown_count"], 1)
        self.assertEqual(partial["aggregate"]["unlabelled_unknown_count"], 1)
        review_labels = {
            item["mechanism"]: item["label"]
            for item in partial["blind_unknown_review"]["items"]
        }
        self.assertEqual(review_labels["neuroadaptive cadence"], "meaningful")
        self.assertIsNone(review_labels["quantum kitchen orchestra"])

    def test_holdout_ignores_blind_labels(self):
        payload = {"results": [{
            "prompt_id": "focus",
            "loop_diagnostics": {
                "iterations_used": 1, "boundary_gain_per_iteration": [1],
                "boundary_trace": [
                    {"stage": "search", "queries": ["focus"],
                     "mechanisms_found": ["focus timer"]},
                    {"stage": "iterate", "queries": ["neuroadaptive cadence"],
                     "new_mechanisms": ["neuroadaptive cadence"]},
                ],
            },
        }]}
        baseline = summarize(payload, suite="holdout")
        labelled = summarize(payload, suite="holdout", labels={
            "development|focus|neuroadaptive cadence": "meaningful",
        })
        self.assertEqual(labelled, baseline)
        self.assertNotIn("blind_unknown_review", labelled)

    def test_planned_executed_and_retrieval_changing_iterations_are_separate(self):
        payload = {"results": [{
            "prompt_id": "focus",
            "loop_diagnostics": {
                "iterations_used": 2,
                "planned_iteration_count": 2,
                "executed_iteration_count": 1,
                "retrieval_changing_iteration_count": 1,
                "boundary_gain_per_iteration": [0, 1],
                "boundary_trace": [
                    {"stage": "search", "queries": ["focus"],
                     "mechanisms_found": ["focus timer"]},
                    {"stage": "iterate", "queries": [], "new_mechanisms": []},
                    {"stage": "iterate", "queries": ["biofeedback"],
                     "new_mechanisms": ["biofeedback"]},
                ],
            },
        }]}

        result = summarize(payload)
        case = result["cases"][0]
        self.assertEqual(case["planned_iteration_count"], 2)
        self.assertEqual(case["executed_iteration_count"], 1)
        self.assertEqual(case["retrieval_changing_iteration_count"], 1)
        self.assertEqual(result["agentic_case_count"], 1)
        self.assertEqual(result["aggregate"]["duplicate_only_iteration_count"], 1)

    def test_holdout_terms_are_not_in_production_phrase_hints(self):
        self.assertEqual(find_leaks(), [])

    def test_release_verdict_reports_development_and_holdout_and_honors_leakage(self):
        payload = {"policy": "host_in_loop", "results": [{
            "prompt_id": "focus",
            "loop_diagnostics": {
                "iterations_used": 1, "boundary_gain_per_iteration": [1],
                "boundary_trace": [
                    {
                        "stage": "search", "queries": ["focus"],
                        "mechanisms_found": ["focus timer", "website blocker"],
                    },
                    {
                        "stage": "iterate", "queries": ["biofeedback"],
                        "new_mechanisms": ["biofeedback"],
                    },
                ],
            },
        }]}
        golden = load_golden()
        combined = summarize_suites(payload, payload, golden, golden)
        self.assertEqual(combined["verdict"], "pass")
        self.assertEqual(combined["development"]["suite"], "development")
        self.assertEqual(combined["holdout"]["suite"], "holdout")
        leaked = summarize_suites(payload, payload, golden, golden, leakage=True)
        self.assertEqual(leaked["verdict"], "leakage_detected")
        self.assertFalse(leaked["passed"])


if __name__ == "__main__":
    unittest.main()
