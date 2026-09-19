import copy
import json
import tempfile
import unittest
from pathlib import Path

from muse_shroom.agent_view import ShownCandidates, rank_view, session_view
from muse_shroom.mcp_schema import MUSE_ITERATE_DESCRIPTION
from muse_shroom.services import MuseCore
from muse_shroom.storage import Store

from tests.helpers import FrozenGitHub, repo
from tests.test_sidecar import REQUEST, _selection


SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "muse-shroom"


def _term_evidence():
    return [{
        "term": "pacing", "kind": "source_term", "request_anchored": True,
        "mechanism_anchored": False, "support_count": 1,
        "sources": [{
            "repo": "labs/pacing", "source_field": "topics",
            "evidence_id": "repo:labs/pacing:metadata", "evidence_text": "pacing",
            "confidence": 0.95, "evidence_relevance_score": 58,
            "evidence_relevance_reason": "core_context", "core_use_case": True,
            "request_anchored": True, "mechanism_anchored": False,
            "retrieval_stage": "discovery",
        }],
    }]


def _session_output():
    boundary = {
        "recalled_mechanisms": ["pomodoro"], "discovered_terms": ["pacing"],
        "discovered_term_evidence": _term_evidence(),
    }
    delta = {"new_mechanisms": ["pomodoro"]}
    return {
        "search_id": "s1", "candidates": [], "coverage": {"output_bytes": 0},
        "boundary": copy.deepcopy(boundary), "boundary_delta": dict(delta),
        "semantic_hypotheses": [{"id": "h1", "status": "evidence_found"}],
        "sidecar_metrics": {"semantic_queries_executed": 2},
        "observation": {
            "boundary": copy.deepcopy(boundary), "boundary_delta": dict(delta),
            "semantic_hypotheses": [{"id": "h1", "status": "evidence_found"}],
            "sidecar_metrics": {"semantic_queries_executed": 1},
            "discovered_term_evidence": _term_evidence(),
        },
    }


def _candidate(name, **extra):
    return {"full_name": name, "description": f"{name} description", **extra}


