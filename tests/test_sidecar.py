import json
import tempfile
import unittest
from pathlib import Path

from evaluation.host_eval import prepare, score_case
from muse_shroom.iteration import validate_hypothesis_evidence
from muse_shroom.models import ContractError, SearchHypothesis, SearchRequest
from muse_shroom.queries import hypothesis_queries
from muse_shroom.ranking import rank_search
from muse_shroom.search import SearchEngine
from muse_shroom.sidecar import (
    compare_base_ledgers, match_hypothesized_term, plan_sidecar_queries,
    public_hypothesis, split_additions, validate_host_hypotheses,
)
from muse_shroom.storage import Store

from tests.helpers import FrozenGitHub, repo


REQUEST = {
    "request": "提高专注力的工具",
    "problem_concepts": [{"term": "focus", "aliases": ["focus management"]}],
    "mechanisms": ["pomodoro", "distraction blocking"],
    "artifact_types": ["application"],
}


def _hypothesis(*terms_and_anchors):
    additions = [
        {
            "term": term,
            "request_anchor": anchor,
            "reason": "neighboring domain may transfer",
            "evidence": "host_hypothesis",
        }
        for term, anchor in terms_and_anchors
    ]
    return {
        "decision": "continue",
        "reason": "test sidecar",
        "add_exploration_directions": additions,
        "strategies": ["keyword"],
    }


def _selection(candidate, mechanism, evidence_id):
    evidence = next(item for item in candidate.get("evidence") or [] if item.get("id") == evidence_id)
    facts = evidence.get("facts") or {}
    if evidence.get("kind") == "readme_excerpt":
        text = str(facts.get("text") or "")
    elif evidence.get("kind") == "mechanism_match":
        text = str((facts.get("mechanisms") or [{}])[0].get("text") or "")
    else:
        raise AssertionError("selection fixture requires textual evidence")
    quote = next(part.strip() for part in text.splitlines() if part.strip())
    return {
        "repo": candidate["full_name"],
        "rationale": "Source-backed fixture selection",
        "mechanism_label": mechanism,
        "source_term": quote.split()[0],
        "quote": quote,
        "evidence_ids": [evidence_id],
        "boundary_role": "edge",
    }


