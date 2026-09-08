import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from evaluation.cassette import CassetteGitHub, load_cassette
from evaluation.matched_ab import (
    adapt_direct_arm, build_matched_blind_pack, build_schedule,
    check_claim_traceability, collect_repository_facts, load_requests,
    main as matched_ab_main,
)
from evaluation.run_ab import build_blind_pack, main as run_ab_main
from evaluation.score_ab import main as score_ab_main, reveal, summarize
from evaluation.score_matched_ab import (
    main as score_matched_main, reveal as reveal_matched, summarize as summarize_matched,
)
from evaluation.version_worker import (
    deterministic_hypothesis, deterministic_selection, main as version_worker_main,
)
from muse_shroom import __version__ as muse_shroom_version
from muse_shroom.github import GitHubNotFoundError
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
            "anchors": [{"term": "observed anchor", "repo": "owner/seed"}],
        }, set())
        self.assertEqual(hypothesis["target_direction"], "requested direction")
        self.assertEqual(hypothesis["concepts"], ["requested direction observed anchor"])
        self.assertEqual(hypothesis["seeds"], ["owner/seed"])
        self.assertEqual(hypothesis["strategies"], ["keyword", "relationship"])
        self.assertNotIn("promote_discovered_terms", hypothesis)

    def test_agentic_policy_does_not_gate_observed_source_terms_by_code_category(self):
        hypothesis = deterministic_hypothesis({
            "unexplored_directions": ["requested direction"],
            "discovered_term_evidence": [{
                "term": "generic topic", "kind": "source_term",
                "request_anchored": False, "support_count": 20,
            }],
        }, set())
        self.assertEqual(hypothesis["target_direction"], "generic topic")
        self.assertEqual(hypothesis["promote_discovered_terms"], ["generic topic"])

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
                {"id": "excerpt", "kind": "readme_excerpt", "facts": {
                    "text": "Measured feedback.", "sha": "abc123",
                }},
            ],
        }
        selection = deterministic_selection(candidate)
        self.assertEqual(selection["evidence_ids"], ["excerpt"])
        self.assertEqual(selection["quote"], "Measured feedback.")
        self.assertNotIn("relevance", selection)
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
            # The cross-version cassette predates recorded README SHAs. Retrieval
            # replays, but the current rank contract correctly refuses to invent
            # a SHA for quote verification.
            self.assertIsNone(agentic["results"][0]["ranking"])
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


