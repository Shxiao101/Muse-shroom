import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "github-inspiration-discovery" / "SKILL.md"
REFERENCES = ROOT / "skills" / "github-inspiration-discovery" / "references"


class SkillInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.skill = SKILL.read_text(encoding="utf-8")

    def test_skill_is_opt_in_and_does_not_auto_trigger(self):
        # The frontmatter description is the trigger surface a host matches against, so
        # the gate has to live there, not only in the body.
        head = self.skill.split("---")[1]
        self.assertIn("description:", head)
        self.assertIn("Use ONLY when the user explicitly asks", head)
        self.assertIn("Do NOT use it when the user merely asks to find", head)
        # A general request for GitHub projects must not read as a request for this Skill.
        self.assertIn("This Skill is opt-in", self.skill)
        self.assertIn("is not by itself a request for this Skill", self.skill)
        self.assertIn("Being loaded is not the same as being asked for", self.skill)

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
        self.assertIn("Prefer Muse-shroom MCP over the CLI", self.skill)
        self.assertIn("Do not change the search strategy", self.skill)
        # The Skill may surface the Explorer link that rank returns, but must never
        # depend on the Explorer being there: it must not launch it, and it must
        # degrade when the field is missing.
        self.assertNotIn("muse-shroom explorer", self.skill)
        self.assertNotIn("run_explorer", self.skill)
        self.assertIn("explorer_url", self.skill)
        self.assertIn("Omit the line when `explorer_url` is absent", self.skill)
        self.assertIn("stop.signals", self.skill)
        self.assertIn("should_stop", self.skill)
        self.assertIn("还有吗", self.skill)
        self.assertIn("muse-shroom observe", self.skill)
        self.assertIn("can_iterate", self.skill)
        self.assertIn("next_action", self.skill)
        self.assertIn("primary retrieval path", self.skill)
        self.assertIn("generic Web search", self.skill)
        self.assertIn("use Muse-shroom", self.skill)
        self.assertIn("display_order", (REFERENCES / "result-contract.md").read_text(encoding="utf-8"))
        hypothesis = (REFERENCES / "hypothesis-contract.md").read_text(encoding="utf-8")
        self.assertIn("rejected_directions", hypothesis)
        self.assertIn("不要 timer", hypothesis)
        self.assertIn("DOM focus", hypothesis)
        self.assertIn("行为干预", hypothesis)
        self.assertIn("observe --search-id", hypothesis)
        self.assertIn("muse_observe", hypothesis)
        self.assertIn("can_iterate", hypothesis)
        self.assertIn("host_hypothesis", hypothesis)
        self.assertIn("request_anchor", hypothesis)
        self.assertNotIn("biofeedback", hypothesis)
        self.assertNotIn("commitment device", hypothesis)
        self.assertNotIn("digital wellbeing", hypothesis)

    def test_hypothesis_contract_keeps_expand_as_compatibility_only(self):
        hypothesis = (REFERENCES / "hypothesis-contract.md").read_text(encoding="utf-8")
        self.assertIn("iterate", hypothesis)
        self.assertIn("compatibility", hypothesis.lower())

    def test_presentation_contract_requires_explicit_mechanisms_and_accurate_counts(self):
        result_contract = (REFERENCES / "result-contract.md").read_text(encoding="utf-8")
        for text in (self.skill, result_contract):
            self.assertIn("New mechanism: <comma-separated new_mechanisms>", text)
            self.assertIn("New mechanism: none", text)
            self.assertIn(
                "Do not describe the number of returned projects as the number of distinct mechanisms",
                text,
            )
            self.assertIn("coverage.presented_mechanism_count", text)

    def test_intent_and_mode_are_combined_by_default(self):
        self.assertIn("one interaction by default", self.skill)
        self.assertIn("In the same message", self.skill)
        self.assertNotIn("Resolve two interactions separately", self.skill)

    def test_mcp_routing_discovers_deferred_tools_before_cli_fallback(self):
        self.assertIn("tool-search or deferred-tool discovery", self.skill)
        self.assertIn("The initial visible tool list alone is not evidence", self.skill)
        self.assertIn("call `muse_status`", self.skill)
        self.assertIn("Use the CLI only after", self.skill)
        self.assertIn("briefly tell the user the concrete reason", self.skill)

    def test_cli_auth_fallback_is_host_agnostic(self):
        self.assertIn("credential-bearing host/local user context", self.skill)
        self.assertIn("does not prove that the user's normal interactive context is unconfigured", self.skill)
        self.assertIn("current context cannot verify the user's credential", self.skill)
        self.assertIn("Do not hard-code a product-specific process", self.skill)
        self.assertIn("same credential-bearing context", self.skill)
        self.assertNotIn("codexsandboxoffline", self.skill)

    def test_deep_iterations_observe_between_calls_and_rank_is_terminal(self):
        hypothesis = (REFERENCES / "hypothesis-contract.md").read_text(encoding="utf-8")
        result_contract = (REFERENCES / "result-contract.md").read_text(encoding="utf-8")
        for text in (self.skill, hypothesis):
            self.assertIn(
                "Never chain two `muse_iterate` calls without an intervening `muse_observe`",
                text,
            )
            self.assertIn("initial deep search response", text)
        for text in (self.skill, result_contract):
            self.assertIn("terminal", text)
            self.assertIn("Do not call", text)
            # Terminality is conditional: a rank where every item failed verification
            # leaves the session open so the Agent can resubmit corrected quotes.
            self.assertIn("next_action", text)
            self.assertIn("rejected_items", text)
        self.assertIn("nothing was saved", result_contract)
        self.assertIn("call `muse_rank` again", result_contract)
        self.assertIn("Do not issue no-op shell commands", self.skill)
        self.assertIn("only before rank", self.skill)

    def test_result_keeps_one_order_and_discloses_coverage_gaps(self):
        result_contract = (REFERENCES / "result-contract.md").read_text(encoding="utf-8")
        for text in (self.skill, result_contract):
            self.assertIn("Do not append a second priority, recommendation, or best-first order", text)
            self.assertIn("boundary.unexplored_directions", text)
            self.assertIn("boundary.presented_mechanisms", text)

    def test_readme_documents_mcp_test_install_and_command(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('mcp = ["mcp>=2.0.0,<3"]', pyproject)
        self.assertIn('test = ["mcp>=2.0.0,<3"]', pyproject)
        self.assertIn('python -m pip install -e ".[mcp]"', readme)
        self.assertIn("python -m unittest tests.test_mcp -v", readme)
        self.assertIn('python -m pip install -e ".[test]"', readme)
        self.assertIn("muse-shroom explorer", readme)
        self.assertIn("--allow-remote", readme)


if __name__ == "__main__":
    unittest.main()
