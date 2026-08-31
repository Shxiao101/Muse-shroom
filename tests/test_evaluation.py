import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from evaluation.cassette import CassetteGitHub, load_cassette
from evaluation.run_ab import build_blind_pack, main as run_ab_main
from evaluation.score_ab import main as score_ab_main, reveal, summarize
from evaluation.version_worker import (
    deterministic_assessment, deterministic_hypothesis, main as version_worker_main,
)
from muse_shroom import __version__ as muse_shroom_version
from muse_shroom.models import SearchRequest
from muse_shroom.queries import build_queries


ROOT = Path(__file__).resolve().parents[1]


class EvaluationTests(unittest.TestCase):
    def test_agentic_policy_uses_observation_only_and_prioritizes_evidence(self):
        used = set()
        hypothesis = deterministic_hypothesis({
            "unexplored_directions": ["requested direction"],
            "discovered_term_evidence": [{
                "term": "observed mechanism", "kind": "candidate_mechanism",
                "confidence": 0.9, "support_count": 2,
            }],
        }, used)
        self.assertEqual(hypothesis["promote_discovered_terms"], ["observed mechanism"])
        self.assertEqual(hypothesis["target_direction"], "observed mechanism")
        self.assertNotIn("requested direction", hypothesis.values())

    def test_agentic_policy_falls_back_to_observed_unexplored_direction(self):
        hypothesis = deterministic_hypothesis({
            "unexplored_directions": ["requested direction"],
            "discovered_term_evidence": [],
        }, set())
        self.assertEqual(hypothesis["target_direction"], "requested direction")
        self.assertNotIn("promote_discovered_terms", hypothesis)

    def test_agentic_policy_prefers_requested_direction_to_generic_project_category(self):
        hypothesis = deterministic_hypothesis({
            "unexplored_directions": ["requested direction"],
            "discovered_term_evidence": [{
                "term": "generic topic", "kind": "project_category",
                "confidence": 0.95, "support_count": 20,
            }],
        }, set())
        self.assertEqual(hypothesis["target_direction"], "requested direction")
        self.assertNotIn("promote_discovered_terms", hypothesis)

    def test_agentic_policy_does_not_promote_project_category_as_mechanism(self):
        hypothesis = deterministic_hypothesis({
            "unexplored_directions": [],
            "discovered_term_evidence": [{
                "term": "generic topic", "kind": "project_category",
                "confidence": 0.95, "support_count": 20,
            }],
        }, set())
        self.assertIsNone(hypothesis)

    def test_agentic_policy_skips_used_evidence_and_tries_the_next_direction(self):
        hypothesis = deterministic_hypothesis({
            "unexplored_directions": ["already queried request direction"],
            "discovered_term_evidence": [
                {"term": "already queried mechanism", "kind": "candidate_mechanism",
                 "confidence": 0.95, "support_count": 3},
                {"term": "fresh mechanism", "kind": "candidate_mechanism",
                 "confidence": 0.8, "support_count": 1},
            ],
        }, {"already queried mechanism", "already queried request direction"})

        self.assertEqual(hypothesis["target_direction"], "fresh mechanism")

    def test_deterministic_rank_fixture_cites_readme_without_claiming_judgment(self):
        candidate = {
            "full_name": "owner/tool", "topics": ["focus"],
            "mechanisms": [{"name": "biofeedback"}],
            "evidence": [
                {"id": "metadata", "kind": "github_metadata", "facts": {}},
                {"id": "excerpt", "kind": "readme_excerpt", "facts": {"text": "Measured feedback."}},
            ],
        }
        assessment = deterministic_assessment(candidate, {"artifact_types": ["application"]})
        self.assertEqual(assessment["reasons"][0]["evidence_ids"], ["excerpt"])
        self.assertEqual(assessment["risks"][0]["evidence_ids"], ["metadata"])
        self.assertIn("human review", assessment["risks"][0]["text"])
    def test_ab_prompt_set_has_two_prompts_per_category(self):
        payload = json.loads((ROOT / "evaluation" / "ab-prompts.json").read_text(encoding="utf-8"))
        prompts = payload["prompts"]
        self.assertEqual(len(prompts), 8)
        counts = {}
        for prompt in prompts:
            counts[prompt["category"]] = counts.get(prompt["category"], 0) + 1
            self.assertTrue(prompt["request"]["core_concepts"])
            self.assertNotIn("repo", prompt["request"])
        self.assertEqual(set(counts.values()), {2})

    def test_ab_release_gate_uses_behavior_scores_not_repository_names(self):
        evaluation = {
            "preferred": "candidate",
            "baseline": {name: 3 for name in ("relevance", "interesting", "evidence", "actionability", "diversity")},
            "candidate": {"relevance": 3, "interesting": 4, "evidence": 4, "actionability": 4, "diversity": 3},
        }
        payload = {"evaluations": [{"prompt_id": str(index), **evaluation} for index in range(8)]}
        result = summarize(payload)
        self.assertTrue(result["passed"])
        self.assertNotIn("repositories", result)

    def test_score_cli_can_save_machine_readable_summary(self):
        evaluation = {
            "preferred": "candidate",
            "baseline": {name: 3 for name in ("relevance", "interesting", "evidence", "actionability", "diversity")},
            "candidate": {"relevance": 3, "interesting": 4, "evidence": 4, "actionability": 4, "diversity": 3},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ratings = root / "ratings.json"
            summary = root / "summary.json"
            ratings.write_text(json.dumps({
                "evaluations": [{"prompt_id": str(index), **evaluation} for index in range(8)]
            }), encoding="utf-8")
            self.assertEqual(score_ab_main([str(ratings), "--output", str(summary)]), 0)
            self.assertTrue(json.loads(summary.read_text(encoding="utf-8"))["passed"])

    def test_cassette_records_and_replays_results_and_not_found(self):
        class ApiResult:
            def __init__(self, data, stale=False, cached_at=None, rate_limit=None):
                self.data = data
                self.stale = stale
                self.cached_at = cached_at
                self.rate_limit = rate_limit

        class NotFound(RuntimeError):
            pass

        class Delegate:
            rate_limits = {}

            def search_repositories(self, query, per_page=10, sort="stars"):
                return ApiResult({"items": [{"full_name": "owner/repo"}]})

            def readme(self, full_name):
                raise NotFound("missing")

        api = SimpleNamespace(ApiResult=ApiResult, GitHubNotFoundError=NotFound)
        with tempfile.TemporaryDirectory() as directory:
            cassette_path = Path(directory) / "fixture.json.gz"
            recorder = CassetteGitHub(api, cassette_path, delegate=Delegate())
            recorded = recorder.search_repositories("music", 10, "stars")
            self.assertEqual(recorded.data["items"][0]["full_name"], "owner/repo")
            with self.assertRaises(NotFound):
                recorder.readme("owner/repo")
            recorder.save()
            self.assertEqual(len(load_cassette(cassette_path)["calls"]), 2)

            replay = CassetteGitHub(api, cassette_path, delegate=None)
            self.assertEqual(
                replay.search_repositories("music", 10, "stars").data,
                recorded.data,
            )
            with self.assertRaises(NotFound):
                replay.readme("owner/repo")
            with self.assertRaisesRegex(RuntimeError, "cassette miss"):
                replay.repository("unknown/repo")

    def test_blind_pack_and_key_can_be_revealed_without_version_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            blind = root / "blind.json"
            key = root / "key.json"

            def payload(label, version, suffix):
                return {
                    "label": label, "muse_shroom_version": version,
                    "results": [{
                        "prompt_id": f"prompt-{index}", "category": "test",
                        "request": {"request": f"request {index}"},
                        "candidates": [{"repo": f"owner/{suffix}-{index}"}],
                    } for index in range(8)],
                }

            baseline.write_text(json.dumps(payload("baseline", "0.2.0", "old")), encoding="utf-8")
            candidate.write_text(json.dumps(payload("candidate", "0.3.0", "new")), encoding="utf-8")
            build_blind_pack(baseline, candidate, blind_path=blind, key_path=key, seed="fixed")
            blind_payload = json.loads(blind.read_text(encoding="utf-8"))
            self.assertNotIn("baseline", blind.read_text(encoding="utf-8"))
            self.assertNotIn("candidate", blind.read_text(encoding="utf-8"))
            self.assertEqual(len(blind_payload["cases"]), 8)

            ratings = {"evaluations": []}
            for case in blind_payload["cases"]:
                ratings["evaluations"].append({
                    "prompt_id": case["prompt_id"], "preferred": "A",
                    "A": {name: 4 for name in ("relevance", "interesting", "evidence", "actionability", "diversity")},
                    "B": {name: 3 for name in ("relevance", "interesting", "evidence", "actionability", "diversity")},
                })
            revealed = reveal(ratings, json.loads(key.read_text(encoding="utf-8")))
            self.assertEqual(len(revealed["evaluations"]), 8)
            self.assertTrue(all(item["preferred"] in {"baseline", "candidate"} for item in revealed["evaluations"]))

    def test_standard_blind_pack_equalizes_shortlist_length(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            blind = root / "blind.json"
            key = root / "key.json"
            baseline.write_text(json.dumps({
                "muse_shroom_version": "0.3.2",
                "results": [{
                    "prompt_id": "one", "category": "test", "request": {"request": "x"},
                    "candidates": [{"repo": f"old/{index}"} for index in range(24)],
                }],
            }), encoding="utf-8")
            candidate.write_text(json.dumps({
                "muse_shroom_version": "0.3.3",
                "results": [{
                    "prompt_id": "one", "category": "test", "request": {"request": "x"},
                    "candidates": [{"repo": f"new/{index}"} for index in range(12)],
                }],
            }), encoding="utf-8")
            build_blind_pack(
                baseline, candidate, blind_path=blind, key_path=key, seed="fixed",
                shortlist_limit=12, case_dir=root / "blind-cases",
            )
            case = json.loads(blind.read_text(encoding="utf-8"))["cases"][0]
            self.assertEqual(case["comparison"], "standard")
            self.assertEqual(len(case["lists"]["A"]), 12)
            self.assertEqual(len(case["lists"]["B"]), 12)
            manifest = json.loads((root / "blind-cases" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["cases"]), 1)
            files = manifest["cases"][0]["files"]
            self.assertEqual(len(files["A"]), 2)
            self.assertEqual(len(files["B"]), 2)
            for label in ("A", "B"):
                for filename in files[label]:
                    payload = json.loads((root / "blind-cases" / filename).read_text(encoding="utf-8"))
                    self.assertEqual(payload["list"], label)
                    self.assertLessEqual(len(payload["candidates"]), 6)

    def test_standard_pack_hides_internal_selection_fields_but_keeps_readme_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            blind = root / "blind.json"
            key = root / "key.json"
            row = {
                "repo": "owner/tool", "url": "https://github.com/owner/tool",
                "description": "Useful tool", "stars": 3, "topics": ["tool"],
                "language": "Python", "archived": False, "pushed_at": "2026-08-01T00:00:00Z",
                "selection_lanes": ["core"], "selection_score_components": {"recall": 99},
                "discovery_paths": [{"kind": "query"}],
                "mechanisms": [{
                    "name": "pomodoro", "role": "mechanism",
                    "evidence_ids": ["mechanisms"], "matched_terms": ["focus timer"],
                }],
                "evidence": [
                    {"id": "metadata", "kind": "github_metadata", "facts": {}},
                    {"id": "readme", "kind": "readme_excerpt", "facts": {
                        "snippet_type": "concept_match", "line_start": 2, "line_end": 3,
                        "sha": "abc", "parent_evidence_id": "parent", "text": "Specific behavior",
                        "untrusted_source": True,
                    }},
                    {"id": "mechanisms", "kind": "mechanism_match", "facts": {
                        "mechanisms": [{
                            "mechanism": "pomodoro", "role": "mechanism",
                            "source_field": "readme", "matched_term": "focus timer",
                            "text": "Specific focus timer behavior", "untrusted_source": True,
                        }],
                        "untrusted_source": True,
                    }},
                ],
            }
            payload = {
                "muse_shroom_version": "test", "results": [{
                    "prompt_id": "one", "category": "test", "request": {"request": "x"},
                    "candidates": [row],
                }],
            }
            baseline.write_text(json.dumps(payload), encoding="utf-8")
            candidate.write_text(json.dumps(payload), encoding="utf-8")
            build_blind_pack(
                baseline, candidate, blind_path=blind, key_path=key, seed="fixed",
                shortlist_limit=12,
            )
            public = json.loads(blind.read_text(encoding="utf-8"))["cases"][0]["lists"]["A"][0]
            self.assertNotIn("selection_lanes", public)
            self.assertNotIn("selection_score_components", public)
            self.assertNotIn("discovery_paths", public)
            self.assertEqual(len(public["evidence"]), 2)
            self.assertEqual(public["evidence"][0]["facts"]["sha"], "abc")
            self.assertEqual(public["mechanisms"][0]["evidence_ids"], ["mechanisms"])
            self.assertEqual(public["evidence"][1]["kind"], "mechanism_match")

    @unittest.skipUnless((ROOT / ".git").exists(), "requires the v0.2 Git revision")
    def test_replay_runs_v02_and_current_from_one_cassette(self):
        class ApiResult:
            def __init__(self, data, stale=False, cached_at=None, rate_limit=None):
                self.data = data
                self.stale = stale
                self.cached_at = cached_at
                self.rate_limit = rate_limit

        class NotFound(RuntimeError):
            pass

        class Delegate:
            rate_limits = {}

            def search_repositories(self, query, per_page=10, sort="stars"):
                return ApiResult({"items": [{
                    "full_name": "owner/music-tool", "html_url": "https://github.com/owner/music-tool",
                    "description": "Small music AI tool", "stargazers_count": 12,
                    "topics": ["music", "ai"], "pushed_at": "2026-08-01T00:00:00Z",
                    "archived": False, "language": "Python", "license": {"spdx_id": "MIT"},
                }]})

            def readme(self, full_name):
                return ApiResult("# Music Tool\n\nSmall music AI tool.\n\n## Install\n\npip install music-tool\n\n## Usage\n\nRun it.")

            def latest_release(self, full_name):
                raise NotFound("missing")

        api = SimpleNamespace(ApiResult=ApiResult, GitHubNotFoundError=NotFound)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompts = root / "prompts.json"
            request_data = {
                "request": "music AI tool", "core_concepts": ["music AI"],
                "adjacent_concepts": ["audio experiment"],
                "artifact_types": ["application"], "exclusions": [],
            }
            prompts.write_text(json.dumps({
                "prompts": [{"id": "one", "category": "creative", "request": request_data}]
            }), encoding="utf-8")
            cassette_path = root / "fixture.json.gz"
            recorder = CassetteGitHub(api, cassette_path, delegate=Delegate())
            request = SearchRequest.from_dict(request_data)
            for spec in build_queries(request):
                recorder.search_repositories(spec["query"], 10, spec.get("sort", "stars"))
            recorder.readme("owner/music-tool")
            with self.assertRaises(NotFound):
                recorder.latest_release("owner/music-tool")
            recorder.save()

            self.assertEqual(run_ab_main([
                "replay", "--repository", str(ROOT), "--prompts", str(prompts),
                "--cassette", str(cassette_path), "--output-dir", str(root / "results"),
            ]), 0)
            agentic_output = root / "agentic.json"
            self.assertEqual(version_worker_main([
                "--source-root", str(ROOT), "--prompts", str(prompts),
                "--output", str(agentic_output), "--cassette", str(cassette_path),
                "--data-dir", str(root / "agentic-data"), "--label", "agentic",
                "--mode", "replay", "--agentic", "--agentic-iterations", "0",
                "--boundary-rank",
            ]), 0)
            agentic = json.loads(agentic_output.read_text(encoding="utf-8"))
            self.assertEqual(agentic["stage"], "agentic_boundary_rank")
            self.assertTrue(agentic["results"][0]["ranking"]["display_order"])
            baseline = json.loads((root / "results" / "baseline.raw.json").read_text(encoding="utf-8"))
            candidate = json.loads((root / "results" / "candidate.raw.json").read_text(encoding="utf-8"))
            self.assertEqual(baseline["muse_shroom_version"], "0.2.0")
            self.assertEqual(candidate["muse_shroom_version"], muse_shroom_version)
            self.assertEqual(baseline["results"][0]["candidates"][0]["repo"], "owner/music-tool")
            self.assertEqual(candidate["results"][0]["candidates"][0]["repo"], "owner/music-tool")
        self.assertEqual(set(candidate["results"][0]["boundary_diagnostics"]), {
            "mechanism_count", "presented_mechanism_count", "mechanism_redundancy",
            "retrieval_mechanism_redundancy", "presentation_mechanism_redundancy",
            "redundancy_scope", "boundary_gain", "direction_coverage",
            "newly_presented_mechanism_count",
        })


if __name__ == "__main__":
    unittest.main()
