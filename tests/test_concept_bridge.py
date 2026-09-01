import json
import tempfile
import unittest

from muse_shroom.analyze import make_evidence, safe_readme
from muse_shroom.models import Concept, SearchRequest
from muse_shroom.search import SearchEngine, _compact_search_output, public_candidate
from muse_shroom.selection import (
    SHORTLIST_LIMIT, balanced_select, concept_coverage, covered_core_ids,
    lexical_concept_evidence, nongeneric_query_source, probe_select,
    score_candidates, shortlist_select,
)
from muse_shroom.storage import Store

from tests.helpers import FrozenGitHub, repo


def path(kind: str, query: str, position: int = 1, *, concept_id: str | None = None,
         term: str | None = None) -> dict:
    item = {"kind": "query", "query": query, "query_kind": kind, "position": position}
    if concept_id:
        item["concept_id"] = concept_id
    if term:
        item["term"] = term
    return item


class TrackingGitHub(FrozenGitHub):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.readmes_fetched: list[str] = []
        self.releases_fetched: list[str] = []

    def readme(self, full_name: str):
        self.readmes_fetched.append(full_name.lower())
        return super().readme(full_name)

    def latest_release(self, full_name: str):
        self.releases_fetched.append(full_name.lower())
        return super().latest_release(full_name)


