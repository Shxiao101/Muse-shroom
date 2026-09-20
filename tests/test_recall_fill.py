"""How a page of results becomes the candidate pool.

The queries take turns rank by rank, and a pool that fills up stops taking new
candidates without discarding the queries that have not been read yet.
"""
import tempfile
import unittest

from muse_shroom.models import SearchRequest
from muse_shroom.search import SearchEngine
from muse_shroom.storage import Store

from tests.helpers import FrozenGitHub, repo


def query_paths(candidate: dict) -> list[dict]:
    return [path for path in candidate.get("discovery_paths") or [] if path.get("kind") == "query"]


class RecallFillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.addCleanup(self.store.close)

    def _three_term_search(self, candidate_limit: int) -> tuple[FrozenGitHub, str, dict]:
        github = FrozenGitHub([
            (term, [repo(f"{term}/app-{index}", 500 - index, description=f"{term} tool")
                    for index in range(1, 4)])
            for term in ("alpha", "beta", "gamma")
        ])
        engine = SearchEngine(self.store, github, candidate_limit=candidate_limit)
        result = engine.search(SearchRequest.from_dict({
            "request": "alpha beta gamma",
            "problem_concepts": [{"term": "alpha"}, {"term": "beta"}, {"term": "gamma"}],
        }), "quick")
        return github, result["search_id"], self.store.load_search(result["search_id"])

    def test_every_query_lands_its_first_result_before_any_query_lands_a_second(self):
        # Filling query by query let the first terms spend the whole pool, so a later
        # term went unsearched in effect although its call had been paid for.
        _github, _search_id, session = self._three_term_search(candidate_limit=3)
        self.assertEqual(
            sorted(item["full_name"] for item in session["candidates"]),
            ["alpha/app-1", "beta/app-1", "gamma/app-1"],
        )

    def test_a_full_pool_still_records_every_query_it_paid_for(self):
        github, search_id, _session = self._three_term_search(candidate_limit=3)
        history = [row for row in self.store.query_history(search_id) if not row["skipped"]]
        self.assertEqual(len(history), github.request_counts["search"])
        self.assertTrue(all(row["result_count"] == 3 for row in history))

    def test_a_full_pool_still_records_later_queries_against_the_candidates_it_holds(self):
        # A repository several queries return is more strongly recalled than one a
        # single query returns; that evidence must not depend on when the pool filled.
        _github, _search_id, session = self._three_term_search(candidate_limit=3)
        alpha = next(item for item in session["candidates"] if item["full_name"] == "alpha/app-1")
        self.assertGreater(len(query_paths(alpha)), 1)


if __name__ == "__main__":
    unittest.main()
