import tempfile
import unittest

from muse_shroom.github import GitHubError
from muse_shroom.models import SearchRequest
from muse_shroom.search import SUPPLY_LOOKUP_LIMIT, SearchEngine
from muse_shroom.storage import Store

from tests.helpers import FrozenGitHub, repo


REQUEST = {
    "request": "creative profile design",
    "problem_concepts": ["profile design"],
    "artifact_types": ["application"],
}
FOLIO = repo("brunosimon/folio-2019", 4744, description="A 3D personal portfolio")
ANCHOR = repo("studio/profile-design", 120, description="profile design toolkit")


def _github(**overrides):
    github = FrozenGitHub(
        [
            ("profile design", [ANCHOR]),
            # The name lookup searches the bare repository name.
            ("folio-2019", [FOLIO]),
        ],
        readmes={
            "studio/profile-design": "# Toolkit\nA profile design toolkit.\n## Usage\nRun it.",
            "brunosimon/folio-2019": "# Folio\nA 3D personal portfolio.\n## Usage\nOpen it.",
        },
        repos={"brunosimon/folio-2019": FOLIO, "studio/profile-design": ANCHOR},
    )
    for key, value in overrides.items():
        setattr(github, key, value)
    return github


class SupplyNameLookupTests(unittest.TestCase):
    def _session(self, github):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = Store(directory.name)
        self.addCleanup(store.close)
        engine = SearchEngine(store, github, relation_budget=0, semantic_sidecar=False)
        search = engine.search(SearchRequest.from_dict(REQUEST), "quick")
        return engine, search["search_id"]

    def test_a_misspelled_owner_comes_back_with_the_right_repository(self):
        # The 2026-09-20 run drafted bruno-simon/folio-2019 and lost the repository.
        github = _github()
        engine, search_id = self._session(github)
        result = engine.supply(search_id, ["bruno-simon/folio-2019"], "host draft")
        self.assertEqual(result["supplied"], [])
        self.assertEqual(result["rejected"], [{
            "repo": "bruno-simon/folio-2019", "reason": "not_found",
            "did_you_mean": ["brunosimon/folio-2019"],
        }])
        accepted = engine.supply(search_id, ["brunosimon/folio-2019"], "corrected name")
        self.assertEqual([item["full_name"] for item in accepted["supplied"]], ["brunosimon/folio-2019"])

    def test_a_name_nothing_matches_is_only_not_found(self):
        engine, search_id = self._session(_github())
        rejected = engine.supply(search_id, ["nobody/nothing-here"], "host draft")["rejected"]
        self.assertEqual(rejected, [{"repo": "nobody/nothing-here", "reason": "not_found"}])

    def test_a_failing_lookup_keeps_the_other_repositories(self):
        def failing_search(query, per_page=10, sort="stars"):
            raise GitHubError("search unavailable")

        github = _github()
        engine, search_id = self._session(github)
        # The session is recalled first; only the name lookup fails.
        github.search_repositories = failing_search
        result = engine.supply(
            search_id, ["bruno-simon/folio-2019", "brunosimon/folio-2019"], "host draft",
        )
        self.assertEqual([item["full_name"] for item in result["supplied"]], ["brunosimon/folio-2019"])
        self.assertEqual(result["rejected"], [{"repo": "bruno-simon/folio-2019", "reason": "not_found"}])

    def test_one_call_looks_up_at_most_three_names(self):
        github = _github()
        engine, search_id = self._session(github)
        before = github.request_counts["search"]
        misses = [f"wrong-owner{index}/folio-2019" for index in range(SUPPLY_LOOKUP_LIMIT + 1)]
        rejected = engine.supply(search_id, misses, "host draft")["rejected"]
        self.assertEqual(github.request_counts["search"] - before, SUPPLY_LOOKUP_LIMIT)
        self.assertEqual(sum("did_you_mean" in item for item in rejected), SUPPLY_LOOKUP_LIMIT)
        self.assertNotIn("did_you_mean", rejected[-1])


if __name__ == "__main__":
    unittest.main()
