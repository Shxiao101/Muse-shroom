import json
import unittest
from pathlib import Path

from evaluation.score_ab import summarize


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


if __name__ == "__main__":
    unittest.main()
