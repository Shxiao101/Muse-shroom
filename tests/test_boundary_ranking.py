import tempfile
import unittest

from muse_shroom.ranking import rank_search
from muse_shroom.storage import Store

from tests.test_ranking import candidate, selected


class AgentOwnedBoundaryTests(unittest.TestCase):
    def test_new_mechanisms_are_an_ordered_set_difference(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                search_id = "s"
                store.create_search(
                    search_id,
                    {"request": "focus", "problem_concepts": ["focus"]},
                    "deep",
                )
                first = candidate("owner/first", 1, "Blocks distracting apps.")
                second = candidate("owner/second", 2, "Locks distracting sites.")
                store.save_candidate(search_id, first)
                store.save_candidate(search_id, second)
                store.save_boundary_snapshot(
                    search_id,
                    "search",
                    {
                        "recalled_mechanisms": [],
                        "presented_mechanisms": ["commitment device"],
                        "mechanism_origins": {},
                    },
                )

                result = rank_search(store, search_id, [
                    selected(first, label="commitment device"),
                    selected(second, label="commitment device"),
                ])
            finally:
                store.close()

        self.assertEqual(result["items"][0]["new_mechanisms"], [])
        self.assertEqual(result["items"][1]["new_mechanisms"], [])
        self.assertEqual(result["newly_presented_mechanisms"], [])


if __name__ == "__main__":
    unittest.main()
