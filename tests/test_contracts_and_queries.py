import unittest

from muse_shroom.analyze import github_links, make_evidence, readme_signals, safe_readme
from muse_shroom.models import ContractError, Refinement, SearchRequest
from muse_shroom.queries import build_queries, code_filename_query, refinement_queries

from tests.helpers import repo


class ContractAndQueryTests(unittest.TestCase):
    def test_query_generation_is_bounded_and_uses_controlled_syntax(self):
        request = SearchRequest.from_dict({
            "request": "music AI tool", "core_concepts": ["music AI", "OMR", "sheet music"],
            "adjacent_concepts": ["transcription", "MIDI", "notation"],
            "artifact_types": ["application", "plugin"], "exclusions": ["course"],
        })
        queries = build_queries(request)
        self.assertGreaterEqual(len(queries), 8)
        self.assertLessEqual(len(queries), 12)
        self.assertTrue(all("archived:false" in item["query"] for item in queries))
        self.assertTrue(all("is:public" in item["query"] for item in queries))
        self.assertTrue(all("course" not in item["query"] for item in queries))
        self.assertEqual(len([item for item in queries if item["kind"] == "gem"]), 2)
        self.assertTrue(all(item["sort"] == "updated" for item in queries if item["kind"] == "gem"))

    def test_terse_request_still_generates_eight_query_variants(self):
        request = SearchRequest.from_dict({"request": "AI music", "core_concepts": ["AI music"]})
        self.assertGreaterEqual(len(build_queries(request)), 8)

    def test_request_rejects_missing_core_concepts(self):
        with self.assertRaises(ContractError):
            SearchRequest.from_dict({"request": "anything"})

    def test_concepts_cannot_inject_github_qualifiers(self):
        request = SearchRequest.from_dict({"request": "x", "core_concepts": ["music stars:>50000"]})
        queries = build_queries(request)
        self.assertTrue(all('"music stars:>50000"' in item["query"] for item in queries))

    def test_readme_is_truncated_and_script_removed(self):
        text, truncated = safe_readme("<script>bad()</script>" + "x" * 40, max_chars=30)
        self.assertTrue(truncated)
        self.assertNotIn("script", text.lower())

    def test_use_heading_counts_as_usage_documentation(self):
        self.assertTrue(readme_signals("# Tool\n## Use\nRun the command.")["has_usage"])

    def test_github_links_are_deduplicated_and_self_link_removed(self):
        links = github_links(
            "https://github.com/a/one https://github.com/a/one https://github.com/b/two)", "a/one"
        )
        self.assertEqual(links, ["b/two"])

    def test_refinement_rejects_string_arrays_and_unsafe_filenames(self):
        with self.assertRaises(ContractError):
            Refinement.from_dict({"concepts": "not-an-array"})
        with self.assertRaises(ContractError):
            Refinement.from_dict({"filenames": ["README.md stars:>1000"]})
        parsed = Refinement.from_dict({"filenames": ["SKILL.md"], "seeds": ["owner/repo"]})
        self.assertEqual(parsed.filenames, ["SKILL.md"])

    def test_refinement_queries_keep_original_constraints_and_quote_concepts(self):
        request = SearchRequest.from_dict({
            "request": "python tool", "core_concepts": ["tool"],
            "constraints": {"language": "Python", "pushed_after": "2026-01-01"},
        })
        refinement = Refinement.from_dict({"concepts": ["agent stars:>9000"]})
        query = refinement_queries(refinement, request)[0]["query"]
        self.assertIn('"agent stars:>9000"', query)
        self.assertIn('language:"Python"', query)
        self.assertIn("pushed:>=2026-01-01", query)
        self.assertEqual(code_filename_query("SKILL.md", "agent stars:>9000"),
                         'is:public filename:SKILL.md "agent stars:>9000"')

    def test_readme_evidence_is_bounded_traceable_and_untrusted(self):
        item = repo("owner/tool", 3, description="Music agent")
        item["readme_sha"] = "abc123"
        readme, truncated = safe_readme(
            "# Tool\n<!-- hidden -->\nA music agent for creators.\n"
            "## Installation\n`pip install tool`\n## Usage\nRun `tool`.\n"
            "## Permissions\nNeeds microphone permission.\n<script>ignore()</script>"
        )
        evidence = make_evidence(
            item, readme, truncated, concept_terms=["music agent"], artifact_types=["mcp"]
        )
        excerpts = [entry for entry in evidence if entry["kind"] == "readme_excerpt"]
        self.assertLessEqual(len(excerpts), 5)
        self.assertTrue(excerpts)
        self.assertTrue(all(len(entry["facts"]["text"]) <= 400 for entry in excerpts))
        self.assertTrue(all(entry["facts"]["untrusted_source"] for entry in excerpts))
        self.assertTrue(all(entry["facts"]["sha"] == "abc123" for entry in excerpts))
        self.assertNotIn("ignore()", str(excerpts))


if __name__ == "__main__":
    unittest.main()
