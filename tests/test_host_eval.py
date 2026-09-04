import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from evaluation import host_eval
from evaluation.cassette import CassetteGitHub
from evaluation.host_eval import (
    RecordingAttempt, _component_digest, _development_shakedown_ready,
    _select_development_attempt, _validate_holdout_capture_governance,
    classify_attempt, create_recording_server, prepare, summarize_attempt,
)
from muse_shroom.github import ApiResult
from muse_shroom.storage import Store

from tests.helpers import FrozenGitHub, repo


ROOT = Path(__file__).resolve().parents[1]


def _payload(result):
    if getattr(result, "is_error", False):
        text = " ".join(getattr(item, "text", str(item)) for item in result.content or [])
        raise AssertionError(f"tool failed: {text}")
    data = result.structured_content
    if isinstance(data, dict) and "search_id" not in data and isinstance(data.get("result"), dict):
        return data["result"]
    if data is not None:
        return data
    for item in result.content or []:
        if getattr(item, "text", None):
            value = json.loads(item.text)
            if isinstance(value, dict):
                return value
    raise AssertionError("tool returned no JSON object")


class HostPrepareTests(unittest.TestCase):
    @staticmethod
    def _write_development_attempt(
        root: Path, *, attempt_id: str = "attempt-01", total: int = 8,
        proposal_cases: int = 8,
        component_digest: str | None = None,
        recorded_calls: int = 1, incomplete: bool = False,
    ) -> Path:
        """Write a sealed development attempt with the fields the preflight reads."""
        attempt = root / attempt_id
        attempt.mkdir(parents=True)
        (attempt / "attempt-events.jsonl").write_text(
            '{"event": "attempt_started"}\n', encoding="utf-8",
        )
        if recorded_calls:
            (attempt / "transcript.jsonl").write_text(
                "".join(
                    json.dumps({"sequence": index, "tool": "muse_search"}) + "\n"
                    for index in range(1, recorded_calls + 1)
                ),
                encoding="utf-8",
            )
        digest = _component_digest(ROOT) if component_digest is None else component_digest
        missing = [f"case-{index:02d}" for index in range(proposal_cases + 1, total + 1)]
        (attempt / "manifest.json").write_text(json.dumps({
            "attempt_id": attempt_id,
            "suite": "development",
            "suite_id": "development-v1-example",
            "suite_digest": "suite-digest",
            "agent_visible_bundle_digest": "bundle-digest",
            "agent_visible_component_digest": digest,
            "classification": "capture_incomplete" if incomplete else "capture_complete",
            "classification_reason": (
                "capture_infrastructure_failure" if incomplete else "artifacts_intact"
            ),
            "capture_closed": not incomplete,
            "expected_case_count": total,
            "completed_case_count": 0 if incomplete else total,
            "diagnostic_failures": ["incomplete_cases"] if incomplete else [],
            "cassette": {"sha256": "cassette-digest"},
        }), encoding="utf-8")
        (attempt / "raw-summary.json").write_text(json.dumps({
            "attempt_id": attempt_id,
            "suite_id": "development-v1-example",
            "suite_digest": "suite-digest",
            "agent_visible_bundle_digest": "bundle-digest",
            "agent_visible_component_digest": digest,
            "cassette_digest": "cassette-digest",
            "host_hypotheses_proposed": proposal_cases,
            "host_hypothesis_proposal_case_rate": {
                "hits": proposal_cases, "total": total,
                "rate": round(proposal_cases / total, 4),
            },
            "cases_without_hypothesis": missing,
            "case_level_summary_included": True,
        }), encoding="utf-8")
        return attempt

    @classmethod
    def _write_ready_development(cls, root: Path) -> Path:
        return cls._write_development_attempt(root)

    def test_prepare_is_create_only_and_emits_external_integrity_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            self._write_ready_development(root / "development")
            kwargs = {
                "suite": "holdout",
                "attempt_root": root / "attempts",
                "development_root": root / "development",
            }
            result = prepare(bundle, **kwargs)
            self.assertEqual(result["cases"], 6)
            self.assertTrue((bundle / "mcp.json").is_file())
            self.assertTrue((bundle / "HOST-RUN.md").is_file())
            self.assertFalse((bundle / "evaluation").exists())
            self.assertTrue(Path(result["mapping"]).is_file())
            self.assertEqual(
                result["agent_visible_component_digest"], _component_digest(ROOT),
            )
            self.assertEqual(
                result["development_gate"]["agent_visible_component_digest"],
                _component_digest(ROOT),
            )
            leakage = json.loads(Path(result["leakage"]).read_text(encoding="utf-8"))
            self.assertTrue(leakage["passed"])
            self.assertFalse(leakage["release_eligible"])
            with self.assertRaises(FileExistsError):
                prepare(bundle, **kwargs)

    def test_non_eight_development_suites_use_manifest_derived_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # No suite size is hard-coded, and no proposal rate blocks: a seven-case
            # suite is accepted, and its rate is reported with the manifest denominator.
            self._write_development_attempt(root / "seven", total=7, proposal_cases=5)
            result = prepare(
                root / "bundle", suite="holdout", attempt_root=root / "attempts",
                development_root=root / "seven",
            )
            self.assertEqual(
                result["development_gate"]["host_hypothesis_proposal_case_rate"],
                {"hits": 5, "total": 7, "rate": round(5 / 7, 4)},
            )
            self.assertEqual(result["development_gate"]["expected_case_count"], 7)

    def test_rate_denominator_must_match_the_prepared_case_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempt = self._write_development_attempt(root / "development")
            summary = json.loads((attempt / "raw-summary.json").read_text(encoding="utf-8"))
            summary["host_hypothesis_proposal_case_rate"]["total"] = 5
            (attempt / "raw-summary.json").write_text(
                json.dumps(summary), encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "denominator"):
                prepare(
                    root / "bundle", suite="holdout", attempt_root=root / "attempts",
                    development_root=root / "development",
                )

    def test_newer_failed_development_attempt_cannot_be_bypassed_by_an_older_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            development = root / "development"
            self._write_development_attempt(development, attempt_id="attempt-01")
            self._write_development_attempt(
                development, attempt_id="attempt-02", incomplete=True,
            )
            with self.assertRaisesRegex(ValueError, "attempt-02 did not close cleanly"):
                prepare(
                    root / "bundle", suite="holdout", attempt_root=root / "attempts",
                    development_root=development,
                )

    def test_component_digest_binds_the_shakedown_to_the_skill_it_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_development_attempt(
                root / "development", component_digest="stale-skill-digest",
            )
            with self.assertRaisesRegex(ValueError, "Skill and production source changed"):
                prepare(
                    root / "bundle", suite="holdout", attempt_root=root / "attempts",
                    development_root=root / "development",
                )
            self.assertFalse((root / "bundle").exists())

    def test_collect_reruns_the_preflight_after_prepare(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            development = root / "development"
            bundle = root / "bundle"
            self._write_development_attempt(development, attempt_id="attempt-01")
            prepare(
                bundle, suite="holdout", attempt_root=root / "attempts",
                development_root=development,
            )
            # A failing shakedown sealed after prepare must still block the capture,
            # even though the acknowledgement carried in mcp.json is well formed.
            self._write_development_attempt(
                development, attempt_id="attempt-02", incomplete=True,
            )
            with self.assertRaisesRegex(ValueError, "attempt-02 did not close cleanly"):
                RecordingAttempt(
                    bundle, root / "attempts", development_root=development,
                )
            self.assertFalse((root / "attempts" / "attempt-01").exists())

    def test_incomplete_capture_omits_case_rows_but_keeps_raw_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            self._write_ready_development(root / "development")
            prepared = prepare(
                bundle, suite="holdout", attempt_root=root / "holdout",
                development_root=root / "development",
            )
            attempt = RecordingAttempt(
                bundle, root / "holdout", development_root=root / "development",
            )
            attempt.close()
            summary = summarize_attempt(
                attempt.path, mapping_path=Path(prepared["mapping"]), suite="holdout",
            )
            self.assertEqual(summary["classification"], "capture_incomplete")
            self.assertFalse(summary["case_level_summary_included"])
            self.assertEqual(summary["cases"], [])
            self.assertEqual(summary["cases_without_hypothesis"], [])
            manifest = json.loads((attempt.path / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["case_level_summary_included"])
            self.assertEqual(
                manifest["development_gate"]["agent_visible_component_digest"],
                _component_digest(ROOT),
            )
            self.assertEqual(
                manifest["agent_visible_component_digest"], _component_digest(ROOT),
            )
            # The raw capture is immutable evidence and survives suppression.
            self.assertTrue((attempt.path / "attempt-events.jsonl").is_file())
            self.assertTrue((attempt.path / "data").is_dir())

    def test_idle_server_launches_do_not_consume_the_attempt_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            development = root / "development"
            # An MCP client allocates an attempt on every launch, including launches
            # the host never uses. Those hold a start event and nothing else.
            self._write_development_attempt(development, attempt_id="attempt-03")
            for idle in ("attempt-04", "attempt-05"):
                (development / idle).mkdir(parents=True)
                (development / idle / "attempt-events.jsonl").write_text(
                    '{"event": "attempt_started"}\n', encoding="utf-8",
                )
            selected = _select_development_attempt(development)
            self.assertEqual(selected["attempt_id"], "attempt-03")

            holdout = root / "holdout"
            (holdout / "attempt-01").mkdir(parents=True)
            (holdout / "attempt-01" / "attempt-events.jsonl").write_text(
                '{"event": "attempt_started"}\n', encoding="utf-8",
            )
            # An idle holdout launch is not an unclassified attempt.
            _validate_holdout_capture_governance(holdout)

            recorded = holdout / "attempt-02"
            recorded.mkdir()
            (recorded / "transcript.jsonl").write_text(
                '{"sequence": 1, "tool": "muse_search"}\n', encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "is not classified"):
                _validate_holdout_capture_governance(holdout)

    def test_recorded_attempts_are_never_skipped_for_a_newer_idle_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            development = root / "development"
            self._write_development_attempt(development, attempt_id="attempt-01")
            self._write_development_attempt(
                development, attempt_id="attempt-02", incomplete=True,
            )
            (development / "attempt-03").mkdir()
            (development / "attempt-03" / "attempt-events.jsonl").write_text(
                '{"event": "attempt_started"}\n', encoding="utf-8",
            )
            # attempt-03 is newer but empty; attempt-02 recorded calls and failed.
            with self.assertRaisesRegex(ValueError, "attempt-02 did not close cleanly"):
                _select_development_attempt(development)

    def test_terminated_capture_is_sealable_once_quiet(self):
        # MCP clients terminate stdio servers rather than shutting them down, so an
        # attempt usually has no close event. That must not permanently block the gate.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            self._write_ready_development(root / "development")
            prepared = prepare(
                bundle, suite="holdout", attempt_root=root / "holdout",
                development_root=root / "development",
            )
            attempt = RecordingAttempt(
                bundle, root / "holdout", development_root=root / "development",
            )
            events = attempt.path / "attempt-events.jsonl"
            # No close(): the process was killed.
            with self.assertRaisesRegex(ValueError, "has no close event"):
                summarize_attempt(
                    attempt.path, mapping_path=Path(prepared["mapping"]), suite="holdout",
                )
            stale = events.stat().st_mtime - host_eval.CAPTURE_QUIESCE_SECONDS - 5
            os.utime(events, (stale, stale))
            summary = summarize_attempt(
                attempt.path, mapping_path=Path(prepared["mapping"]), suite="holdout",
            )
            manifest = json.loads((attempt.path / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["close_kind"], "terminated")
            self.assertTrue(manifest["capture_closed"])
            self.assertNotIn("capture_closed_with_error", manifest["diagnostic_failures"])
            self.assertEqual(summary["classification"], "capture_incomplete")

    def test_gracefully_closed_capture_records_its_close_kind(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            self._write_ready_development(root / "development")
            prepared = prepare(
                bundle, suite="holdout", attempt_root=root / "holdout",
                development_root=root / "development",
            )
            attempt = RecordingAttempt(
                bundle, root / "holdout", development_root=root / "development",
            )
            attempt.close()
            summarize_attempt(
                attempt.path, mapping_path=Path(prepared["mapping"]), suite="holdout",
            )
            manifest = json.loads((attempt.path / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["close_kind"], "graceful")
            self.assertTrue(manifest["capture_closed"])

    def test_rank_outcome_surfaces_a_case_that_ended_with_nothing_accepted(self):
        from evaluation.host_eval import _rank_outcome

        # A case counts as completed once any rank returns output, so a run where every
        # item failed verification must be visible in the rejection breakdown.
        events = [
            {"tool": "muse_search", "output": {"search_id": "s"}},
            {"tool": "muse_iterate", "output": {"next_action": "rank"}},
            {"tool": "muse_rank", "output": {
                "items": [],
                "rejected_items": [
                    {"reasons": ["quote_not_verbatim_at_recorded_sha"]},
                    {"reasons": ["evidence_not_owned:repo:other:readme"]},
                ],
            }},
        ]
        outcome = _rank_outcome(events)
        self.assertTrue(outcome["ended_with_empty_selection"])
        self.assertEqual(outcome["selection_accepted"], 0)
        self.assertEqual(outcome["selection_rejected"], 2)
        self.assertEqual(outcome["rejection_reasons"], {
            "quote_not_verbatim_at_recorded_sha": 1, "evidence_not_owned": 1,
        })

    def test_rank_outcome_reads_the_final_retry(self):
        from evaluation.host_eval import _rank_outcome

        events = [
            {"tool": "muse_rank", "output": {
                "items": [], "rejected_items": [{"reasons": ["quote_not_verbatim_at_recorded_sha"]}],
            }},
            {"tool": "muse_rank", "output": {"items": [{"repo": "owner/name"}], "rejected_items": []}},
        ]
        outcome = _rank_outcome(events)
        self.assertEqual(outcome["rank_calls"], 2)
        self.assertEqual(outcome["selection_accepted"], 1)
        self.assertFalse(outcome["ended_with_empty_selection"])
        # Reasons accumulate across attempts so a recovered case still shows what failed.
        self.assertEqual(outcome["rejection_reasons"], {"quote_not_verbatim_at_recorded_sha": 1})

    def test_change_set_b_evidence_paths_remain_trackable(self):
        evidence = "evaluation/evidence/host-v0.7.0/example/attempt-01"
        for name in ("manifest.json", "raw-summary.json", "leakage.json", "score.json"):
            with self.subTest(name=name):
                result = subprocess.run(
                    ["git", "check-ignore", "-q", "--no-index", f"{evidence}/{name}"],
                    cwd=ROOT, capture_output=True, text=True, check=False,
                )
                # git check-ignore exits 1 when a path is NOT ignored, which is the
                # outcome this assertion wants. Never chain it with && or set -e.
                self.assertEqual(result.returncode, 1, f"{name} is ignored: {result.stdout}")
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", "evaluation/results/x/score.json"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(ignored.returncode, 0, "capture results are expected to stay ignored")

    def test_attempt_allocator_never_reuses_existing_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            prepare(bundle, attempt_root=root / "attempts")
            first = RecordingAttempt(bundle, root / "attempts")
            first.close()
            second = RecordingAttempt(bundle, root / "attempts")
            second.close()
            self.assertEqual(first.path.name, "attempt-01")
            self.assertEqual(second.path.name, "attempt-02")
            self.assertTrue(first.events_path.exists())
            self.assertTrue(second.events_path.exists())

    def test_holdout_capture_requires_ready_development_and_sealed_governance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            self._write_ready_development(root / "development")
            self.assertEqual(
                _development_shakedown_ready(root / "development"),
                (True, str(root / "development" / "attempt-01")),
            )
            prepare(
                bundle, suite="holdout", attempt_root=root / "holdout",
                development_root=root / "development",
            )
            with self.assertRaisesRegex(ValueError, "requires development shakedown"):
                RecordingAttempt(
                    bundle, root / "holdout", development_root=root / "missing",
                )

            first = RecordingAttempt(
                bundle, root / "holdout", development_root=root / "development",
            )
            first.close()
            # Only an attempt that recorded host calls is a real attempt; an idle
            # launch carries no evidence and is skipped by governance.
            _validate_holdout_capture_governance(
                root / "holdout", suite_id=first.suite_id,
                suite_digest=first.suite_digest,
            )
            (first.path / "transcript.jsonl").write_text(
                '{"sequence": 1, "tool": "muse_search"}\n', encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "is not classified"):
                RecordingAttempt(
                    bundle, root / "holdout", development_root=root / "development",
                )

            (first.path / "manifest.json").write_text(json.dumps({
                "suite": "holdout", "suite_id": first.suite_id,
                "suite_digest": first.suite_digest,
                "classification": "capture_incomplete",
            }), encoding="utf-8")
            _validate_holdout_capture_governance(
                root / "holdout", suite_id=first.suite_id,
                suite_digest=first.suite_digest,
            )
            second = RecordingAttempt(
                bundle, root / "holdout", development_root=root / "development",
            )
            second.close()
            (second.path / "manifest.json").write_text(json.dumps({
                "suite": "holdout", "suite_id": second.suite_id,
                "suite_digest": second.suite_digest,
                "classification": "capability_observed",
            }), encoding="utf-8")
            (second.path / "transcript.jsonl").write_text(
                '{"sequence": 1, "tool": "muse_search"}\n', encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "cannot be recaptured"):
                RecordingAttempt(
                    bundle, root / "holdout", development_root=root / "development",
                )

    @unittest.skipUnless(importlib.util.find_spec("mcp") is not None, "MCP extra is not installed")
    def test_collect_and_summarize_run_from_cli_without_mutating_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            attempts = root / "attempts"
            prepared = prepare(bundle, suite="development", attempt_root=attempts)
            collected = subprocess.run(
                [
                    sys.executable, "evaluation/host_eval.py", "collect",
                    "--bundle", str(bundle), "--attempt-root", str(attempts),
                    "--search-interval", "0",
                ],
                cwd=ROOT, input="", capture_output=True, text=True, encoding="utf-8",
                check=False,
            )
            self.assertEqual(collected.returncode, 0, collected.stderr)
            summarized = subprocess.run(
                [
                    sys.executable, "evaluation/host_eval.py", "summarize",
                    "--attempt", str(attempts / "attempt-01"),
                    "--mapping", str(prepared["mapping"]), "--suite", "development",
                ],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
            )
            self.assertEqual(summarized.returncode, 0, summarized.stderr)
            self.assertEqual(json.loads(summarized.stdout)["classification_reason"], "capture_infrastructure_failure")
            self.assertFalse(any(bundle.rglob("__pycache__")))


class _Clock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class CassetteCaptureSafetyTests(unittest.TestCase):
    def test_non_search_call_does_not_reset_repository_search_clock(self):
        class NotFound(RuntimeError):
            pass

        class Delegate:
            rate_limits = {}

            def search_repositories(self, query, per_page=10, sort="stars"):
                return ApiResult({"items": []})

            def readme(self, full_name):
                return ApiResult("readme")

        clock = _Clock()
        api = SimpleNamespace(
            ApiResult=ApiResult, GitHubNotFoundError=NotFound,
            GitHubRateLimitError=type("RateLimit", (RuntimeError,), {}),
        )
        with tempfile.TemporaryDirectory() as directory:
            recorder = CassetteGitHub(
                api, Path(directory) / "capture.json.gz", delegate=Delegate(),
                search_interval=3.5, serial_capture=True,
                monotonic=clock.monotonic, wall_time=lambda: 0, sleeper=clock.sleep,
            )
            recorder.search_repositories("first")
            recorder.readme("owner/repo")
            recorder.search_repositories("second")
        starts = [
            item["monotonic"] for item in recorder.payload["capture_diagnostics"]
            if item.get("event") == "search_started"
        ]
        self.assertEqual(starts, [100.0, 103.5])
        self.assertEqual(clock.sleeps, [3.5])
        outcomes = [
            item for item in recorder.payload["capture_diagnostics"]
            if item.get("event") == "api_outcome"
        ]
        self.assertEqual(len(outcomes), 3)
        self.assertTrue(all(item.get("request_sequence") for item in outcomes))

    def test_terminal_github_error_is_redacted_recorded_and_replayable(self):
        class GitHubError(RuntimeError):
            pass

        class NotFound(GitHubError):
            pass

        class Delegate:
            rate_limits = {}

            def readme(self, full_name):
                raise GitHubError("request failed with github_pat_secretvalue")

        api = SimpleNamespace(
            ApiResult=ApiResult, GitHubError=GitHubError,
            GitHubNotFoundError=NotFound,
            GitHubRateLimitError=type("RateLimit", (GitHubError,), {}),
        )
        with tempfile.TemporaryDirectory() as directory:
            cassette = Path(directory) / "capture.json.gz"
            recorder = CassetteGitHub(api, cassette, delegate=Delegate(), auto_save=True)
            with self.assertRaises(GitHubError):
                recorder.readme("owner/repo")
            response = next(iter(recorder.payload["calls"].values()))["response"]
            self.assertEqual(response["kind"], "error")
            self.assertEqual(response["message"], "request failed with [redacted]")
            replay = CassetteGitHub(api, cassette, delegate=None)
            with self.assertRaisesRegex(GitHubError, "redacted"):
                replay.readme("owner/repo")

    def test_rate_limit_backoff_priority_and_three_retry_cap(self):
        class RateLimit(RuntimeError):
            pass

        class NotFound(RuntimeError):
            pass

        api = SimpleNamespace(
            ApiResult=ApiResult, GitHubError=RuntimeError,
            GitHubNotFoundError=NotFound, GitHubRateLimitError=RateLimit,
        )
        scenarios = [
            ({"retry_after": 7, "remaining": 0, "reset": 130}, 7, "retry_after"),
            ({"remaining": 0, "reset": 130}, 30, "rate_limit_reset"),
            ({"remaining": 5}, 60, "secondary_limit"),
        ]
        for index, (rate_limit, delay, reason) in enumerate(scenarios):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                class Delegate:
                    rate_limits = {"search": rate_limit}

                    def __init__(self):
                        self.calls = 0

                    def search_repositories(self, query, per_page=10, sort="stars"):
                        self.calls += 1
                        if self.calls == 1:
                            raise RateLimit("wait")
                        return ApiResult({"items": []})

                clock = _Clock()
                delegate = Delegate()
                recorder = CassetteGitHub(
                    api, Path(directory) / f"capture-{index}.json.gz", delegate=delegate,
                    monotonic=clock.monotonic, wall_time=lambda: 100, sleeper=clock.sleep,
                )
                recorder.search_repositories("query")
                self.assertEqual(clock.sleeps, [delay])
                waits = [
                    item for item in recorder.payload["capture_diagnostics"]
                    if item.get("event") == "rate_limit_wait"
                ]
                self.assertEqual([item["reason"] for item in waits], [reason])

        class AlwaysLimited:
            rate_limits = {"search": {"remaining": 4}}

            def __init__(self):
                self.calls = 0

            def search_repositories(self, query, per_page=10, sort="stars"):
                self.calls += 1
                raise RateLimit("still limited")

        with tempfile.TemporaryDirectory() as directory:
            clock = _Clock()
            delegate = AlwaysLimited()
            recorder = CassetteGitHub(
                api, Path(directory) / "limited.json.gz", delegate=delegate,
                monotonic=clock.monotonic, wall_time=lambda: 100, sleeper=clock.sleep,
            )
            with self.assertRaises(RateLimit):
                recorder.search_repositories("query")
            self.assertEqual(delegate.calls, 4)
            self.assertEqual(clock.sleeps, [60, 60, 60])


@unittest.skipUnless(importlib.util.find_spec("mcp") is not None, "MCP extra is not installed")
class RecordingProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_records_real_calls_and_summarize_seals_attempt(self):
        from mcp import Client

        request = {
            "request": "提高专注力",
            "problem_concepts": ["focus improvement"],
            "mechanisms": ["focus timer"],
            "artifact_types": ["application"],
            "exploration_level": 0.7,
        }
        prompts = {"schema_version": 1, "prompts": [{"id": "focus", "request": request}]}
        timer = repo("focus/timer", 200, description="focus timer")
        feedback = repo("labs/biofeedback", 20, description="biofeedback training")
        github = FrozenGitHub(
            [("focus", [timer]), ("biofeedback", [feedback])],
            readmes={
                "focus/timer": "# Focus timer\nA focus timer.\n## Usage\nStart.",
                "labs/biofeedback": "# Biofeedback\nBiofeedback training.\n## Usage\nWear.",
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompts_path = root / "prompts.json"
            prompts_path.write_text(json.dumps(prompts), encoding="utf-8")
            bundle = root / "bundle"
            prepared = prepare(
                bundle, suite="development", prompts=prompts_path,
                attempt_root=root / "attempts",
            )
            server, attempt = create_recording_server(
                bundle, root / "attempts", github_delegate=github, search_interval=0,
            )
            async with Client(server) as client:
                searched = _payload(await client.call_tool("muse_search", {
                    "request": request, "mode": "deep",
                }))
                search_id = searched["search_id"]
                await client.call_tool("muse_observe", {"search_id": search_id})
                iterated = _payload(await client.call_tool("muse_iterate", {
                    "search_id": search_id,
                    "hypothesis": {
                        "decision": "continue", "reason": "test a neighboring mechanism",
                        "add_exploration_directions": [{
                            "term": "biofeedback", "reason": "may transfer",
                            "evidence": "host_hypothesis", "request_anchor": "focus improvement",
                        }],
                        "strategies": ["keyword"],
                    },
                }))
                selection = []
                for candidate in iterated["candidates"]:
                    evidence = next(
                        (
                            item for item in candidate.get("evidence") or []
                            if item.get("kind") in {"readme_excerpt", "mechanism_match"}
                        ),
                        None,
                    )
                    if evidence is None:
                        continue
                    facts = evidence.get("facts") or {}
                    if evidence.get("kind") == "mechanism_match":
                        match = (facts.get("mechanisms") or [{}])[0]
                        text = str(match.get("text") or "")
                        mechanism = str(match.get("name") or "test mechanism")
                    else:
                        text = str(facts.get("text") or "")
                        mechanism = "test mechanism"
                    quote = next(
                        (line.strip() for line in text.splitlines() if line.strip()), "",
                    )
                    if not quote:
                        continue
                    selection.append({
                        "repo": candidate["full_name"],
                        "rationale": "Recorded evidence fixture",
                        "mechanism_label": mechanism,
                        "source_term": quote.split()[0],
                        "quote": quote,
                        "evidence_ids": [evidence["id"]],
                        "boundary_role": "edge",
                    })
                self.assertTrue(selection)
                # A live Agent that mis-copies a quote must be able to recover through
                # MCP, not just in-process: the first rank accepts nothing and leaves
                # the session open, the corrected resubmission completes the case.
                broken = [
                    {**item, "quote": "Text absent from every recorded source"}
                    for item in selection
                ]
                rejected = _payload(await client.call_tool("muse_rank", {
                    "search_id": search_id, "selection": broken,
                }))
                self.assertEqual(rejected["items"], [])
                self.assertEqual(rejected["next_action"], "rank")
                self.assertEqual(
                    rejected["rejected_items"][0]["reasons"],
                    ["quote_not_verbatim_at_recorded_sha"],
                )
                ranked = _payload(await client.call_tool("muse_rank", {
                    "search_id": search_id, "selection": selection,
                }))
                self.assertEqual(ranked["next_action"], "done")
                self.assertTrue(ranked["items"])
            attempt.close()

            transcript = [
                json.loads(line) for line in attempt.transcript_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [item["tool"] for item in transcript],
                ["muse_search", "muse_observe", "muse_iterate", "muse_rank", "muse_rank"],
            )
            self.assertTrue(attempt.cassette_path.is_file())
            summary = summarize_attempt(
                attempt.path, mapping_path=Path(prepared["mapping"]), suite="development",
            )
            self.assertEqual(summary["classification"], "capture_complete")
            self.assertEqual(summary["classification_reason"], "artifacts_intact")
            self.assertGreaterEqual(summary["host_hypotheses_proposed"], 1)
            # The retry is visible in the shakedown telemetry rather than hidden.
            self.assertEqual(summary["selection"]["rank_calls"], 2)
            self.assertEqual(summary["selection"]["cases_ending_empty"], 0)
            self.assertEqual(
                summary["selection"]["rejection_reasons"],
                {"quote_not_verbatim_at_recorded_sha": len(selection)},
            )
            self.assertTrue((attempt.path / "manifest.json").is_file())
            with self.assertRaises(FileExistsError):
                summarize_attempt(
                    attempt.path, mapping_path=Path(prepared["mapping"]), suite="development",
                )

    async def test_proxy_rejects_non_fixture_search_and_records_error(self):
        from mcp import Client

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "bundle"
            prepare(bundle, attempt_root=root / "attempts")
            server, attempt = create_recording_server(
                bundle, root / "attempts", github_delegate=FrozenGitHub([]), search_interval=0,
            )
            async with Client(server) as client:
                result = await client.call_tool("muse_search", {
                    "request": {"request": "invented", "problem_concepts": ["invented"]},
                    "mode": "deep",
                })
                self.assertTrue(result.is_error)
            attempt.close()
            event = json.loads(attempt.transcript_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(event["tool"], "muse_search")
            self.assertEqual(event["error"]["type"], "ValueError")


if __name__ == "__main__":
    unittest.main()