class MatchedABContractTests(unittest.TestCase):
    def test_load_requests_accepts_the_registered_v2_file(self):
        needs = load_requests()
        self.assertEqual(
            [need["id"] for need in needs],
            [f"need-{index:02d}" for index in range(1, 9)],
        )
        for need in needs:
            self.assertEqual(set(need), {"id", "captured_at", "text"})
            self.assertTrue(need["text"])

    def test_load_requests_enforces_schema_v2_and_exact_fields(self):
        def valid_payload():
            return {
                "schema_version": 2,
                "collection_status": "ready_for_evaluation",
                "requests": [
                    {"id": f"need-{index:02d}", "captured_at": "2026-09-08", "text": f"need {index}"}
                    for index in range(1, 9)
                ],
            }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def write(payload):
                path = root / "ab-requests.json"
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                return path

            self.assertEqual(
                [need["id"] for need in load_requests(write(valid_payload()))],
                [f"need-{index:02d}" for index in range(1, 9)],
            )

            categorized = valid_payload()
            categorized["requests"][0]["category"] = "dev"
            with self.assertRaisesRegex(ValueError, "id/captured_at/text"):
                load_requests(write(categorized))

            stale_status = valid_payload()
            stale_status["collection_status"] = "awaiting_real_maintainer_needs"
            with self.assertRaisesRegex(ValueError, "collection_status"):
                load_requests(write(stale_status))

            legacy_schema = valid_payload()
            legacy_schema["schema_version"] = 1
            with self.assertRaisesRegex(ValueError, "schema_version"):
                load_requests(write(legacy_schema))

            too_few = valid_payload()
            too_few["requests"] = too_few["requests"][:7]
            with self.assertRaisesRegex(ValueError, "exactly 8"):
                load_requests(write(too_few))

    def test_build_schedule_is_deterministic_and_covers_every_need_and_arm(self):
        first = json.dumps(build_schedule("muse-shroom-matched-v1", 1), ensure_ascii=False)
        second = json.dumps(build_schedule("muse-shroom-matched-v1", 1), ensure_ascii=False)
        self.assertEqual(first, second)

        needs = {f"need-{index:02d}" for index in range(1, 9)}
        runs = json.loads(first)["runs"]
        self.assertEqual(len(runs), 16)
        self.assertEqual(
            {(run["need_id"], run["arm"]) for run in runs},
            {(need, arm) for need in needs for arm in ("muse-shroom", "direct")},
        )
        self.assertEqual(len({run["run_id"] for run in runs}), 16)
        for run in runs:
            self.assertEqual(
                set(run["metadata"]),
                {"model_id", "muse_shroom_revision", "skill_component_digest",
                 "timestamp", "configuration"},
            )
            self.assertTrue(run["metadata"]["skill_component_digest"])

        self.assertEqual(len(build_schedule("muse-shroom-matched-v1", 3)["runs"]), 48)
        self.assertNotEqual(
            [run["need_id"] for run in runs],
            [run["need_id"] for run in build_schedule("muse-shroom-matched-v2", 1)["runs"]],
        )

    def test_direct_adapter_requires_matching_requests_and_run_metadata(self):
        requests = {
            "schema_version": 2,
            "requests": [{
                "id": "need-1", "captured_at": "2026-09-08", "text": "Find a small tool",
            }],
        }
        direct = {
            "metadata": {
                "model_id": "model", "muse_shroom_revision": "none",
                "skill_component_digest": "none", "timestamp": "2026-09-03T00:00:00Z",
                "configuration": {},
            },
            "results": [{"prompt_id": "need-1", "candidates": []}],
        }

        adapted = adapt_direct_arm(requests, direct)

        self.assertEqual(adapted["results"][0]["prompt_id"], "need-1")
        self.assertEqual(adapted["results"][0]["request"], "Find a small tool")
        self.assertEqual(adapted["arm"], "direct")
        self.assertNotIn("category", adapted["results"][0])

    def test_direct_adapter_rejects_legacy_schema_and_incomplete_metadata(self):
        metadata = {
            "model_id": "model", "muse_shroom_revision": "none",
            "skill_component_digest": "none", "timestamp": "2026-09-03T00:00:00Z",
            "configuration": {},
        }
        direct = {"metadata": metadata, "results": [{"prompt_id": "need-1", "candidates": []}]}
        legacy = {"requests": [{"prompt_id": "need-1", "category": "dev", "request": "Find a tool"}]}
        with self.assertRaisesRegex(ValueError, "exactly match"):
            adapt_direct_arm(legacy, direct)
        incomplete = {key: value for key, value in metadata.items() if key != "timestamp"}
        with self.assertRaisesRegex(ValueError, "metadata is incomplete"):
            adapt_direct_arm(
                {"requests": [{"id": "need-1", "captured_at": "2026-09-08", "text": "Find a tool"}]},
                {"metadata": incomplete, "results": [{"prompt_id": "need-1", "candidates": []}]},
            )

    def test_matched_blind_pack_interleaves_arms_behind_ab_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            need_ids = ["need-1", "need-2"]

            def payload(arm, suffix):
                return {"arm": arm, "results": [
                    {
                        "prompt_id": need_id, "request": f"text for {need_id}",
                        "candidates": [{"repo": f"{suffix}/{need_id}"}],
                    }
                    for need_id in need_ids
                ]}

            blind = root / "blind.json"
            key = root / "blind-key.json"
            build_matched_blind_pack(
                payload("muse-shroom", "skill"), payload("direct", "bare"),
                blind_path=blind, key_path=key, seed="fixed",
            )
            blind_text = blind.read_text(encoding="utf-8")
            self.assertNotIn("muse-shroom", blind_text)
            self.assertNotIn("direct", blind_text)
            key_payload = json.loads(key.read_text(encoding="utf-8"))
            cases = json.loads(blind_text)["cases"]
            self.assertEqual([case["need_id"] for case in cases], need_ids)
            for case in cases:
                self.assertEqual(case["request"], f"text for {case['need_id']}")
                mapping = key_payload["mappings"][case["need_id"]]
                self.assertEqual(set(mapping), {"A", "B"})
                self.assertEqual(set(mapping.values()), {"muse-shroom", "direct"})
                for label, arm in mapping.items():
                    suffix = "skill" if arm == "muse-shroom" else "bare"
                    self.assertEqual(
                        [item["repo"] for item in case["lists"][label]],
                        [f"{suffix}/{case['need_id']}"],
                    )

    def test_matched_blind_pack_rejects_mismatched_arms(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def payload(request_text, prompt_ids):
                return {"arm": "direct", "results": [
                    {"prompt_id": need_id, "request": request_text, "candidates": []}
                    for need_id in prompt_ids
                ]}

            mushroom = payload("shared text", ["need-1", "need-2"])
            blind = root / "blind.json"
            key = root / "blind-key.json"
            with self.assertRaisesRegex(ValueError, "different request text"):
                build_matched_blind_pack(
                    mushroom, payload("other text", ["need-1", "need-2"]),
                    blind_path=blind, key_path=key, seed="fixed",
                )
            with self.assertRaisesRegex(ValueError, "same need IDs"):
                build_matched_blind_pack(
                    mushroom, payload("shared text", ["need-1"]),
                    blind_path=blind, key_path=key, seed="fixed",
                )

    def test_schedule_command_writes_a_reproducible_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second, other = root / "a.json", root / "b.json", root / "c.json"
            for path, seed in ((first, "fixed"), (second, "fixed"), (other, "elsewhere")):
                self.assertEqual(matched_ab_main([
                    "schedule", "--seed", seed, "--reps", "1", "--output", str(path),
                ]), 0)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertNotEqual(first.read_bytes(), other.read_bytes())
            payload = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual(payload["seed"], "fixed")
            self.assertEqual(len(payload["runs"]), 16)
            self.assertEqual({run["arm"] for run in payload["runs"]}, {"muse-shroom", "direct"})

    def test_blind_command_writes_the_pack_and_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def payload(suffix):
                return {"results": [{
                    "prompt_id": "need-1", "request": "same text",
                    "candidates": [{"repo": f"{suffix}/repo"}],
                }]}

            mushroom, direct = root / "muse.json", root / "direct.json"
            mushroom.write_text(json.dumps(payload("skill")), encoding="utf-8")
            direct.write_text(json.dumps(payload("bare")), encoding="utf-8")
            blind, key = root / "blind.json", root / "blind-key.json"

            self.assertEqual(matched_ab_main([
                "blind", "--muse-shroom", str(mushroom), "--direct", str(direct),
                "--output", str(blind), "--key", str(key), "--seed", "fixed",
            ]), 0)

            self.assertNotIn("muse-shroom", blind.read_text(encoding="utf-8"))
            mapping = json.loads(key.read_text(encoding="utf-8"))["mappings"]["need-1"]
            self.assertEqual(set(mapping.values()), {"muse-shroom", "direct"})

    def test_collect_repository_facts_aggregates_both_arms_read_only(self):
        class Result:
            def __init__(self, data):
                self.data = data

        class Client:
            def __init__(self):
                self.repository_calls = []
                self.readme_calls = []

            def repository(self, full_name):
                self.repository_calls.append(full_name)
                if full_name == "ghost/missing":
                    raise GitHubNotFoundError("missing")
                return Result({"full_name": full_name, "archived": full_name == "old/archived"})

            def readme(self, full_name):
                self.readme_calls.append(full_name)
                if full_name == "old/archived":
                    raise GitHubNotFoundError("no readme")
                return Result({"sha": f"sha-for-{full_name}", "text": f"Readme of {full_name}"})

        client = Client()
        arms = [
            {"results": [{"prompt_id": "need-1", "candidates": [
                {"repo": "Live/Tool", "quote": "x"},
                {"full_name": "live/TOOL", "quote": "y"},
                {"repo": "old/archived"},
            ]}]},
            {"results": [{"prompt_id": "need-2", "candidates": [
                {"repo": "ghost/missing"},
            ]}]},
        ]

        facts = collect_repository_facts(arms, client)

        self.assertEqual(client.repository_calls, ["ghost/missing", "Live/Tool", "old/archived"])
        self.assertEqual(client.readme_calls, ["Live/Tool", "old/archived"])
        self.assertEqual(facts["live/tool"], {
            "exists": True, "archived": False,
            "sources": [{"sha": "sha-for-Live/Tool", "text": "Readme of Live/Tool"}],
        })
        self.assertEqual(facts["old/archived"], {"exists": True, "archived": True, "sources": []})
        self.assertEqual(facts["ghost/missing"], {"exists": False})

    def test_claim_checker_separates_nonexistent_archived_and_text_mismatch(self):
        arm = {
            "arm": "muse-shroom",
            "results": [{
                "prompt_id": "need-1",
                "candidates": [
                    {"repo": "missing/repo"},
                    {"repo": "old/repo"},
                    {"repo": "wrong/quote", "source_term": "device", "quote": "exact quote"},
                    {"repo": "good/repo", "source_term": "device", "quote": "exact quote"},
                ],
            }],
        }
        facts = {
            "missing/repo": {"exists": False},
            "old/repo": {"exists": True, "archived": True},
            "wrong/quote": {
                "exists": True, "archived": False,
                "sources": [{"sha": "abc", "text": "different text"}],
            },
            "good/repo": {
                "exists": True, "archived": False,
                "sources": [{"sha": "def", "text": "a device with exact quote"}],
            },
        }

        checked = check_claim_traceability(arm, facts)

        failures = {item["repo"]: item["failures"] for item in checked["repositories"]}
        self.assertEqual(failures["missing/repo"], ["repository_not_found"])
        self.assertEqual(failures["old/repo"], ["repository_archived"])
        self.assertEqual(
            failures["wrong/quote"], ["quote_not_verbatim_at_recorded_sha"],
        )
        self.assertEqual(failures["good/repo"], [])


class MatchedScoreTests(unittest.TestCase):
    @staticmethod
    def ratings(specs):
        return {"evaluations": [
            {"need_id": need_id, "repetition": round, "preferred": preferred}
            for need_id, preferences in specs.items()
            for round, preferred in enumerate(preferences, 1)
        ]}

    def test_majority_pools_repetitions_and_passes_threshold(self):
        specs = {f"need-{index:02d}": ["muse-shroom", "direct", "muse-shroom"] for index in range(6)}
        specs["need-06"] = ["direct", "muse-shroom", "direct"]
        specs["need-07"] = ["tie", "tie", "tie"]

        result = summarize_matched(self.ratings(specs))

        self.assertEqual(result["mode"], "release")
        self.assertTrue(result["release_eligible"])
        self.assertEqual(result["wins"], {"muse-shroom": 6, "direct": 1, "tie": 1})
        self.assertEqual(result["need_verdicts"]["need-06"], "direct")
        self.assertEqual(result["need_verdicts"]["need-07"], "tie")
        self.assertEqual(result["verdict"], "pass")
        self.assertTrue(result["passed"])
        self.assertEqual(result["stability"]["per_need"]["need-00"], 2)
        self.assertEqual(result["stability"]["needs_with_flips"], 7)

    def test_ties_never_promote_an_arm_and_all_tie_needs_count_as_losses(self):
        specs = {f"need-{index:02d}": ["tie", "tie", "tie"] for index in range(8)}
        result = summarize_matched(self.ratings(specs))
        self.assertEqual(result["wins"], {"muse-shroom": 0, "direct": 0, "tie": 8})
        self.assertEqual(result["verdict"], "fail")
        self.assertFalse(result["passed"])
        self.assertEqual(result["stability"]["needs_with_flips"], 0)

        mixed = summarize_matched(self.ratings({"need-01": ["muse-shroom", "tie", "tie"]}))
        self.assertEqual(mixed["need_verdicts"], {"need-01": "tie"})
        self.assertEqual(mixed["wins"], {"muse-shroom": 0, "direct": 0, "tie": 1})

    def test_single_repetition_is_pilot_and_never_release_eligible(self):
        specs = {f"need-{index:02d}": ["muse-shroom"] for index in range(8)}

        result = summarize_matched(self.ratings(specs))

        self.assertEqual(result["mode"], "pilot")
        self.assertFalse(result["release_eligible"])
        self.assertEqual(result["verdict"], "not_measured")
        self.assertFalse(result["passed"])
        self.assertEqual(result["wins"], {"muse-shroom": 8, "direct": 0, "tie": 0})

    def test_claim_traceability_is_reported_but_never_gates(self):
        specs = {f"need-{index:02d}": ["muse-shroom", "muse-shroom", "direct"] for index in range(8)}
        reports = [
            {"arm": "muse-shroom", "checked": 10, "passed": 8, "failed": 2},
            {"arm": "direct", "checked": 6, "passed": 6, "failed": 0},
        ]

        result = summarize_matched(self.ratings(specs), reports)

        self.assertEqual(
            result["claim_traceability"],
            {
                "muse-shroom": {"checked": 10, "passed": 8, "failed": 2},
                "direct": {"checked": 6, "passed": 6, "failed": 0},
            },
        )
        self.assertTrue(result["passed"])

    def test_incomplete_or_invalid_ratings_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "same number of rated repetitions"):
            summarize_matched({"evaluations": [
                {"need_id": "a", "repetition": 1, "preferred": "tie"},
                {"need_id": "a", "repetition": 2, "preferred": "tie"},
                {"need_id": "b", "repetition": 1, "preferred": "tie"},
            ]})
        with self.assertRaisesRegex(ValueError, "duplicate rating"):
            summarize_matched({"evaluations": [
                {"need_id": "a", "repetition": 1, "preferred": "tie"},
                {"need_id": "a", "repetition": 1, "preferred": "direct"},
            ]})
        with self.assertRaisesRegex(ValueError, "muse-shroom, direct, or tie"):
            summarize_matched({"evaluations": [
                {"need_id": "a", "repetition": 1, "preferred": "baseline"},
            ]})
        with self.assertRaisesRegex(ValueError, "cover 1..2"):
            summarize_matched({"evaluations": [
                {"need_id": "a", "repetition": 2, "preferred": "tie"},
                {"need_id": "a", "repetition": 3, "preferred": "tie"},
            ]})

    def test_reveal_translates_blind_labels_before_scoring(self):
        key = {"mappings": {
            "need-01": {"A": "muse-shroom", "B": "direct"},
            "need-02": {"A": "direct", "B": "muse-shroom"},
        }}
        blind = {"evaluations": [
            {"need_id": "need-01", "repetition": 1, "preferred": "A",
             "A": {"relevance": 4}, "B": {"relevance": 2}},
            {"need_id": "need-02", "repetition": 1, "preferred": "B"},
            {"need_id": "need-01", "repetition": 2, "preferred": "tie"},
        ]}

        revealed = reveal_matched(blind, key)["evaluations"]

        self.assertEqual(
            [item["preferred"] for item in revealed],
            ["muse-shroom", "muse-shroom", "tie"],
        )
        self.assertEqual(revealed[0]["muse-shroom"], {"relevance": 4})
        self.assertEqual(revealed[0]["direct"], {"relevance": 2})
        with self.assertRaisesRegex(ValueError, "missing blind mapping"):
            reveal_matched(
                {"evaluations": [{"need_id": "need-09", "repetition": 1, "preferred": "A"}]}, key,
            )
        with self.assertRaisesRegex(ValueError, "must be A, B, or tie"):
            reveal_matched(
                {"evaluations": [
                    {"need_id": "need-01", "repetition": 1, "preferred": "muse-shroom"},
                ]}, key,
            )

    def test_cli_unblinds_with_the_key_before_scoring(self):
        need_ids = [f"need-{index:02d}" for index in range(8)]
        mappings = {
            need_id: ({"A": "muse-shroom", "B": "direct"} if index % 2
                      else {"A": "direct", "B": "muse-shroom"})
            for index, need_id in enumerate(need_ids)
        }
        blind = {"evaluations": [
            {"need_id": need_id, "repetition": 1,
             "preferred": "A" if mappings[need_id]["A"] == "muse-shroom" else "B"}
            for need_id in need_ids
        ]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ratings, key, summary = root / "r.json", root / "k.json", root / "s.json"
            ratings.write_text(json.dumps(blind), encoding="utf-8")
            key.write_text(json.dumps({"mappings": mappings}), encoding="utf-8")

            # Un-blinding gives Muse-shroom every need, and a pilot still exits non-zero.
            self.assertEqual(score_matched_main([
                str(ratings), "--key", str(key), "--output", str(summary),
            ]), 1)

            result = json.loads(summary.read_text(encoding="utf-8"))
            self.assertEqual(result["wins"], {"muse-shroom": 8, "direct": 0, "tie": 0})
            self.assertEqual(result["mode"], "pilot")
            self.assertFalse(result["release_eligible"])

    def test_matched_score_cli_can_save_machine_readable_summary(self):
        specs = {f"need-{index:02d}": ["muse-shroom", "muse-shroom", "direct"] for index in range(8)}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ratings = root / "matched-ratings.json"
            summary = root / "matched-summary.json"
            ratings.write_text(json.dumps(self.ratings(specs)), encoding="utf-8")
            self.assertEqual(score_matched_main([str(ratings), "--output", str(summary)]), 0)
            self.assertTrue(json.loads(summary.read_text(encoding="utf-8"))["passed"])


if __name__ == "__main__":
    unittest.main()
