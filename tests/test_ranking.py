import json
import tempfile
import unittest

from muse_shroom.models import ContractError, SearchRequest
from muse_shroom.ranking import rank_search
from muse_shroom.search import SearchEngine
from muse_shroom.storage import Store

from tests.helpers import FrozenGitHub, repo


def candidate(full_name, stars, category, *, adjacent=False, quality=True):
    item = repo(full_name, stars, description=f"{category} tool", topics=[category])
    item["matched_kinds"] = ["adjacent"] if adjacent else ["core"]
    item["discovery_paths"] = [{"kind": "query", "query": category}]
    item["evidence"] = [
        {"id": f"repo:{full_name.lower()}:metadata", "kind": "github_metadata", "source": item["html_url"],
         "facts": {"stars": stars, "license": "MIT", "topics": [category]}},
        {"id": f"repo:{full_name.lower()}:readme", "kind": "readme", "source": item["html_url"] + "#readme",
         "facts": {"has_install": quality, "has_usage": quality}},
    ]
    return item


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.search_id = "search-1"
        self.store.create_search(self.search_id, {"request": "Codex过度设计和审查"}, "deep")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _assessment(self, item, relevance=85, uniqueness=80, usability=80, difficulty="easy"):
        name = item["full_name"]
        return {
            "repo": name, "relevance": relevance, "uniqueness": uniqueness, "usability": usability,
            "difficulty": difficulty, "use_case": "Reduce unnecessary implementation complexity",
            "category": item["topics"][0], "artifact_type": "skill",
            "reasons": [{"text": "Documented workflow", "evidence_ids": [f"repo:{name.lower()}:readme"]}],
            "risks": [{"text": "Check scope before use", "evidence_ids": [f"repo:{name.lower()}:metadata"]}],
        }

    def test_occam_review_is_found_without_name_and_classified_as_gem(self):
        items = [
            candidate("mindorigin150/occam-review", 22, "minimal-review"),
            candidate("large/popular-review", 25000, "review"),
            candidate("tools/token-meter", 800, "cost", adjacent=True),
        ]
        self.store.db.execute("DELETE FROM searches WHERE id=?", (self.search_id,))
        self.store.db.commit()
        github = FrozenGitHub([
            ("token cost", [items[2]]),
            ("overengineering", [items[0], items[1]]),
        ], readmes={
            item["full_name"]: "# Tool\n## Installation\n## Usage\nDocumented workflow" for item in items
        })
        first = SearchEngine(self.store, github).search(SearchRequest.from_dict({
            "request": "Codex过度设计和重复审查", "core_concepts": ["overengineering", "code review minimalism"],
            "adjacent_concepts": ["token cost"], "artifact_types": ["skill"]
        }), "deep")
        self.assertNotIn("occam-review", json.dumps(first["candidates"][0].get("discovery_paths", [])))
        by_name = {item["full_name"].lower(): item for item in first["candidates"]}
        result = rank_search(self.store, first["search_id"], {
            "assessments": [self._assessment(by_name[item["full_name"].lower()]) for item in items]
        })
        gems = {item["repo"].lower() for item in result["buckets"]["gems"]}
        self.assertIn("mindorigin150/occam-review", gems)
        self.assertGreaterEqual(result["coverage"]["adjacent_share"], 0.2)

    def test_invalid_evidence_reference_fails(self):
        item = candidate("owner/repo", 10, "x")
        self.store.save_candidate(self.search_id, item)
        assessment = self._assessment(item)
        assessment["reasons"][0]["evidence_ids"] = ["made-up"]
        with self.assertRaises(ContractError):
            rank_search(self.store, self.search_id, [assessment])

    def test_low_quality_candidates_do_not_fill_buckets(self):
        item = candidate("owner/empty", 0, "empty", quality=False)
        item["evidence"] = [item["evidence"][0]]
        self.store.save_candidate(self.search_id, item)
        assessment = self._assessment(item, relevance=20, uniqueness=100, usability=10, difficulty="unknown")
        assessment["reasons"][0]["evidence_ids"] = [f"repo:{item['full_name'].lower()}:metadata"]
        result = rank_search(self.store, self.search_id, [assessment])
        self.assertEqual(result["coverage"]["returned"], 0)


if __name__ == "__main__":
    unittest.main()