class ConceptBridgeTests(unittest.TestCase):
    def test_chinese_concept_and_english_alias_are_one_group(self):
        group = Concept.from_value({"term": "自控", "aliases": ["self-control", "self regulation"]})
        english = Concept.from_value({"term": "self-control"})
        candidate = repo("owner/timer", 12, description="A self-control commitment timer")
        self.assertGreater(concept_coverage(candidate, [group]), 0)
        self.assertAlmostEqual(
            concept_coverage(candidate, [group]),
            concept_coverage(candidate, [english]),
        )

    def test_multiple_aliases_do_not_stack_scores(self):
        one = Concept.from_value({"term": "自控", "aliases": ["self-control"]})
        many = Concept.from_value({"term": "自控", "aliases": ["self-control", "self regulation"]})
        candidate = repo(
            "owner/timer", 12,
            description="self-control and self regulation for habit change",
        )
        self.assertAlmostEqual(concept_coverage(candidate, [one]), concept_coverage(candidate, [many]))
        self.assertLess(concept_coverage(candidate, [many]), 100.0)

    def test_same_concept_queries_are_capped_in_rrf(self):
        request = SearchRequest.from_dict({
            "request": "self-control focus",
            "core_concepts": ["self-control", "focus management"],
        })
        once = repo("owner/one", 10, description="self-control")
        once["discovery_paths"] = [
            path("core", "self-control in:readme", 1, concept_id="core:0", term="self-control"),
        ]
        many = repo("owner/many", 10, description="self-control")
        many["discovery_paths"] = [
            path("core", "self-control in:readme", 1, concept_id="core:0", term="self-control"),
            path("core", "self-control in:topics", 1, concept_id="core:0", term="self-control"),
            path("core", "self-control in:name,description", 1, concept_id="core:0", term="self-control"),
            path("core", "self-control in:name,description,topics", 1, concept_id="core:0", term="self-control"),
        ]
        two_concepts = repo("owner/two", 10, description="self-control focus")
        two_concepts["discovery_paths"] = [
            path("core", "self-control in:readme", 1, concept_id="core:0", term="self-control"),
            path("core", "focus management in:readme", 1, concept_id="core:1", term="focus management"),
        ]
        scored = {
            item["full_name"]: item["selection_score_components"]["rrf"]
            for item in score_candidates([once, many, two_concepts], request, enriched=False)
        }
        self.assertLess(scored["owner/many"], scored["owner/one"] * 2)
        self.assertGreater(scored["owner/two"], scored["owner/many"])

    def test_each_core_concept_group_gets_probe_slots(self):
        request = SearchRequest.from_dict({
            "request": "focus",
            "core_concepts": ["alpha signal", "beta signal", "gamma signal"],
        })
        crowded = []
        for index in range(12):
            item = repo(f"popular/alpha-{index}", 8000 - index, description="alpha signal helper")
            item["matched_kinds"] = ["core"]
            item["discovery_paths"] = [
                path("core", "alpha", index + 1, concept_id="core:0", term="alpha signal"),
            ]
            crowded.append(item)
        beta = repo("niche/beta-tool", 40, description="beta signal tracker")
        beta.update({
            "matched_kinds": ["core"],
            "discovery_paths": [path("core", "beta", 1, concept_id="core:1", term="beta signal")],
        })
        gamma = repo("niche/gamma-tool", 35, description="gamma signal tracker")
        gamma.update({
            "matched_kinds": ["core"],
            "discovery_paths": [path("core", "gamma", 1, concept_id="core:2", term="gamma signal")],
        })
        selected, _ = probe_select(crowded + [beta, gamma], request)
        names = {item["full_name"] for item in selected}
        self.assertIn("niche/beta-tool", names)
        self.assertIn("niche/gamma-tool", names)

    def test_rank_six_low_exposure_candidate_can_enter_probe(self):
        request = SearchRequest.from_dict({
            "request": "self-control app",
            "core_concepts": [{"term": "自控", "aliases": ["self-control"]}],
            "artifact_types": ["application"],
        })
        candidates = []
        for index in range(1, 11):
            stars = 40 if index == 6 else 50_000 - index
            description = (
                "self-control commitment device"
                if index == 6 else "self-control pomodoro tutorial"
            )
            item = repo(f"hit-{index}/repo", stars, description=description)
            item["matched_kinds"] = ["typed"]
            item["discovery_paths"] = [
                path("typed", '"自控" "app"', index, concept_id="core:0", term="自控"),
            ]
            candidates.append(item)
        for index in range(40):
            item = repo(f"viral-{index}/video", 80_000 - index, description="short video editor")
            item["matched_kinds"] = ["popular"]
            item["discovery_paths"] = [path("typed", "app tool", index + 1)]
            candidates.append(item)
        selected, _ = probe_select(candidates, request)
        names = {item["full_name"] for item in selected}
        self.assertEqual(len(selected), 30)
        self.assertIn("hit-6/repo", names)

    def test_high_star_without_concept_evidence_does_not_displace_alias_hit(self):
        request = SearchRequest.from_dict({
            "request": "focus",
            "core_concepts": [{"term": "自控", "aliases": ["self-control"]}],
        })
        alias_hit = repo("small/behavior", 80, description="self-control commitment device")
        alias_hit.update({
            "matched_kinds": ["core"],
            "discovery_paths": [
                path("core", "self-control", 4, concept_id="core:0", term="self-control"),
            ],
            "readme": "# Behavior\nA self-control commitment device with a distinct mechanism.\n## Installation\n## Usage",
        })
        popular = repo("huge/tutorial", 180_000, description="short video flowchart tutorial")
        popular.update({
            "matched_kinds": ["typed"],
            "discovery_paths": [path("typed", "app tutorial", 1)],
            "readme": "# Tutorial\nMake short videos and flowcharts.\n## Installation\n## Usage",
        })
        selected, _ = probe_select([popular, alias_hit], request)
        self.assertIn("small/behavior", {item["full_name"] for item in selected})
        shortlist, _ = shortlist_select([popular, alias_hit], request)
        names = {item["full_name"] for item in shortlist}
        self.assertIn("small/behavior", names)

    def test_low_stars_alone_are_not_a_gem_reason(self):
        request = SearchRequest.from_dict({"request": "focus", "core_concepts": ["self-control"]})
        obscure = repo("tiny/unrelated", 2, description="hello world")
        obscure.update({"matched_kinds": [], "discovery_paths": [], "readme": "# Hello\nUnrelated."})
        scored = score_candidates([obscure], request, enriched=True)[0]
        self.assertNotIn("gems", scored["selection_lanes"])
        self.assertNotIn("concept_bridge", scored["selection_lanes"])
        self.assertFalse(lexical_concept_evidence(scored))

    def test_confirmation_only_candidate_uses_adjacent_shortlist_lane(self):
        request = SearchRequest.from_dict({"request": "focus", "core_concepts": ["focus"]})
        candidate = repo("probe/decision-log", 12, description="unrelated utility")
        candidate.update({
            "matched_kinds": ["confirmation"],
            "discovery_paths": [
                path(
                    "confirmation", '"decision log" "focus"',
                    concept_id="confirmation:decision log", term="decision log",
                ),
            ],
        })

        scored = score_candidates([candidate], request, enriched=False)[0]

        self.assertTrue(nongeneric_query_source(candidate))
        self.assertEqual(scored["matched_kinds"], ["confirmation"])
        self.assertIn("adjacent", scored["selection_lanes"])
        self.assertNotIn("core", scored["selection_lanes"])

    def test_shortlist_is_at_most_twelve_and_release_is_shortlist_only(self):
        core = [repo(f"core/tool-{index}", index + 1, description="music AI creator tool") for index in range(10)]
        omr = [repo(f"omr/tool-{index}", index + 11, description="optical music recognition") for index in range(10)]
        adjacent = [repo(f"ambient/tool-{index}", index + 21, description="ambient sound generator") for index in range(10)]
        readmes = {
            item["full_name"]: (
                f"# {item['full_name']}\nA documented creative tool.\n## Installation\nInstall it.\n## Usage\nRun it."
            )
            for item in core + omr + adjacent
        }
        github = TrackingGitHub([
            ("ambient sound", adjacent),
            ("optical music recognition", omr),
            ("music AI", core),
        ], readmes=readmes)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            result = SearchEngine(store, github).search(SearchRequest.from_dict({
                "request": "music AI inspiration",
                "core_concepts": ["music AI", "optical music recognition"],
                "adjacent_concepts": ["ambient sound"],
                "artifact_types": ["application"],
            }), "quick")
            store.close()
        self.assertLessEqual(result["assessment_candidate_count"], SHORTLIST_LIMIT)
        self.assertLessEqual(len(set(github.readmes_fetched)), 30)
        self.assertLessEqual(len(set(github.releases_fetched)), SHORTLIST_LIMIT)
        shortlist = {item["full_name"].lower() for item in result["candidates"]}
        self.assertTrue(set(github.releases_fetched) <= shortlist)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode("utf-8")), 30_000)
        self.assertLessEqual(result["coverage"]["output_bytes"], 30_000)
        self.assertTrue(all(item.get("concept_matches") is not None for item in result["candidates"]))
        self.assertTrue(all(item.get("selection_reason") for item in result["candidates"]))
        self.assertGreater(result["coverage"]["core_concepts_covered"], 0)
        self.assertEqual(
            result["coverage"]["core_concepts_covered"],
            len(covered_core_ids(result["candidates"], SearchRequest.from_dict({
                "request": "music AI inspiration",
                "core_concepts": ["music AI", "optical music recognition"],
                "adjacent_concepts": ["ambient sound"],
                "artifact_types": ["application"],
            }))),
        )

    def test_query_only_hits_do_not_count_as_core_coverage(self):
        request = SearchRequest.from_dict({
            "request": "focus",
            "core_concepts": [{"term": "自控", "aliases": ["self-control"]}],
        })
        query_only = repo("owner/query-hit", 10, description="unrelated video editor")
        query_only["concept_matches"] = [{
            "concept_id": "core:0", "label": "自控", "matched_alias": "自控",
            "source": "query", "score": 87,
        }]
        lexical = repo("owner/lexical", 10, description="A self-control commitment timer")
        lexical["concept_matches"] = [{
            "concept_id": "core:0", "label": "自控", "matched_alias": "self-control",
            "source": "description", "score": 80,
        }]
        self.assertEqual(covered_core_ids([query_only], request), set())
        self.assertEqual(covered_core_ids([lexical], request), {"core:0"})

    def test_compact_prefers_concept_match_excerpt_over_overview(self):
        item = repo("owner/protocol", 12, description="self-control trainer")
        item["readme_sha"] = "abc123"
        readme, truncated = safe_readme(
            "# Trainer\nEnglish overview without the mechanism.\n\n"
            "A delay-chain protocol for self-control training.\n"
            "## Installation\nInstall it.\n## Usage\nRun it."
        )
        item["evidence"] = make_evidence(
            item, readme, truncated, concept_terms=["self-control", "自控"],
        )
        public = public_candidate(item)
        excerpts = [entry for entry in public["evidence"] if entry["kind"] == "readme_excerpt"]
        self.assertTrue(excerpts)
        self.assertEqual(excerpts[0]["facts"]["snippet_type"], "concept_match")
        self.assertIn("self-control", excerpts[0]["facts"]["text"].casefold())
        self.assertNotIn("English overview", excerpts[0]["facts"]["text"])
        self.assertEqual(len(excerpts), 2)
        self.assertIn(excerpts[1]["facts"]["snippet_type"], {"usage", "installation"})
        self.assertFalse(any(item.get("kind") == "github_release" for item in public["evidence"]))
        self.assertLessEqual(len(excerpts[0]["facts"]["text"]), 240)
        self.assertEqual(excerpts[0]["facts"]["line_start"], 4)
        self.assertGreaterEqual(excerpts[0]["facts"]["line_end"], 4)
        self.assertEqual(excerpts[0]["facts"]["sha"], "abc123")
        self.assertEqual(
            excerpts[0]["facts"]["parent_evidence_id"],
            "repo:owner/protocol:readme",
        )

    def test_concept_match_excerpt_keeps_two_hundred_plus_characters(self):
        item = repo("owner/protocol", 12, description="self-control trainer")
        paragraph = (
            "Based on chained-delay protocol theory this self-control trainer uses a sacred-seat "
            "rule, a precedent-binding rule, and linear delay to help people keep a habit chain. "
        ) * 4
        readme, truncated = safe_readme(f"# Trainer\n{paragraph}\n## Usage\nRun the desktop app after install.")
        item["latest_release"] = {
            "tag_name": "v1.0.0", "published_at": "2026-08-01T00:00:00Z",
            "html_url": "https://github.com/owner/protocol/releases/tag/v1.0.0",
        }
        item["evidence"] = make_evidence(
            item, readme, truncated, concept_terms=["self-control"],
        )
        public = public_candidate(item)
        excerpts = [entry for entry in public["evidence"] if entry["kind"] == "readme_excerpt"]
        self.assertGreaterEqual(len(excerpts[0]["facts"]["text"]), 220)
        self.assertLessEqual(len(excerpts[0]["facts"]["text"]), 240)
        self.assertNotIn("github_release", {item.get("kind") for item in public["evidence"]})
        self.assertEqual(public["latest_release"]["evidence_id"], "repo:owner/protocol:release")

    def test_compact_output_enforces_exact_thirty_kilobyte_wire_limit(self):
        candidates = []
        readmes = {}
        for index in range(30):
            item = repo(
                f"owner-{index}/long-repository-name-{index}", index + 1,
                description="音乐人工智能创作工具" * 30,
                topics=[f"long-topic-name-{topic}" for topic in range(10)],
            )
            candidates.append(item)
            readmes[item["full_name"]] = (
                "# 项目\n" + "音乐人工智能创作流程与灵感探索证据内容" * 30
                + "\n## Usage\n" + "运行这个工具生成音乐并保留创作上下文" * 30
            )
        github = TrackingGitHub([("音乐人工智能", candidates)], readmes=readmes)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            result = SearchEngine(store, github).search(SearchRequest.from_dict({
                "request": "音乐人工智能",
                "core_concepts": ["音乐人工智能"],
            }), "quick")
            store.close()
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(encoded), 30_000)
        self.assertEqual(result["coverage"]["output_bytes"], len(encoded))
        self.assertTrue(result["coverage"]["output_compacted"])
        self.assertTrue(all(item["evidence"] for item in result["candidates"]))
        for item in result["candidates"]:
            excerpts = [value for value in item["evidence"] if value.get("kind") == "readme_excerpt"]
            self.assertTrue(excerpts)
            self.assertGreaterEqual(len(excerpts[0]["facts"]["text"]), 200)
            self.assertIn("line_start", excerpts[0]["facts"])
            self.assertIn("line_end", excerpts[0]["facts"])
            self.assertIn("parent_evidence_id", excerpts[0]["facts"])

    def test_compact_output_falls_back_to_assessment_grade_candidates(self):
        candidates = []
        for index in range(12):
            candidates.append({
                "full_name": f"owner/project-{index}",
                "html_url": f"https://github.com/owner/project-{index}",
                "description": "A concrete inspiration candidate " * 12,
                "topics": [f"topic-{value}" for value in range(8)],
                "stargazers_count": index,
                "archived": False,
                "selection_reason": {"reason": "unbounded explanation " * 240},
                "mechanisms": [{"name": "commitment device"}],
                "evidence": [{
                    "id": f"repo:owner/project-{index}:readme:overview",
                    "kind": "readme_excerpt",
                    "facts": {
                        "snippet_type": "overview", "line_start": 1, "line_end": 3,
                        "parent_evidence_id": f"repo:owner/project-{index}:readme",
                        "text": "Evidence-backed overview " * 10,
                    },
                }],
            })
        output = {"coverage": {}, "candidates": candidates}

        _compact_search_output(output)

        encoded = json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(encoded), 30_000)
        self.assertEqual(output["coverage"]["output_bytes"], len(encoded))
        self.assertEqual(output["coverage"]["output_truncation_level"], "assessment_minimal")
        self.assertTrue(output["coverage"]["candidate_details_truncated"])
        self.assertTrue(all(item.get("full_name") and item.get("evidence") for item in candidates))
        self.assertTrue(all("selection_reason" not in item for item in candidates))

    def test_badge_only_overview_is_not_used_as_concept_evidence(self):
        item = repo("owner/badges", 12, description="self-control trainer")
        readme, truncated = safe_readme(
            "# Tool\n[English](README.md) | [中文](README.zh.md)\n\n"
            "A self-control trainer with a delay-chain protocol for habit change.\n## Usage\nRun it."
        )
        item["evidence"] = make_evidence(item, readme, truncated, concept_terms=["self-control"])
        public = public_candidate(item)
        excerpts = [entry for entry in public["evidence"] if entry["kind"] == "readme_excerpt"]
        self.assertEqual(excerpts[0]["facts"]["snippet_type"], "concept_match")
        self.assertNotIn("English", excerpts[0]["facts"]["text"])

    def test_selection_reason_does_not_claim_an_unverified_mechanism(self):
        request = SearchRequest.from_dict({
            "request": "自控训练", "core_concepts": [{"term": "自控", "aliases": ["self-control"]}],
        })
        item = repo("small/behavior", 80, description="self-control commitment device")
        item.update({
            "matched_kinds": ["core"],
            "readme": "# Behavior\nA self-control commitment device.\n## Usage\nRun it.",
            "discovery_paths": [path(
                "core", "self-control", 1, concept_id="core:0", term="self-control",
            )],
        })
        selected, _ = balanced_select(
            [item], request, {"concept_bridge": 1}, enriched=True,
        )
        reason = selected[0]["selection_reason"]["reason"]
        self.assertIn("self-control", reason)
        self.assertIn("自控", reason)
        self.assertNotIn("distinct", reason.casefold())
        self.assertNotIn("mechanism", reason.casefold())

    def test_expand_shares_one_readme_budget_across_both_enrich_phases(self):
        popular = repo("tools/popular", 10000, description="Broad tool")
        hidden = [repo(f"tools/hidden-{index}", 3 + index, description="Specific review skill")
                  for index in range(8)]
        related = repo("tools/related", 4, description="Linked helper")
        readmes = {
            "tools/popular": "# Popular\n## Installation",
            "tools/related": "# Related\n## Installation",
            **{item["full_name"]: f"# {item['full_name']}\nSpecific review skill.\n## Installation\n## Usage"
               for item in hidden},
        }
        github = TrackingGitHub(
            [("broad", [popular]), ("specific symptom", hidden),
             ('"tools/popular" in:readme', [related])],
            readmes=readmes,
        )
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            engine = SearchEngine(store, github, enrich_limit=3, relation_budget=2)
            first = engine.search(SearchRequest.from_dict({
                "request": "broad search", "core_concepts": ["broad"]
            }), "deep")
            before = len(github.readmes_fetched)
            engine.expand(first["search_id"], {"concepts": ["specific symptom"], "seeds": ["tools/popular"]})
            store.close()
        self.assertLessEqual(len(github.readmes_fetched) - before, 3)

    def test_same_request_reuses_the_same_search(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            github = TrackingGitHub(
                [("music", [repo("tools/useful", 10, description="Useful music tool")])],
                readmes={"tools/useful": "# Useful\n## Installation\nRun it."},
            )
            engine = SearchEngine(store, github)
            request = SearchRequest.from_dict({"request": "music", "core_concepts": ["music"]})
            first = engine.search(request, "quick")
            releases = list(github.releases_fetched)
            second = engine.search(request, "quick")
            self.assertTrue(second["reused"])
            self.assertEqual(second["search_id"], first["search_id"])
            self.assertEqual(github.releases_fetched, releases)
            for output in (first, second):
                encoded = json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self.assertEqual(output["coverage"]["output_bytes"], len(encoded))
            store.close()


if __name__ == "__main__":
    unittest.main()
