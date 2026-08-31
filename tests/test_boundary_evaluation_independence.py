import gzip
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluation.check_boundary_leakage import find_leaks
from evaluation.version_worker import deterministic_hypothesis
from muse_shroom.boundary import DISCOVERY_PHRASE_HINTS


ROOT = Path(__file__).resolve().parents[1]


class BoundaryEvaluationIndependenceTests(unittest.TestCase):
    def test_holdout_expected_terms_and_aliases_are_not_phrase_hints(self):
        self.assertEqual(find_leaks(), [])

    def test_leakage_checker_rejects_normalized_holdout_alias(self):
        leaked = sorted(DISCOVERY_PHRASE_HINTS)[0]
        payload = {
            "schema_version": 2,
            "cases": [{
                "id": "leak",
                "acceptable_new_mechanisms": [
                    {"id": "expected", "term": "safe term", "aliases": [leaked.upper()]},
                ],
                "cross_mechanism_directions": [],
            }],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "holdout.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            leaks = find_leaks(path)
        self.assertEqual(leaks[0]["normalized"], leaked.casefold())

    def test_holdout_golden_is_not_visible_to_agentic_policy(self):
        observation = {
            "discovered_term_evidence": [{
                "term": "observed-only", "kind": "candidate_mechanism",
                "confidence": 0.8, "support_count": 1,
            }],
            "unexplored_directions": ["request-only"],
        }
        with patch.object(Path, "read_text", side_effect=AssertionError("policy read a file")):
            hypothesis = deterministic_hypothesis(observation, set())
        self.assertEqual(hypothesis["target_direction"], "observed-only")

    def test_holdout_prompts_do_not_contain_expected_mechanisms(self):
        prompts = json.loads(
            (ROOT / "evaluation" / "holdout" / "boundary-prompts.json").read_text(encoding="utf-8")
        )
        golden = json.loads(
            (ROOT / "evaluation" / "holdout" / "boundary-golden-cases.json").read_text(encoding="utf-8")
        )
        prompt_by_id = {item["id"]: json.dumps(item["request"], ensure_ascii=False).casefold()
                        for item in prompts["prompts"]}
        for case in golden["cases"]:
            request_text = prompt_by_id[case["id"]]
            for field in ("acceptable_new_mechanisms", "cross_mechanism_directions"):
                for concept in case[field]:
                    for term in [concept["term"], *(concept.get("aliases") or [])]:
                        self.assertNotIn(str(term).casefold(), request_text)

    def test_fresh_fixture_replay_is_offline(self):
        cassette = ROOT / "evaluation" / "fixtures" / "boundary-ci-v1.json.gz"
        with gzip.open(cassette, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["schema_version"], 1)
        self.assertTrue(payload["calls"])
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run([
                sys.executable, str(ROOT / "evaluation" / "run_boundary_eval.py"),
                "replay", "--ci", "--output-dir", temporary,
            ], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn('"suite": "ci"', completed.stdout)


if __name__ == "__main__":
    unittest.main()
