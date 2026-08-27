import unittest

from repo_radar.analyze import github_links, safe_readme
from repo_radar.models import ContractError, SearchRequest
from repo_radar.queries import build_queries


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

    def test_github_links_are_deduplicated_and_self_link_removed(self):
        links = github_links(
            "https://github.com/a/one https://github.com/a/one https://github.com/b/two)", "a/one"
        )
        self.assertEqual(links, ["b/two"])


if __name__ == "__main__":
    unittest.main()
