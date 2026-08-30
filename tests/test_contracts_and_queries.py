import unittest

from muse_shroom.analyze import github_links, make_evidence, readme_signals, safe_readme
from muse_shroom.models import (
    Assessment, ContractError, Refinement, SearchHypothesis, SearchRequest,
)
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
        queries = build_queries(request)
        self.assertGreaterEqual(len(queries), 8)
        surfaces = " ".join(item["query"] for item in queries)
        self.assertIn("in:name,description", surfaces)
        self.assertIn("in:topics", surfaces)
        self.assertIn("in:readme", surfaces)

    def test_generic_core_terms_do_not_become_isolated_queries(self):
        request = SearchRequest.from_dict({
            "request": "文章配图 Skill",
            "core_concepts": ["文章配图", "Skill"],
            "artifact_types": ["skill"],
        })
        queries = [item["query"] for item in build_queries(request)]
        joined = "\n".join(queries)
        self.assertTrue(any('"文章配图"' in query for query in queries))
        self.assertFalse(any(query.startswith('"Skill"') or query.startswith('"skill"') for query in queries))
        self.assertNotIn('"Skill"', joined)
        self.assertIn('"文章配图" "skill"', joined)

    def test_chinese_concepts_stay_intact_phrases(self):
        request = SearchRequest.from_dict({
            "request": "正文配图",
            "core_concepts": [{"term": "正文配图", "weight": 1.0}],
            "artifact_types": ["skill"],
        })
        joined = " ".join(item["query"] for item in build_queries(request))
        self.assertIn('"正文配图"', joined)
        self.assertNotIn('"正"', joined)
        self.assertNotIn('"文配图"', joined)

    def test_typed_query_skips_type_tokens_already_in_the_concept(self):
        request = SearchRequest.from_dict({
            "request": "agent skill",
            "core_concepts": ["writing skill"],
            "artifact_types": ["skill"],
        })
        joined = " ".join(item["query"] for item in build_queries(request))
        self.assertIn('"writing skill"', joined)
        self.assertNotIn('"writing skill" "skill"', joined)

    def test_concept_aliases_are_optional_and_capped(self):
        parsed = SearchRequest.from_dict({
            "request": "focus",
            "core_concepts": [{"term": "自控", "aliases": ["self-control", "self regulation"], "weight": 1.0}],
        })
        self.assertEqual(parsed.core_concepts[0].terms(), ["自控", "self-control", "self regulation"])
        queries = build_queries(parsed)
        self.assertLessEqual(len(queries), 12)
        self.assertTrue(all("concept_id" in item for item in queries))
        self.assertTrue(any(item.get("term") == "self-control" for item in queries))
        with self.assertRaises(ContractError):
            SearchRequest.from_dict({
                "request": "focus",
                "core_concepts": [{"term": "自控", "aliases": ["a", "b", "c", "d", "e"]}],
            })

    def test_query_plan_reserves_gem_and_adjacent_slots_when_aliases_exist(self):
        request = SearchRequest.from_dict({
            "request": "focus",
            "core_concepts": [
                {"term": "专注", "aliases": ["focus management"]},
                {"term": "自控", "aliases": ["self-control"]},
                {"term": "减少分心", "aliases": ["distraction blocking"]},
            ],
            "adjacent_concepts": [
                {"term": "拖延", "aliases": ["procrastination"]},
                {"term": "行为约束", "weight": 0.65},
                {"term": "commitment device", "weight": 0.6},
            ],
            "artifact_types": ["application"],
            "exploration_level": 0.6,
        })
        queries = build_queries(request)
        kinds = [item["kind"] for item in queries]
        terms = [item.get("term") for item in queries]
        self.assertLessEqual(len(queries), 12)
        self.assertEqual(kinds.count("gem"), 2)
        self.assertGreaterEqual(kinds.count("adjacent"), 2)
        self.assertTrue(any(term in {"self-control", "focus management", "distraction blocking"} for term in terms))
        self.assertTrue(any(item["kind"] == "core" and item.get("term") == "自控" for item in queries))
        self.assertTrue(any('"自控" "app"' in item["query"] for item in queries))

    def test_aliases_do_not_expand_the_query_budget(self):
        request = SearchRequest.from_dict({
            "request": "focus",
            "core_concepts": [
                {"term": "专注管理", "aliases": ["focus management", "deep work", "attention", "flow state"]},
                {"term": "自控", "aliases": ["self-control", "self regulation", "willpower", "impulse control"]},
                {"term": "减少分心", "aliases": ["distraction blocking", "focus mode", "website blocker", "app blocker"]},
            ],
            "adjacent_concepts": [
                {"term": "commitment device", "aliases": ["precommitment", "temptation bundling"]},
            ],
            "artifact_types": ["application"],
        })
        queries = build_queries(request)
        self.assertLessEqual(len(queries), 12)
        self.assertTrue(all(item.get("concept_id") for item in queries))

    def test_request_rejects_missing_core_concepts(self):
        with self.assertRaises(ContractError):
            SearchRequest.from_dict({"request": "anything"})

    def test_non_strict_search_request_still_ignores_unknown_fields(self):
        parsed = SearchRequest.from_dict({
            "request": "focus", "core_concepts": ["focus"], "query": "ignored by CLI",
        })
        self.assertTrue(parsed.legacy_schema)
        self.assertEqual(parsed.problem_concepts[0].term, "focus")

    def test_strict_search_request_rejects_unknown_fields(self):
        with self.assertRaises(ContractError) as raised:
            SearchRequest.from_dict(
                {"request": "focus", "problem_concepts": ["focus"], "query": "focus tools"},
                strict=True,
            )
        self.assertIn("query", str(raised.exception))
        self.assertIn("problem_concepts", str(raised.exception))

    def test_strict_search_request_accepts_v04_fields(self):
        parsed = SearchRequest.from_dict({
            "request": "focus",
            "problem_concepts": [{"term": "focus", "aliases": ["concentration"]}],
            "mechanisms": ["pomodoro"],
            "exploration_directions": ["commitment device"],
            "artifact_types": ["application", "plugin"],
            "constraints": {
                "language": "Python",
                "pushed_after": "2026-01-01",
                "include_archived": False,
                "min_stars": 1,
                "max_stars": 100,
            },
            "exclusions": ["awesome list"],
            "exploration_level": 0.6,
        }, strict=True)
        self.assertFalse(parsed.legacy_schema)
        self.assertEqual(parsed.problem_concepts[0].term, "focus")

    def test_strict_search_request_matches_published_types_and_enums(self):
        cases = [
            ({"request": 123, "problem_concepts": ["focus"]}, "request"),
            ({"request": "focus", "problem_concepts": ["focus"], "artifact_types": "application"}, "artifact_types"),
            ({"request": "focus", "problem_concepts": ["focus"], "artifact_types": ["banana"]}, "artifact_types"),
            ({
                "request": "focus", "problem_concepts": ["focus"],
                "constraints": {"include_archived": "false"},
            }, "include_archived"),
            ({"request": "focus", "problem_concepts": [{"term": 123}]}, "concept term"),
            ({"request": "focus", "problem_concepts": [{"term": "focus", "weight": "1"}]}, "concept weight"),
            ({"request": "focus", "problem_concepts": ["focus"], "exclusions": [123]}, "exclusions"),
        ]
        for payload, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(ContractError) as raised:
                    SearchRequest.from_dict(payload, strict=True)
                self.assertIn(expected, str(raised.exception))

    def test_strict_search_request_keeps_legacy_fields_explicit(self):
        parsed = SearchRequest.from_dict(
            {"request": "focus", "core_concepts": ["focus"], "adjacent_concepts": ["timer"]},
            strict=True,
        )
        self.assertTrue(parsed.legacy_schema)
        with self.assertRaises(ContractError) as raised:
            SearchRequest.from_dict({
                "request": "focus",
                "problem_concepts": ["focus"],
                "core_concepts": ["focus"],
            }, strict=True)
        self.assertIn("legacy", str(raised.exception))

    def test_strict_hypothesis_rejects_unknown_fields_and_requires_decision(self):
        with self.assertRaises(ContractError) as raised:
            SearchHypothesis.from_dict({
                "decision": "continue",
                "concepts": ["pomodoro"],
                "rationale": "guessed field",
                "mechanisms": ["pomodoro"],
            }, strict=True)
        message = str(raised.exception)
        self.assertIn("rationale", message)
        self.assertIn("mechanisms", message)
        with self.assertRaises(ContractError):
            SearchHypothesis.from_dict({"concepts": ["pomodoro"]}, strict=True)

    def test_strict_hypothesis_rejects_null_schema_values(self):
        with self.assertRaises(ContractError) as raised:
            SearchHypothesis.from_dict({
                "decision": "continue",
                "concepts": ["pomodoro"],
                "strategies": None,
            }, strict=True)
        self.assertIn("strategies", str(raised.exception))

        with self.assertRaises(ContractError) as raised:
            SearchHypothesis.from_dict({
                "decision": "continue",
                "add_exploration_directions": [{
                    "term": "biofeedback",
                    "source_iteration": None,
                }],
            }, strict=True)
        self.assertIn("source_iteration", str(raised.exception))

    def test_strict_assessment_rejects_missing_and_unknown_fields(self):
        evidence = {"repo:owner/tool:readme:overview": "readme_excerpt"}
        complete = {
            "repo": "owner/tool",
            "relevance": 80,
            "uniqueness": 70,
            "usability": 75,
            "difficulty": "unknown",
            "use_case": "unknown",
            "category": "focus",
            "artifact_type": "application",
            "reasons": [{"text": "documented", "evidence_ids": ["repo:owner/tool:readme:overview"]}],
            "risks": [],
        }
        parsed = Assessment.from_dict(complete, evidence, strict=True)
        self.assertEqual(parsed.use_case, "unknown")
        self.assertEqual(parsed.risks, [])
        missing = dict(complete)
        del missing["use_case"]
        with self.assertRaises(ContractError) as raised:
            Assessment.from_dict(missing, evidence, strict=True)
        self.assertIn("use_case", str(raised.exception))
        with self.assertRaises(ContractError) as raised:
            Assessment.from_dict({**complete, "fit": 9, "caveats": "x"}, evidence, strict=True)
        self.assertIn("fit", str(raised.exception))
        no_reasons = dict(complete)
        no_reasons["reasons"] = []
        with self.assertRaises(ContractError) as raised:
            Assessment.from_dict(no_reasons, evidence, strict=True)
        self.assertIn("reasons", str(raised.exception))

    def test_strict_assessment_matches_published_types_and_enums(self):
        evidence = {"repo:owner/tool:metadata": "metadata"}
        complete = {
            "repo": "owner/tool",
            "relevance": 80,
            "uniqueness": 70,
            "usability": 75,
            "difficulty": "unknown",
            "use_case": "unknown",
            "category": "focus",
            "artifact_type": "application",
            "reasons": [{"text": "metadata", "evidence_ids": ["repo:owner/tool:metadata"]}],
            "risks": [],
        }
        cases = [
            ({"artifact_type": "banana"}, "artifact_type"),
            ({"difficulty": "trivial"}, "difficulty"),
            ({"relevance": "80"}, "relevance"),
            ({"use_case": 123}, "use_case"),
            ({"category": 123}, "category"),
            ({"reasons": [{"text": "metadata", "evidence_ids": "repo:owner/tool:metadata"}]}, "evidence_ids"),
            ({"reasons": [{"text": 123, "evidence_ids": ["repo:owner/tool:metadata"]}]}, "text"),
        ]
        for patch, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(ContractError) as raised:
                    Assessment.from_dict({**complete, **patch}, evidence, strict=True)
                self.assertIn(expected, str(raised.exception))

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