class SidecarContractTests(unittest.TestCase):
    def test_host_hypothesis_is_split_from_ordinary_additions(self):
        hypothesis = SearchHypothesis.from_dict({
            "decision": "continue",
            "add_exploration_directions": [
                {"term": "observed-term", "evidence": "discovered_term"},
                {
                    "term": "neighboring-domain mechanism",
                    "request_anchor": "focus",
                    "evidence": "host_hypothesis",
                    "reason": "transfer",
                },
            ],
            "strategies": ["keyword"],
        }, strict=True)
        host, ordinary = split_additions(hypothesis)
        self.assertEqual([item.term for item in host], ["neighboring-domain mechanism"])
        self.assertEqual([item.term for item in ordinary], ["observed-term"])

    def test_host_hypothesis_does_not_enter_ordinary_queries(self):
        request = SearchRequest.from_dict(REQUEST)
        hypothesis = SearchHypothesis.from_dict(_hypothesis(("neighboring-domain mechanism", "focus")))
        executed, _skipped = hypothesis_queries(hypothesis, request, limit=6)
        self.assertFalse(any("neighboring-domain mechanism" in item["query"] for item in executed))

    def test_pure_and_bridge_queries_are_separately_quoted(self):
        request = SearchRequest.from_dict(REQUEST)
        records = [{
            "id": "h1:1:pacing",
            "term": "physiological pacing",
            "request_anchor": "focus",
            "queries": [],
        }]
        planned, skipped = plan_sidecar_queries(records, request, remaining_budget=4)
        self.assertEqual(skipped, [])
        self.assertEqual([item["kind"] for item in planned], ["semantic_pure", "semantic_bridge"])
        self.assertIn('"physiological pacing"', planned[0]["query"])
        self.assertIn('"physiological pacing"', planned[1]["query"])
        self.assertIn('"focus"', planned[1]["query"])
        self.assertNotIn('"physiological pacing focus"', planned[1]["query"])

    def test_unrelated_evidence_id_is_rejected(self):
        request = SearchRequest.from_dict(REQUEST)
        hypothesis = SearchHypothesis.from_dict({
            "decision": "continue",
            "target_direction": "unrelated leap",
            "add_exploration_directions": [{
                "term": "unrelated leap",
                "evidence": "repo:other/repo:readme:overview",
                "reason": "borrowed id",
            }],
            "strategies": ["keyword"],
        })
        boundary = {
            "discovered_terms": ["pomodoro"],
            "discovered_term_evidence": [{
                "term": "pomodoro",
                "promotable": True,
                "sources": [{"evidence_id": "repo:owner/timer:readme:overview"}],
            }],
        }
        with self.assertRaises(ContractError):
            validate_hypothesis_evidence(hypothesis, request, boundary, [
                {"evidence": [{"id": "repo:other/repo:readme:overview"}]},
            ])

    def test_host_hypothesis_requires_problem_anchor_and_iteration_window(self):
        request = SearchRequest.from_dict(REQUEST)
        hypothesis = SearchHypothesis.from_dict(_hypothesis(("neighboring-domain mechanism", "focus")))
        validate_host_hypotheses(hypothesis, request, iteration=1, existing=[])
        with self.assertRaises(ContractError):
            validate_host_hypotheses(hypothesis, request, iteration=3, existing=[])
        bad_anchor = SearchHypothesis.from_dict(_hypothesis(("neighboring-domain mechanism", "unrelated")))
        with self.assertRaises(ContractError):
            validate_host_hypotheses(bad_anchor, request, iteration=1, existing=[])
        existing = [{"term": "one"}, {"term": "two"}]
        with self.assertRaises(ContractError):
            validate_host_hypotheses(hypothesis, request, iteration=2, existing=existing)

    def test_term_owned_evidence_id_is_still_accepted(self):
        request = SearchRequest.from_dict(REQUEST)
        hypothesis = SearchHypothesis.from_dict({
            "decision": "continue",
            "target_direction": "pomodoro",
            "add_exploration_directions": [{
                "term": "pomodoro",
                "evidence": "repo:owner/timer:readme:overview",
                "reason": "owned id",
            }],
            "strategies": ["keyword"],
        })
        boundary = {
            "discovered_terms": ["pomodoro"],
            "discovered_term_evidence": [{
                "term": "pomodoro",
                "promotable": False,
                "sources": [{"evidence_id": "repo:owner/timer:readme:overview"}],
            }],
        }
        validate_hypothesis_evidence(hypothesis, request, boundary, [])


