import json
import unittest
from pathlib import Path

from evaluation.analyze_priority import (
    DEFAULT_CASSETTE,
    DEFAULT_LABELS,
    DEFAULT_RELEASE,
    LABELS,
    aggregate,
    analyze,
    canonical,
    dedupe,
    load_search_results,
    query_stage,
    render_markdown,
)


class PriorityAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.analysis = analyze(
            DEFAULT_RELEASE,
            DEFAULT_CASSETTE,
            DEFAULT_LABELS,
            root / "evaluation" / "boundary-prompts.json",
            root / "evaluation" / "holdout" / "boundary-prompts.json",
            root / "evaluation" / "boundary-golden-cases.json",
            root / "evaluation" / "holdout" / "boundary-golden-cases.json",
        )

    def test_canonical_normalizes_browser_surface(self):
        self.assertEqual(canonical("web automation"), "browser automation")

    def test_dedupe_preserves_distinct_core_phrase(self):
        shared = [{"repo": "one/compressor"}]
        items = [
            {"candidate": "adaptive context compression", "discovery_evidence": shared},
            {"candidate": "intelligent context compression", "discovery_evidence": shared},
            {"candidate": "adaptive data encryption", "discovery_evidence": shared},
        ]
        unique, owners = dedupe(items)
        self.assertEqual([item["candidate"] for item in unique], [
            "adaptive context compression", "adaptive data encryption",
        ])
        self.assertEqual(owners["intelligent context compression"], "adaptive context compression")

    def test_query_stage_classification(self):
        item = {"discovery_evidence": [{"repo": "one/source"}]}
        request = {"problem_concepts": ["focus improvement"]}
        self.assertEqual(query_stage('"focus improvement" "candidate"', item, request), "stage1")
        self.assertEqual(query_stage('"candidate" "anchor term"', item, request), "stage2")
        self.assertEqual(query_stage('"candidate" "one/source"', item, request), "stage3")

    def test_real_v046_analysis_inputs_are_available_and_bounded(self):
        self.assertTrue(DEFAULT_CASSETTE.exists())
        self.assertTrue(DEFAULT_LABELS.exists())
        self.assertTrue((DEFAULT_RELEASE / "boundary-development-agentic.raw.json").exists())
        searches = load_search_results(DEFAULT_CASSETTE)
        self.assertGreater(len(searches), 100)
        labels = json.loads(DEFAULT_LABELS.read_text(encoding="utf-8"))
        self.assertEqual(labels["schema_version"], 1)

    def test_aggregate_separates_attempted_and_skipped(self):
        records = [
            {"case_id": "one", "raw_queue_rank": 1, "human_diagnostic_label": "meaningful", "attempted": False, "deduped_queue_rank": 4, "query_details": [], "confirmation_status": "skipped_budget"},
            {"case_id": "one", "raw_queue_rank": 2, "human_diagnostic_label": "meaningful", "attempted": True, "deduped_queue_rank": 1, "query_details": [{"query_stage": "stage1", "same_repo_overlap": False, "independent_repo_count": 1, "new_core_evidence": True}], "confirmation_status": "confirmed", "query_count": 1, "query_stages": ["stage1"]},
            {"case_id": "one", "raw_queue_rank": 3, "human_diagnostic_label": "wrong_domain", "attempted": True, "deduped_queue_rank": 2, "query_details": [{"query_stage": "stage3", "same_repo_overlap": True, "independent_repo_count": 0, "new_core_evidence": False}], "confirmation_status": "rejected", "query_count": 1, "query_stages": ["stage3"]},
        ]
        metrics = aggregate(records, "development")
        self.assertEqual(metrics["meaningful_candidate_count"], 2)
        self.assertEqual(metrics["meaningful_attempted_count"], 1)
        self.assertEqual(metrics["meaningful_skipped_count"], 1)
        self.assertEqual(metrics["top_1_meaningful_coverage"], 0.5)
        self.assertEqual(metrics["queries_on_eventual_rejects"], 1)
        self.assertEqual(metrics["queries_with_same_repo_overlap"], 1)

    def test_real_analysis_meets_bounded_delivery_contract(self):
        dev = self.analysis["metrics"]["development"]
        holdout = self.analysis["metrics"]["holdout"]
        self.assertEqual(dev["total_executed_queries"], 35)
        self.assertEqual(holdout["total_executed_queries"], 26)
        self.assertEqual(dev["queries_on_eventual_rejects"] + holdout["queries_on_eventual_rejects"], 55)
        self.assertEqual(self.analysis["holdout_taxonomy_counts"], {"A": 0, "B": 3, "C": 1})
        self.assertLessEqual(len(self.analysis["root_causes"]), 3)
        self.assertLessEqual(len(self.analysis["recommended_directions"]), 2)

    def test_candidate_records_have_required_trace_fields(self):
        required = {
            "suite", "case_id", "candidate", "canonical_term", "raw_queue_rank",
            "deduped_queue_rank", "discovery_repos", "discovery_source_fields",
            "evidence_relevance_score", "mechanism_specificity", "novelty_score",
            "confirmability_score", "confirmation_priority_score",
            "confirmation_priority_reason", "attempted", "confirmation_status",
            "skip_reason", "query_count", "query_stages", "confirmation_repos",
            "confirmation_evidence", "frozen_taxonomy_match", "human_diagnostic_label",
        }
        for suite_records in self.analysis["records"].values():
            for record in suite_records:
                self.assertTrue(required <= record.keys())
                self.assertIsNotNone(record["evidence_relevance_score"])
                self.assertIsNotNone(record["mechanism_specificity"])

    def test_candidate_status_and_query_count_trace_to_confirmation_analysis(self):
        source = json.loads((DEFAULT_RELEASE / "confirmation-analysis.json").read_text(encoding="utf-8"))
        for suite in ("development", "holdout"):
            expected = {
                (record["prompt_id"], record["candidate"]): (
                    record["confirmation_status"], len(record.get("confirmation_queries") or [])
                )
                for record in source["suites"][suite]["records"]
            }
            actual = {
                (record["case_id"], record["candidate"]): (
                    record["confirmation_status"], record["query_count"]
                )
                for record in self.analysis["records"][suite]
            }
            self.assertEqual(actual, expected)
            for case_id in {key[0] for key in actual}:
                ranks = sorted(
                    record["raw_queue_rank"] for record in self.analysis["records"][suite]
                    if record["case_id"] == case_id
                )
                self.assertEqual(ranks, list(range(1, len(ranks) + 1)))

    def test_executed_queries_have_required_attribution_fields(self):
        required = {
            "candidate", "query_stage", "query_kind", "query_result_count",
            "new_repo_count", "independent_repo_count", "same_repo_overlap",
            "new_core_evidence", "final_candidate_label",
        }
        for suite_records in self.analysis["records"].values():
            for record in suite_records:
                for detail in record["query_details"]:
                    self.assertTrue(required <= detail.keys())
                    self.assertIn(detail["query_stage"], {"stage1", "stage2", "stage3"})
                    self.assertIn(detail["final_candidate_label"], LABELS | {"unlabeled"})

    def test_blind_packets_exclude_outcome_and_priority_fields(self):
        forbidden = {
            "raw_queue_rank", "deduped_queue_rank", "confirmation_priority_score",
            "confirmation_priority_reason", "attempted", "confirmation_status",
            "skip_reason", "human_diagnostic_label", "release_verdict", "golden",
        }
        for packet in self.analysis["blind_review_development"] + self.analysis["blind_review_holdout"]:
            self.assertFalse(forbidden & packet.keys())

    def test_top_three_development_skipped_candidates_are_labeled(self):
        self.assertEqual(len(self.analysis["top_skipped_development"]), 8)
        for records in self.analysis["top_skipped_development"].values():
            self.assertEqual(len(records), 3)
            self.assertTrue(all(record.get("human_diagnostic_label") for record in records))

    def test_markdown_is_rendered_from_analysis(self):
        markdown = render_markdown(self.analysis)
        self.assertIn("Queries on eventual rejects: 55/61", markdown)
        self.assertIn("A/B/C = 0 / 3 / 1", markdown)


if __name__ == "__main__":
    unittest.main()
