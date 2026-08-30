import json
import unittest
from pathlib import Path

from evaluation.boundary_eval import FORMAL_METRICS, summarize


ROOT = Path(__file__).resolve().parents[1]


class BoundaryEvaluationTests(unittest.TestCase):
    def test_golden_cases_define_mechanism_spaces_not_repositories(self):
        payload = json.loads(
            (ROOT / "evaluation" / "boundary-golden-cases.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(len(payload["cases"]), 8)
        for case in payload["cases"]:
            self.assertTrue(case["mainstream_mechanisms"])
            self.assertTrue(case["acceptable_new_mechanism_types"])
            self.assertTrue(case["avoid_repeating"])
            self.assertTrue(case["cross_mechanism_directions"])
            self.assertNotIn("repos", case)

    def test_agentic_summary_enforces_trace_and_non_pagination(self):
        payload = {
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
                        {"stage": "search", "queries": ["focus"]},
                        {
                            "stage": "iterate",
                            "queries": ["digital wellbeing"],
                            "hypothesis": {
                                "promote_discovered_terms": ["digital wellbeing"]
                            },
                            "evidence_sources": [{"term": "digital wellbeing"}],
                        },
                    ],
                },
            }]
        }
        result = summarize(payload)
        self.assertEqual(set(result["formal_metrics"]), set(FORMAL_METRICS))
        self.assertEqual(result["verdict"], "pass")
        self.assertTrue(result["cases"][0]["queries_changed_after_initial"])
        self.assertTrue(result["cases"][0]["evidence_backed_promotions"])

    def test_single_pass_does_not_claim_boundary_success(self):
        result = summarize({"results": [{
            "prompt_id": "focus",
            "boundary_diagnostics": {},
            "loop_diagnostics": {"iterations_used": 0},
        }]})
        self.assertEqual(result["verdict"], "insufficient_agentic_cases")
        self.assertIsNone(result["passed"])


if __name__ == "__main__":
    unittest.main()