class SessionViewTests(unittest.TestCase):
    def test_each_fact_is_stated_once(self):
        output = _session_output()
        original = copy.deepcopy(output)
        view = session_view(output)
        self.assertEqual(output, original)
        observation = view["observation"]
        self.assertNotIn("boundary", observation)
        self.assertEqual(view["boundary"]["recalled_mechanisms"], ["pomodoro"])
        self.assertNotIn("discovered_term_evidence", view["boundary"])
        self.assertNotIn("boundary_delta", view)
        self.assertNotIn("semantic_hypotheses", view)
        self.assertEqual(observation["boundary_delta"], {"new_mechanisms": ["pomodoro"]})
        # Values that differ are not duplicates; both stay.
        self.assertEqual(view["sidecar_metrics"], {"semantic_queries_executed": 2})
        self.assertEqual(observation["sidecar_metrics"], {"semantic_queries_executed": 1})

    def test_term_evidence_keeps_what_hypotheses_cite(self):
        term = session_view(_session_output())["observation"]["discovered_term_evidence"][0]
        self.assertEqual(term["term"], "pacing")
        self.assertTrue(term["request_anchored"])
        self.assertEqual(term["sources"], [{
            "repo": "labs/pacing", "source_field": "topics",
            "evidence_id": "repo:labs/pacing:metadata", "evidence_text": "pacing",
            "request_anchored": True,
        }])

    def test_output_bytes_describe_the_view(self):
        view = session_view(_session_output())
        size = len(json.dumps(view, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        self.assertEqual(view["coverage"]["output_bytes"], size)


class RankViewTests(unittest.TestCase):
    def test_rank_view_keeps_presentation_and_citation_fields(self):
        output = {
            "next_action": "done",
            "items": [{
                "repo": "labs/pacing", "rationale": "why", "boundary_role": "edge",
                "mechanism_label": "pacing", "new_mechanisms": ["pacing"],
                "source": "muse_recall", "quote": "pacing", "source_term": "pacing",
                "evidence_ids": ["repo:labs/pacing:metadata"],
                "verification": {"evidence_id": "repo:labs/pacing:metadata"},
                "evidence": [{"id": "repo:labs/pacing:metadata"}],
                "discovery_paths": [{"kind": "query"}],
            }],
            "rejected_items": [{"index": 1, "reasons": ["quote_not_found"], "evidence_ids_checked": ["x"]}],
            "boundary": {"presented_mechanisms": ["pacing"], "discovered_term_evidence": _term_evidence()},
            "sidecar_metrics": {"validated_presented": 1, "base_ledger": [{"stage": "search"}]},
        }
        view = rank_view(output)
        item = view["items"][0]
        self.assertNotIn("evidence", item)
        self.assertNotIn("discovery_paths", item)
        for key in ("repo", "rationale", "boundary_role", "new_mechanisms", "source",
                    "quote", "evidence_ids", "verification"):
            self.assertIn(key, item)
        self.assertEqual(view["rejected_items"], output["rejected_items"])
        self.assertNotIn("discovered_term_evidence", view["boundary"])
        self.assertEqual(view["sidecar_metrics"], {"validated_presented": 1})
        self.assertIn("evidence", output["items"][0])


class ShownCandidatesTests(unittest.TestCase):
    def test_only_new_or_changed_candidates_are_resent(self):
        shown = ShownCandidates()
        shown.reset("s1", [_candidate("a/one"), _candidate("b/two")])
        view = shown.omit_unchanged({"search_id": "s1", "candidates": [
            _candidate("a/one"), _candidate("b/two", description="new evidence"), _candidate("c/three"),
        ]})
        self.assertEqual([item["full_name"] for item in view["candidates"]], ["b/two", "c/three"])
        self.assertEqual(view["unchanged_candidates"], ["a/one"])
        again = shown.omit_unchanged({"search_id": "s1", "candidates": [
            _candidate("b/two", description="new evidence"), _candidate("c/three"),
        ]})
        self.assertEqual(again["candidates"], [])
        self.assertEqual(again["unchanged_candidates"], ["b/two", "c/three"])

    def test_a_new_server_or_another_search_gets_the_full_list(self):
        candidates = [_candidate("a/one")]
        shown = ShownCandidates()
        shown.reset("s1", candidates)
        other = shown.omit_unchanged({"search_id": "s2", "candidates": list(candidates)})
        self.assertEqual(other["candidates"], candidates)
        self.assertNotIn("unchanged_candidates", other)
        fresh = ShownCandidates().omit_unchanged({"search_id": "s1", "candidates": list(candidates)})
        self.assertEqual(fresh["candidates"], candidates)


class MuseCoreViewTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data_dir = directory.name
        github = FrozenGitHub(
            [
                ("focus", [repo("tools/timer", 300, description="pomodoro timer for focus")]),
                ("pomodoro", [repo("tools/timer", 300, description="pomodoro timer for focus")]),
                ("distraction", [repo("labs/blocker", 60, description="distraction blocking for focus")]),
            ],
            readmes={
                "tools/timer": "# Timer\nA pomodoro timer for focus.\n## Usage\nStart it.",
                "labs/blocker": "# Blocker\nDistraction blocking for focus.\n## Usage\nBlock.",
            },
        )
        self.core = MuseCore(data_dir=self.data_dir, github=github)

    def test_iterate_names_candidates_the_server_already_returned(self):
        search = self.core.search(REQUEST, "deep")
        first = {item["full_name"] for item in search["candidates"]}
        self.assertTrue(first)
        iterated = self.core.iterate(search["search_id"], {
            "decision": "continue", "reason": "cover distraction blocking",
            "target_mechanism": "distraction blocking", "strategies": ["keyword"],
        })
        unchanged = set(iterated.get("unchanged_candidates") or [])
        self.assertTrue(unchanged)
        self.assertLessEqual(unchanged, first)
        self.assertFalse(unchanged & {item["full_name"] for item in iterated["candidates"]})
        self.assertNotIn("boundary", iterated["observation"])

    def test_rank_returns_the_view_and_saves_the_full_record(self):
        search = self.core.search(REQUEST, "quick")
        candidate = next(item for item in search["candidates"] if item["full_name"] == "tools/timer")
        evidence_id = next(
            item["id"] for item in candidate["evidence"] if item.get("kind") == "readme_excerpt"
        )
        ranked = self.core.rank(search["search_id"], {
            "selection": [_selection(candidate, "pomodoro", evidence_id)],
        })
        self.assertEqual(ranked["next_action"], "done")
        self.assertNotIn("evidence", ranked["items"][0])
        self.assertIn("verification", ranked["items"][0])
        store = Store(self.data_dir)
        try:
            saved = store.get_ranking(search["search_id"])
        finally:
            store.close()
        self.assertIn("evidence", saved["items"][0])


class ContractTextTests(unittest.TestCase):
    def test_every_channel_names_unchanged_candidates(self):
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        result = (SKILL_DIR / "references" / "result-contract.md").read_text(encoding="utf-8")
        for text in (skill, result, MUSE_ITERATE_DESCRIPTION):
            self.assertIn("unchanged_candidates", text)
        self.assertIn("Rank does not repeat the full evidence or discovery paths", result)


if __name__ == "__main__":
    unittest.main()