class SidecarSearchTests(unittest.TestCase):
    def _engine(self, github, **options):
        directory = tempfile.TemporaryDirectory()
        store = Store(directory.name)
        engine = SearchEngine(store, github, relation_budget=0, **options)

        def close() -> None:
            store.close()
            directory.cleanup()

        self.addCleanup(close)
        return engine, store

    def test_zero_one_and_two_hypotheses_and_later_evidence_only(self):
        pacing = repo(
            "labs/pacing", 40,
            description="physiological pacing for attention training",
            topics=["focus"],
        )
        github = FrozenGitHub(
            [("focus", [repo("tools/timer", 200, description="pomodoro timer")])],
            readmes={"tools/timer": "# Timer\nA pomodoro timer.\n## Usage\nStart it."},
        )
        engine, _store = self._engine(github)
        request = SearchRequest.from_dict(REQUEST)
        search = engine.search(request, "deep")
        first = engine.iterate(search["search_id"], {
            "decision": "continue",
            "reason": "no leap this round",
            "target_mechanism": "pomodoro",
            "strategies": ["keyword"],
        })
        self.assertEqual(first["observation"]["semantic_hypotheses"], [])

        github.searches.append(("physiological pacing", [pacing]))
        github.readmes["labs/pacing"] = "# Pacing\nphysiological pacing for deep work.\n## Usage\nWear it."
        second = engine.iterate(search["search_id"], _hypothesis(("physiological pacing", "focus")))
        hypotheses = second["observation"]["semantic_hypotheses"]
        self.assertEqual(len(hypotheses), 1)
        self.assertIn(hypotheses[0]["status"], {"searched", "evidence_found", "inconclusive"})

        with self.assertRaises(ContractError):
            engine.iterate(search["search_id"], _hypothesis(
                ("another leap", "focus"),
                ("third leap", "focus"),
            ))

    def test_sidecar_does_not_change_regular_shortlist_or_base_queries(self):
        pacing = repo(
            "labs/pacing", 12,
            description="physiological pacing sensor",
            topics=["wellbeing"],
        )
        timer = repo("tools/timer", 400, description="pomodoro timer for focus")
        github = FrozenGitHub(
            [
                ("focus", [timer]),
                ("pomodoro", [timer]),
                ("physiological pacing", [pacing]),
            ],
            readmes={
                "tools/timer": "# Timer\nA pomodoro timer.\n## Usage\nStart it.",
                "labs/pacing": "# Pacing\nphysiological pacing.\n## Usage\nInstall.",
            },
        )
        request = SearchRequest.from_dict(REQUEST)
        enabled, store_a = self._engine(github)
        disabled, store_b = self._engine(github, semantic_sidecar=False)
        left = enabled.search(request, "deep")
        right = disabled.search(request, "deep")
        enabled_iter = enabled.iterate(left["search_id"], _hypothesis(("physiological pacing", "focus")))
        disabled_iter = disabled.iterate(right["search_id"], _hypothesis(("physiological pacing", "focus")))
        self.assertEqual(
            compare_base_ledgers(
                store_a.get_session_state(left["search_id"])["semantic_sidecar"]["base_ledger"],
                store_b.get_session_state(right["search_id"])["semantic_sidecar"]["base_ledger"],
            ),
            [],
        )
        # The ledger is internal audit state. Exporting it every round spent ~5KB of an
        # output already capped at SEARCH_OUTPUT_MAX_BYTES and failed whole iterations.
        for output in (left, enabled_iter):
            metrics = (output.get("observation") or {}).get("sidecar_metrics") or {}
            self.assertNotIn("base_ledger", metrics)
            self.assertNotIn("base_ledger", output.get("sidecar_metrics") or {})
        left_regular = [
            item["full_name"] for item in enabled_iter["candidates"]
            if item.get("full_name") != "labs/pacing"
        ]
        right_regular = [item["full_name"] for item in disabled_iter["candidates"]]
        self.assertEqual(left_regular, right_regular)
        self.assertLessEqual(len(enabled_iter["candidates"]), 14)

    def test_sidecar_only_evidence_reaches_assessment_without_original_problem_words(self):
        pacing = repo(
            "labs/pacing", 9,
            description="physiological pacing wearable",
            topics=["sensor"],
        )
        timer = repo("tools/timer", 300, description="pomodoro timer")
        github = FrozenGitHub(
            [("focus", [timer]), ("physiological pacing", [pacing])],
            readmes={
                "tools/timer": "# Timer\nA pomodoro timer.\n## Usage\nStart.",
                "labs/pacing": "# Device\nphysiological pacing without mentioning the user problem.\n## Usage\nWear.",
            },
        )
        engine, _store = self._engine(github)
        search = engine.search(SearchRequest.from_dict(REQUEST), "deep")
        iterated = engine.iterate(search["search_id"], _hypothesis(("physiological pacing", "focus")))
        names = {item["full_name"] for item in iterated["candidates"]}
        self.assertIn("labs/pacing", names)
        self.assertTrue(match_hypothesized_term(
            {
                "description": pacing["description"],
                "topics": pacing["topics"],
                "readme": github.readmes["labs/pacing"],
            },
            "physiological pacing",
        ))

    def test_validation_ignores_numeric_thresholds(self):
        pacing = repo(
            "labs/pacing", 15,
            description="physiological pacing for training",
        )
        timer = repo("tools/timer", 500, description="pomodoro timer for focus")
        github = FrozenGitHub(
            [("focus", [timer]), ("physiological pacing", [pacing])],
            readmes={
                "tools/timer": "# Timer\nA pomodoro timer.\n## Usage\nStart it.",
                "labs/pacing": "# Pacing\nphysiological pacing.\n## Usage\nUse it.",
            },
        )
        engine, store = self._engine(github)
        search = engine.search(SearchRequest.from_dict(REQUEST), "deep")
        iterated = engine.iterate(search["search_id"], _hypothesis(("physiological pacing", "focus")))
        selection = []
        for item in iterated["candidates"]:
            evidence_id = next(
                (
                    ev["id"] for ev in item.get("evidence") or []
                    if ev.get("kind") == "readme_excerpt"
                ),
                None,
            )
            if not evidence_id:
                continue
            mechanism = None
            if item["full_name"] == "labs/pacing":
                mechanism = "physiological pacing"
                evidence_id = next(
                    ev["id"] for ev in item.get("evidence") or []
                    if ev.get("kind") == "mechanism_match"
                )
            selection.append(_selection(item, mechanism or "pomodoro", evidence_id))
        ranking = rank_search(store, search["search_id"], selection)
        statuses = {item["term"]: item["status"] for item in ranking["semantic_hypotheses"]}
        self.assertIn(statuses.get("physiological pacing"), {"validated", "presented", "evidence_found"})

    def test_unvalidated_hypothesis_never_enters_new_mechanisms(self):
        pacing = repo("labs/pacing", 11, description="physiological pacing")
        timer = repo("tools/timer", 220, description="pomodoro timer")
        github = FrozenGitHub(
            [("focus", [timer]), ("physiological pacing", [pacing])],
            readmes={
                "tools/timer": "# Timer\nA pomodoro timer.\n## Usage\nStart.",
                "labs/pacing": "# Pacing\nphysiological pacing.\n## Usage\nGo.",
            },
        )
        engine, store = self._engine(github)
        search = engine.search(SearchRequest.from_dict(REQUEST), "deep")
        iterated = engine.iterate(search["search_id"], _hypothesis(("physiological pacing", "focus")))
        selection = []
        for item in iterated["candidates"]:
            evidence_id = next(
                (
                    ev["id"] for ev in item.get("evidence") or []
                    if ev.get("kind") == "readme_excerpt"
                ),
                None,
            )
            if evidence_id:
                selection.append(_selection(item, "base mechanism", evidence_id))
        ranking = rank_search(store, search["search_id"], selection)
        for item in ranking["items"]:
            self.assertNotIn("physiological pacing", item.get("new_mechanisms") or [])

    def test_public_hypothesis_statuses_are_derived(self):
        record = {
            "id": "h1",
            "term": "physiological pacing",
            "status": "proposed",
            "queries": [
                {"kind": "semantic_pure", "executed": True},
                {"kind": "semantic_bridge", "executed": True},
            ],
            "evidence_repos": [],
        }
        self.assertEqual(public_hypothesis(record)["status"], "rejected")
        record["queries"][1]["skipped"] = True
        record["queries"][1]["executed"] = False
        record["incomplete"] = True
        from muse_shroom.sidecar import derive_hypothesis_status
        self.assertEqual(derive_hypothesis_status(record), "inconclusive")


class HostEvalWorkflowTests(unittest.TestCase):
    def test_prepare_excludes_golden_and_anonymizes_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            result = prepare(bundle)
            self.assertTrue((bundle / "src").exists())
            self.assertTrue((bundle / "skills").exists())
            self.assertFalse((bundle / "evaluation").exists())
            self.assertFalse((bundle / ".git").exists())
            cases = json.loads((bundle / "cases.json").read_text(encoding="utf-8"))
            self.assertTrue(cases["cases"])
            self.assertTrue(all(item["id"].startswith("case-") for item in cases["cases"]))
            self.assertGreaterEqual(result["cases"], 1)

    def test_score_case_requires_query_and_evidence_for_capability(self):
        directions = [{"term": "physiological pacing", "aliases": ["pacing"]}]
        scored = score_case({
            "semantic_hypotheses": [{
                "term": "physiological pacing",
                "status": "evidence_found",
                "queries": [{
                    "kind": "semantic_pure",
                    "query": '"physiological pacing" in:name,description,topics,readme is:public',
                    "executed": True,
                }],
                "evidence_repos": ["labs/pacing"],
            }],
        }, directions)
        self.assertTrue(scored["capability_query_hit"])
        self.assertTrue(scored["capability_evidence_hit"])
