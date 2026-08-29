import tempfile
import unittest

from muse_shroom.models import SearchRequest
from muse_shroom.search import SearchEngine
from muse_shroom.storage import Store

from tests.helpers import FrozenGitHub, repo


class GoldenDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_music_ai_finds_homr_gui_through_reverse_readme(self):
        homr = repo("liebharc/homr", 2100, description="Optical music recognition", topics=["omr", "music"])
        gui = repo("Quackone/homr_gui", 45, description="A friendly GUI for homr", topics=["omr", "gui"])
        github = FrozenGitHub(
            [("music AI", [homr]), ('"liebharc/homr" in:readme', [gui])],
            readmes={
                "liebharc/homr": "# homr\n## Installation\n## Usage\nOMR command line\nhttps://github.com/missing/dead"
            },
        )
        engine = SearchEngine(self.store, github, enrich_limit=30, relation_budget=20)
        first = engine.search(SearchRequest.from_dict({
            "request": "音乐+AI小工具", "core_concepts": ["music AI", "optical music recognition"],
            "adjacent_concepts": ["OMR GUI"], "artifact_types": ["application"]
        }), "deep")
        self.assertIsNone(first["incomplete_phase"])
        self.assertEqual(first["next_action"], "expand")
        result = engine.expand(first["search_id"], {
            "concepts": ["optical music recognition"], "anchors": ["homr"], "seeds": ["liebharc/homr"]
        })
        target = next(item for item in result["candidates"] if item["full_name"].lower() == "quackone/homr_gui")
        self.assertTrue(any(path.get("relation") == "reverse_readme" for path in target["discovery_paths"]))
        stored = self.store.get_candidate("Quackone/homr_gui", result["search_id"])
        self.assertTrue(any(item["kind"] == "discovery_relation" for item in stored["evidence"]))

    def test_lilith_mod_keeps_mod_and_adjacent_directions(self):
        mod = repo("community/lilith-modpack", 120, description="Mods for The NOexistenceN of Lilith", topics=["mod"])
        music = repo("tools/lilith-audio", 18, description="Extract and remix game audio", topics=["audio"])
        capture = repo("tools/game-window-capture", 340, description="Capture game windows", topics=["capture"])
        pet = repo("tools/codex-desktop-pet", 12, description="A Codex desktop pet", topics=["agent", "pet"])
        github = FrozenGitHub([
            ("The NOexistenceN of Lilith", [mod]),
            ("game audio", [music]),
            ("window capture", [capture]),
            ("codex desktop pet", [pet]),
        ])
        result = SearchEngine(self.store, github).search(SearchRequest.from_dict({
            "request": "Lilith mod与相关灵感", "core_concepts": ["The NOexistenceN of Lilith"],
            "adjacent_concepts": ["game audio", "window capture", "codex desktop pet"],
            "artifact_types": ["mod"], "exploration_level": 1.0
        }), "quick")
        names = {item["full_name"] for item in result["candidates"]}
        self.assertIn("community/lilith-modpack", names)
        adjacent_pool = {
            "tools/lilith-audio", "tools/game-window-capture", "tools/codex-desktop-pet"
        }
        adjacent_names = {
            item["full_name"] for item in result["candidates"] if "adjacent" in item.get("matched_kinds", [])
        }
        self.assertGreaterEqual(len(adjacent_names & adjacent_pool), 2)
        self.assertLessEqual(sum(name.startswith("tools/") for name in names), 2)

    def test_archived_and_explicitly_excluded_results_are_removed(self):
        active = repo("tools/useful", 10, description="Useful music tool")
        archived = repo("tools/old", 900, description="Old music tool", archived=True)
        course = repo("tools/course", 500, description="Music AI course")
        github = FrozenGitHub([("music", [active, archived, course])], readmes={
            "tools/useful": "# Useful\n## Installation", "tools/old": "# Old", "tools/course": "# Course"
        })
        result = SearchEngine(self.store, github).search(SearchRequest.from_dict({
            "request": "music tool", "core_concepts": ["music"], "exclusions": ["course"]
        }), "quick")
        self.assertEqual([item["full_name"] for item in result["candidates"]], ["tools/useful"])

    def test_search_output_omits_readme_but_snapshot_retains_it(self):
        candidate = repo("tools/useful", 10, description="Useful music tool")
        github = FrozenGitHub([("music", [candidate])], readmes={
            "tools/useful": "# Useful\n## Installation\nRun it locally."
        })
        result = SearchEngine(self.store, github).search(SearchRequest.from_dict({
            "request": "music tool", "core_concepts": ["music"]
        }), "quick")

        self.assertNotIn("readme", result["candidates"][0])
        self.assertNotIn("owner", result["candidates"][0])
        self.assertEqual(result["candidates"][0]["description"], "Useful music tool")
        stored = self.store.get_candidate("tools/useful", result["search_id"])
        self.assertEqual(stored["readme"], "# Useful\n## Installation\nRun it locally.")

    def test_expand_enriches_new_low_star_refinement_before_old_candidates(self):
        popular = repo("tools/popular", 10000, description="Broad tool")
        target = repo("tools/hidden-gem", 3, description="Specific review skill")
        github = FrozenGitHub(
            [("broad", [popular]), ("specific symptom", [target])],
            readmes={
                "tools/popular": "# Popular\n## Installation",
                "tools/hidden-gem": "# Hidden Gem\n## Installation\n## Usage",
            },
        )
        engine = SearchEngine(self.store, github, enrich_limit=1, relation_budget=1)
        first = engine.search(SearchRequest.from_dict({
            "request": "broad search", "core_concepts": ["broad"]
        }), "deep")
        result = engine.expand(first["search_id"], {"concepts": ["specific symptom"]})

        hidden = next(item for item in result["candidates"] if item["full_name"] == "tools/hidden-gem")
        self.assertTrue(any(item.get("kind") == "readme_excerpt" for item in hidden["evidence"]))
        stored = self.store.get_candidate("tools/hidden-gem", result["search_id"])
        self.assertIn("readme", stored)

    def test_repeated_recall_does_not_duplicate_discovery_paths(self):
        candidate = repo("tools/review", 3, description="Specific review skill")
        github = FrozenGitHub([("specific symptom", [candidate])])
        engine = SearchEngine(self.store, github, relation_budget=1)
        first = engine.search(SearchRequest.from_dict({
            "request": "specific", "core_concepts": ["specific symptom"]
        }), "deep")
        refinement = {"concepts": ["specific symptom"]}
        engine.expand(first["search_id"], refinement)
        result = engine.expand(first["search_id"], refinement)

        found = self.store.get_candidate("tools/review", result["search_id"])
        self.assertIsNotNone(found)
        identities = {
            (
                path.get("kind"), path.get("query"), path.get("query_kind"),
                path.get("relation"), path.get("from"),
            )
            for path in found["discovery_paths"]
        }
        self.assertEqual(len(found["discovery_paths"]), len(identities))
        self.assertEqual(len(found["matched_kinds"]), len(set(found["matched_kinds"])))


if __name__ == "__main__":
    unittest.main()
