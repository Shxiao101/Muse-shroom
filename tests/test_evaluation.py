import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from evaluation.cassette import CassetteGitHub, load_cassette
from evaluation.run_ab import build_blind_pack, main as run_ab_main
from evaluation.score_ab import main as score_ab_main, reveal, summarize
from muse_shroom.models import SearchRequest
from muse_shroom.queries import build_queries


ROOT = Path(__file__).resolve().parents[1]


class EvaluationTests(unittest.TestCase):
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
            baseline = json.loads((root / "results" / "baseline.raw.json").read_text(encoding="utf-8"))
            candidate = json.loads((root / "results" / "candidate.raw.json").read_text(encoding="utf-8"))
            self.assertEqual(baseline["muse_shroom_version"], "0.2.0")
            self.assertEqual(candidate["muse_shroom_version"], "0.3.1")
            self.assertEqual(baseline["results"][0]["candidates"][0]["repo"], "owner/music-tool")
            self.assertEqual(candidate["results"][0]["candidates"][0]["repo"], "owner/music-tool")


if __name__ == "__main__":
    unittest.main()
