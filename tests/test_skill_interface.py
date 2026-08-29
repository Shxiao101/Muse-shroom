import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "github-inspiration-discovery" / "SKILL.md"
REFERENCES = ROOT / "skills" / "github-inspiration-discovery" / "references"


class SkillInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.skill = SKILL.read_text(encoding="utf-8")

    def test_skill_uses_split_contracts_and_iterate_not_expand(self):
        for name in (
            "request-contract.md", "hypothesis-contract.md",
            "assessment-contract.md", "result-contract.md",
        ):
            self.assertTrue((REFERENCES / name).exists(), name)
            self.assertIn(name, self.skill)
        self.assertNotIn("mechanism_novelty", self.skill)
        self.assertNotIn("mechanism_novelty", (REFERENCES / "assessment-contract.md").read_text(encoding="utf-8"))
        self.assertIn("muse-shroom iterate", self.skill)
        self.assertNotIn("muse-shroom expand", self.skill)
        self.assertNotIn("muse_expand", self.skill)
        self.assertIn("muse_search", self.skill)
        self.assertIn("muse_observe", self.skill)
        self.assertIn("muse_iterate", self.skill)
        self.assertIn("muse_rank", self.skill)
        self.assertIn("Otherwise use the CLI", self.skill)
        self.assertIn("Do not change the search strategy", self.skill)
        self.assertIn("stop.signals", self.skill)
        self.assertIn("should_stop", self.skill)
        self.assertIn("还有吗", self.skill)
        self.assertIn("muse-shroom observe", self.skill)
        self.assertIn("can_iterate", self.skill)
        self.assertIn("next_action", self.skill)
        self.assertIn("display_order", (REFERENCES / "result-contract.md").read_text(encoding="utf-8"))
        hypothesis = (REFERENCES / "hypothesis-contract.md").read_text(encoding="utf-8")
        self.assertIn("rejected_directions", hypothesis)
        self.assertIn("不要 timer", hypothesis)
        self.assertIn("DOM focus", hypothesis)
        self.assertIn("行为干预", hypothesis)
        self.assertIn("observe --search-id", hypothesis)
        self.assertIn("muse_observe", hypothesis)
        self.assertIn("can_iterate", hypothesis)

    def test_hypothesis_contract_keeps_expand_as_compatibility_only(self):
        hypothesis = (REFERENCES / "hypothesis-contract.md").read_text(encoding="utf-8")
        self.assertIn("iterate", hypothesis)
        self.assertIn("compatibility", hypothesis.lower())

    def test_readme_documents_mcp_test_install_and_command(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('mcp = ["mcp>=2.0.0,<3"]', pyproject)
        self.assertIn('test = ["mcp>=2.0.0,<3"]', pyproject)
        self.assertIn('python -m pip install -e ".[mcp]"', readme)
        self.assertIn("python -m unittest tests.test_mcp -v", readme)
        self.assertIn('python -m pip install -e ".[test]"', readme)


if __name__ == "__main__":
    unittest.main()
