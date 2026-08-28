import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout

from muse_shroom.cli import main
from muse_shroom.models import ContractError, SearchRequest
from muse_shroom.ranking import _percentiles, rank_search
from muse_shroom.search import SearchEngine
from muse_shroom.selection import balanced_select
from muse_shroom.storage import Store

from tests.helpers import FrozenGitHub, repo


def path(kind: str, query: str, position: int = 1) -> dict:
    return {"kind": "query", "query": query, "query_kind": kind, "position": position}


class V03QualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_relevant_low_star_candidate_beats_unrelated_high_star_candidate(self):
        request = SearchRequest.from_dict({"request": "music agent", "core_concepts": ["music agent"]})
        relevant = repo("small/music-agent", 2, description="A music agent for creators")
        relevant.update({"matched_kinds": ["core"], "discovery_paths": [path("core", "music agent")]})
        unrelated = repo("large/framework", 200000, description="General framework")
        unrelated.update({"matched_kinds": [], "discovery_paths": []})
        selected, _ = balanced_select([unrelated, relevant], request, {"core": 1}, enriched=False)
        self.assertEqual(selected[0]["full_name"], "small/music-agent")

    def test_schema_v2_caps_assessment_candidates_and_omits_raw_readme(self):
        core = [repo(f"core/tool-{index}", index + 1, description="music AI creator tool") for index in range(10)]
        omr = [repo(f"omr/tool-{index}", index + 11, description="optical music recognition") for index in range(10)]
        adjacent = [repo(f"ambient/tool-{index}", index + 21, description="ambient sound generator") for index in range(10)]
        readmes = {
            item["full_name"]: f"# {item['full_name']}\nA documented creative tool.\n## Installation\nInstall it.\n## Usage\nRun it."
            for item in core + omr + adjacent
        }
        github = FrozenGitHub([
            ("ambient sound", adjacent),
            ("optical music recognition", omr),
            ("music AI", core),
        ], readmes=readmes)
        result = SearchEngine(self.store, github).search(SearchRequest.from_dict({
            "request": "music AI inspiration", "core_concepts": ["music AI", "optical music recognition"],
            "adjacent_concepts": ["ambient sound"], "artifact_types": ["application"],
        }), "quick")
        self.assertEqual(result["schema_version"], 2)
        self.assertGreater(result["candidate_count"], 24)
        self.assertLessEqual(result["assessment_candidate_count"], 24)
        self.assertTrue(all("readme" not in item for item in result["candidates"]))
        self.assertTrue(all(item.get("selected_for_assessment") for item in result["candidates"]))
        self.assertLess(len(json.dumps(result, ensure_ascii=False)), 80_000)

    def test_relation_expansion_respects_original_constraints(self):
        seed = repo("seed/tool", 100, description="python music tool", language="Python")
        good = repo("related/good", 5, description="python music helper", language="Python",
                    pushed_at="2026-08-01T00:00:00Z")
        wrong_language = repo("related/js", 5, description="music helper", language="JavaScript")
        too_old = repo("related/old", 5, description="python music helper", language="Python",
                       pushed_at="2024-01-01T00:00:00Z")
        github = FrozenGitHub([
            ('"seed/tool" in:readme', [good, wrong_language, too_old]),
            ("python music", [seed]),
        ], readmes={"seed/tool": "# Seed\nPython music.", "related/good": "# Good\nPython music helper."})
        engine = SearchEngine(self.store, github, relation_budget=4)
        first = engine.search(SearchRequest.from_dict({
            "request": "python music", "core_concepts": ["python music"],
            "constraints": {"language": "Python", "pushed_after": "2026-01-01"},
        }), "deep")
        result = engine.expand(first["search_id"], {"seeds": ["seed/tool"]})
        names = {item["full_name"] for item in result["candidates"]}
        self.assertIn("related/good", names)
        self.assertNotIn("related/js", names)
        self.assertNotIn("related/old", names)

    def test_failed_relationship_requests_consume_budget(self):
        seed = repo("seed/tool", 10, description="music tool")
        github = FrozenGitHub([("music", [seed])], readmes={
            "seed/tool": "# Seed\nhttps://github.com/missing/one\nhttps://github.com/missing/two\nhttps://github.com/missing/three"
        })
        engine = SearchEngine(self.store, github, relation_budget=2)
        first = engine.search(SearchRequest.from_dict({
            "request": "music", "core_concepts": ["music"]
        }), "deep")
        result = engine.expand(first["search_id"], {"seeds": ["seed/tool"]})
        self.assertEqual(result["coverage"]["relation_calls"], 2)
        self.assertEqual(result["incomplete_phase"], "relationship_budget_reached")

    def test_refinement_exclusions_remove_old_persisted_candidates(self):
        useful = repo("tools/useful", 5, description="music helper")
        unwanted = repo("tools/course", 5, description="music course")
        github = FrozenGitHub([("music", [useful, unwanted])], readmes={
            "tools/useful": "# Useful\nMusic helper.", "tools/course": "# Course\nMusic course.",
        })
        engine = SearchEngine(self.store, github, relation_budget=1)
        first = engine.search(SearchRequest.from_dict({
            "request": "music", "core_concepts": ["music"]
        }), "deep")
        engine.expand(first["search_id"], {"exclude": ["course"]})
        persisted = {item["full_name"] for item in self.store.load_search(first["search_id"])["candidates"]}
        self.assertEqual(persisted, {"tools/useful"})

    def test_popularity_uses_full_recalled_cohort_and_ties_share_percentile(self):
        self.assertEqual(_percentiles([repo("a/one", 10), repo("b/two", 10)])["a/one"],
                         _percentiles([repo("a/one", 10), repo("b/two", 10)])["b/two"])
        search_id = "cohort"
        self.store.create_search(search_id, {"request": "x", "core_concepts": ["x"]}, "quick")
        items = [repo("a/low", 10), repo("b/mid", 100), repo("c/high", 1000)]
        for item in items:
            item["matched_kinds"] = ["core"]
            item["discovery_paths"] = [path("core", "x")]
            item["selection_score_components"] = {"evidence_completeness": 80}
            item["evidence"] = [
                {"id": f"repo:{item['full_name']}:metadata", "kind": "github_metadata", "facts": {"license": "MIT"}},
                {"id": f"repo:{item['full_name']}:readme", "kind": "readme", "facts": {"has_install": True, "has_usage": True}},
                {"id": f"repo:{item['full_name']}:readme:overview", "kind": "readme_excerpt", "facts": {"text": "Useful tool"}},
            ]
            self.store.save_candidate(search_id, item)
        assessment = {
            "repo": "b/mid", "relevance": 90, "uniqueness": 80, "usability": 80,
            "difficulty": "easy", "use_case": "Useful tool", "category": "tool", "artifact_type": "application",
            "reasons": [{"text": "Useful tool", "evidence_ids": ["repo:b/mid:readme:overview"]}],
            "risks": [{"text": "Check setup", "evidence_ids": ["repo:b/mid:metadata"]}],
        }
        result = rank_search(self.store, search_id, [assessment])
        returned = [item for bucket in result["buckets"].values() for item in bucket]
        self.assertEqual(result["coverage"]["recalled"], 3)
        self.assertEqual(returned[0]["scores"]["components"]["popularity_percentile"], 50.0)

    def test_verified_use_case_requires_readme_excerpt(self):
        search_id = "no-readme"
        self.store.create_search(search_id, {"request": "x", "core_concepts": ["x"]}, "quick")
        item = repo("owner/no-readme", 1)
        item["evidence"] = [{"id": "repo:owner/no-readme:metadata", "kind": "github_metadata", "facts": {}}]
        self.store.save_candidate(search_id, item)
        assessment = {
            "repo": "owner/no-readme", "relevance": 80, "uniqueness": 80, "usability": 70,
            "difficulty": "unknown", "use_case": "Verified feature", "category": "x", "artifact_type": "unknown",
            "reasons": [{"text": "Feature", "evidence_ids": ["repo:owner/no-readme:metadata"]}],
            "risks": [{"text": "Unknown", "evidence_ids": ["repo:owner/no-readme:metadata"]}],
        }
        with self.assertRaises(ContractError):
            rank_search(self.store, search_id, [assessment])

    def test_candidates_and_inspect_never_return_raw_readme(self):
        search_id = "cli-view"
        self.store.create_search(search_id, {"request": "x", "core_concepts": ["x"]}, "quick")
        item = repo("owner/repo", 1)
        item.update({"readme": "UNTRUSTED RAW README", "selected_for_assessment": True, "evidence": []})
        self.store.save_candidate(search_id, item)
        self.store.close()
        for argv in (
            ["--data-dir", self.temp.name, "candidates", "--search-id", search_id],
            ["--data-dir", self.temp.name, "inspect", "owner/repo", "--search-id", search_id],
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(main(argv), 0)
            self.assertNotIn("UNTRUSTED RAW README", stdout.getvalue())
        self.store = Store(self.temp.name)


if __name__ == "__main__":
    unittest.main()
