import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from evaluation.host_eval import prepare, score_case
from muse_shroom.github import GitHubAuthenticationError, GitHubRateLimitError, describe_error
from muse_shroom.iteration import validate_hypothesis_evidence
from muse_shroom.models import ContractError, SearchHypothesis, SearchRequest
from muse_shroom.queries import CJK_RE, hypothesis_queries
from muse_shroom.ranking import rank_search
from muse_shroom.search import SEARCH_OUTPUT_MAX_BYTES, SearchEngine, _wire_size
from muse_shroom.selection import SHORTLIST_LIMIT
from muse_shroom.sidecar import (
    SEMANTIC_CANDIDATE_CAP, SEMANTIC_QUERIES_PER_HYPOTHESIS, SEMANTIC_QUERY_BUDGET,
    apply_semantic_mechanism, compare_base_ledgers, derive_hypothesis_status,
    match_hypothesized_term, plan_sidecar_queries, public_hypothesis, split_additions,
    validate_host_hypotheses,
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
        pacing_repos = [
            repo(
                f"labs/pacing-{index}", 12 - index,
                description=f"physiological pacing sensor {index}",
                topics=["wellbeing"],
            )
            for index in range(6)
        ]
        timer = repo("tools/timer", 400, description="pomodoro timer for focus")
        github = FrozenGitHub(
            [
                ("focus", [timer]),
                ("pomodoro", [timer]),
                ("physiological pacing", pacing_repos),
            ],
            readmes={"tools/timer": "# Timer\nA pomodoro timer.\n## Usage\nStart it."},
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
            if not item["full_name"].startswith("labs/pacing")
        ]
        right_regular = [item["full_name"] for item in disabled_iter["candidates"]]
        self.assertEqual(left_regular, right_regular)
        # Many sidecar matches must leave the base query count and README budget alone.
        self.assertEqual(
            store_a.normal_query_count(left["search_id"]),
            store_b.normal_query_count(right["search_id"]),
        )
        sidecar = store_a.get_session_state(left["search_id"])["semantic_sidecar"]
        self.assertEqual(sidecar["metrics"]["semantic_assessment_count"], len(pacing_repos))
        self.assertLessEqual(len(enabled_iter["candidates"]), 14)

    def test_every_semantic_match_is_offered_not_just_one(self):
        pacing = repo("labs/pacing", 40, description="physiological pacing wearable", topics=["sensor"])
        stride = repo("labs/stride", 30, description="physiological pacing coach", topics=["sensor"])
        timer = repo("tools/timer", 300, description="pomodoro timer")
        github = FrozenGitHub(
            [("focus", [timer]), ("physiological pacing", [pacing, stride])],
            readmes={
                "tools/timer": "# Timer\nA pomodoro timer.\n## Usage\nStart.",
                "labs/pacing": "# Pacing\nphysiological pacing for runners.\n## Usage\nWear.",
                "labs/stride": "# Stride\nphysiological pacing for walkers.\n## Usage\nWear.",
            },
        )
        engine, store = self._engine(github)
        search = engine.search(SearchRequest.from_dict(REQUEST), "deep")
        iterated = engine.iterate(search["search_id"], _hypothesis(("physiological pacing", "focus")))
        public = {item["full_name"]: item for item in iterated["candidates"]}
        self.assertIn("labs/pacing", public)
        self.assertIn("labs/stride", public)
        for name in ("labs/pacing", "labs/stride"):
            evidence = [
                item for item in public[name].get("evidence") or []
                if item.get("kind") == "mechanism_match"
            ]
            self.assertTrue(evidence)
            self.assertTrue(evidence[0]["facts"]["mechanisms"][0]["text"])
        sidecar = store.get_session_state(search["search_id"])["semantic_sidecar"]
        flagged = {
            item["full_name"] for item in sidecar["candidates"]
            if item.get("selected_for_assessment")
        }
        self.assertEqual(flagged, {"labs/pacing", "labs/stride"})
        self.assertEqual(sidecar["metrics"]["semantic_assessment_count"], 2)
        record = next(
            item for item in sidecar["hypotheses"]
            if item["term"] == "physiological pacing"
        )
        self.assertIsNone(record["assessment_repo"])

    def test_unselected_semantic_candidate_stays_out_of_the_ranking(self):
        pacing = repo("labs/pacing", 40, description="physiological pacing wearable")
        stride = repo("labs/stride", 30, description="physiological pacing coach")
        timer = repo("tools/timer", 300, description="pomodoro timer")
        github = FrozenGitHub(
            [("focus", [timer]), ("physiological pacing", [pacing, stride])],
            readmes={
                "tools/timer": "# Timer\nA pomodoro timer.\n## Usage\nStart.",
                "labs/pacing": "# Pacing\nphysiological pacing for runners.\n## Usage\nWear.",
                "labs/stride": "# Stride\nphysiological pacing for walkers.\n## Usage\nWear.",
            },
        )
        engine, store = self._engine(github)
        search = engine.search(SearchRequest.from_dict(REQUEST), "deep")
        iterated = engine.iterate(search["search_id"], _hypothesis(("physiological pacing", "focus")))
        public = {item["full_name"]: item for item in iterated["candidates"]}
        pacing_evidence = next(
            item["id"] for item in public["labs/pacing"]["evidence"]
            if item.get("kind") == "mechanism_match"
        )
        ranking = rank_search(store, search["search_id"], [
            _selection(public["labs/pacing"], "physiological pacing", pacing_evidence),
        ])
        self.assertEqual(ranking["next_action"], "done")
        self.assertEqual([item["repo"] for item in ranking["items"]], ["labs/pacing"])
        self.assertNotIn("labs/stride", [item["repo"] for item in ranking["items"]])
        self.assertEqual(ranking["rejected_items"], [])

    def test_worst_case_sidecar_round_stays_within_the_output_limit(self):
        # A full base shortlist plus a cap-sized set of sidecar matches is the heaviest
        # round the Agent can be handed; the compaction ladder must absorb it.
        request = SearchRequest.from_dict({
            **REQUEST,
            "mechanisms": ["pomodoro", "distraction blocking", "timeboxing", "body doubling"],
        })
        groups = {
            "pomodoro": "pomodoro timer for deep work",
            "distraction blocking": "website blocker for distraction blocking",
            "timeboxing": "timeboxing planner for focused sprints",
            "body doubling": "body doubling session partner",
        }
        base = []
        readmes = {}
        searches = []
        counter = 0
        for needle, description in groups.items():
            items = []
            for index in range(10):
                item = repo(
                    f"owner{counter}/tool-{index}", 500 - counter * 10 - index,
                    description=f"{description} number {index}", topics=["focus"],
                )
                counter += 1
                items.append(item)
                readmes[item["full_name"]] = (
                    f"# Tool\n{description} number {index}.\n## Usage\nRun {index}."
                )
            searches.append((needle, items))

        def semantic(prefix, needle):
            return [
                repo(
                    f"labs{counter}/{prefix}-{index}", 90 - index,
                    description=f"{needle} " + "wearable telemetry data for athletes " * 7,
                )
                for index in range(10)
            ]

        # FrozenGitHub serves at most 10 items per query, so the worst case needs both
        # queries of both hypotheses to return disjoint repositories.
        for needle in groups:
            for term in ("physiological pacing", "attention pacing"):
                if needle in term or term in needle:
                    raise AssertionError("fixture needles must not shadow each other")
        pacing_pure = semantic("pacing-a", "physiological pacing")
        pacing_bridge = semantic("pacing-b", "physiological pacing")
        attention_pure = semantic("attention-a", "attention pacing")
        attention_bridge = semantic("attention-b", "attention pacing")
        searches.extend([
            ('"physiological pacing" "focus"', pacing_bridge),
            ('"physiological pacing"', pacing_pure),
            ('"attention pacing" "focus"', attention_bridge),
            ('"attention pacing"', attention_pure),
        ])
        github = FrozenGitHub(searches, readmes=readmes)
        engine, _store = self._engine(github)
        search = engine.search(request, "deep")
        iterated = engine.iterate(search["search_id"], _hypothesis(
            ("physiological pacing", "focus"), ("attention pacing", "focus"),
        ))
        offered = {item["full_name"] for item in (
            pacing_pure + pacing_bridge + attention_pure + attention_bridge
        )}
        names = {item["full_name"] for item in iterated["candidates"]}
        base_shortlisted = {
            item["full_name"] for item in iterated["candidates"]
            if item["full_name"].startswith("owner")
        }
        self.assertGreaterEqual(len(base_shortlisted), SHORTLIST_LIMIT - 1)
        self.assertEqual(len(offered), SEMANTIC_CANDIDATE_CAP)
        self.assertTrue(offered.issubset(names))
        for item in iterated["candidates"]:
            if item["full_name"] in offered:
                self.assertTrue(item.get("evidence"))
        self.assertLessEqual(_wire_size(iterated), SEARCH_OUTPUT_MAX_BYTES)
        self.assertEqual(iterated["coverage"]["output_bytes"], _wire_size(iterated))

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

    def test_inspect_and_explorer_find_a_candidate_only_the_sidecar_recalled(self):
        # A 2026-09-20 Codex run could not correct a rejected quote for its only leap:
        # inspect read the candidate table, which never holds sidecar-only candidates.
        from muse_shroom.cli import main
        from muse_shroom.explorer.read_model import ExplorerReadModel
        from muse_shroom.services import MuseCore

        pacing = repo("labs/pacing", 9, description="physiological pacing wearable")
        timer = repo("tools/timer", 300, description="pomodoro timer")
        github = FrozenGitHub(
            [("focus", [timer]), ("physiological pacing", [pacing])],
            readmes={
                "tools/timer": "# Timer\nA pomodoro timer.\n## Usage\nStart.",
                "labs/pacing": "# Device\nphysiological pacing for steady attention.\n## Usage\nWear.",
            },
        )
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        core = MuseCore(data_dir=directory.name, github=github)
        search = core.search(REQUEST, "deep")
        core.iterate(search["search_id"], _hypothesis(("physiological pacing", "focus")))
        store = Store(directory.name)
        try:
            self.assertIsNone(store.get_candidate("labs/pacing", search["search_id"]))
        finally:
            store.close()

        inspected = core.inspect("labs/pacing", search["search_id"])
        semantic = [
            item for item in inspected["repository"]["evidence"] if item.get("kind") == "mechanism_match"
        ]
        self.assertTrue(semantic)
        self.assertIn("physiological pacing", json.dumps(semantic))
        detail = ExplorerReadModel(data_dir=directory.name).repo_detail(search["search_id"], "labs/pacing")
        self.assertEqual(detail["repo"], "labs/pacing")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["--data-dir", directory.name, "inspect", "labs/pacing", "--search-id", search["search_id"]])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["repository"]["full_name"], "labs/pacing")
        # Without a session, inspect still reads only the shared snapshots.
        with self.assertRaises(KeyError):
            core.inspect("labs/pacing", "no-such-search")

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


CJK_ANCHOR_REQUEST = {
    "request": "提高专注力的工具",
    "problem_concepts": [
        {"term": "提高专注"},
        {"term": "减少分心", "aliases": ["distraction"]},
    ],
    "mechanisms": ["pomodoro"],
    "artifact_types": ["application"],
}


def _host_addition(term, anchor, aliases=None, **extra):
    addition = {
        "term": term,
        "request_anchor": anchor,
        "reason": "neighboring domain may transfer",
        "evidence": "host_hypothesis",
        **extra,
    }
    if aliases is not None:
        addition["aliases"] = aliases
    return addition


def _host_hypothesis(*additions, **fields):
    return {
        "decision": "continue",
        "reason": "test sidecar",
        "add_exploration_directions": list(additions),
        "strategies": ["keyword"],
        **fields,
    }


def _record(term, anchor, aliases=(), record_id="h1:1:x"):
    return {
        "id": record_id, "term": term, "aliases": list(aliases),
        "request_anchor": anchor, "queries": [],
    }


def _phrases(query):
    return re.findall(r'"([^"]+)"', query)


class SidecarReachTests(unittest.TestCase):
    def test_host_hypothesis_aliases_are_a_bounded_single_line_list(self):
        hypothesis = SearchHypothesis.from_dict(_host_hypothesis(_host_addition(
            "physiological pacing", "focus", aliases=["pacing", "Pacing", "physiological pacing"],
        )), strict=True)
        addition = hypothesis.add_exploration_directions[0]
        # Repeats of each other or of the term are dropped; the limit applies to what was sent.
        self.assertEqual(addition.aliases, ["pacing"])
        self.assertEqual(addition.to_dict()["aliases"], ["pacing"])
        for aliases in (["a", "b", "c", "d"], "pacing", ["pacing", 3], ["two\nlines"], [""]):
            with self.assertRaises(ContractError, msg=repr(aliases)):
                SearchHypothesis.from_dict(_host_hypothesis(_host_addition(
                    "physiological pacing", "focus", aliases=aliases,
                )), strict=True)

    def test_only_host_hypotheses_carry_aliases(self):
        with self.assertRaises(ContractError):
            SearchHypothesis.from_dict(_host_hypothesis({
                "term": "observed-term", "evidence": "discovered_term", "aliases": ["other"],
            }), strict=True)

    def test_host_hypothesis_needs_an_english_phrasing(self):
        request = SearchRequest.from_dict(CJK_ANCHOR_REQUEST)
        cjk_only = SearchHypothesis.from_dict(_host_hypothesis(
            _host_addition("呼吸节律调节", "提高专注"),
        ))
        with self.assertRaises(ContractError) as raised:
            validate_host_hypotheses(cjk_only, request, iteration=1, existing=[])
        self.assertIn("English phrasing", str(raised.exception))
        with_alias = SearchHypothesis.from_dict(_host_hypothesis(
            _host_addition("呼吸节律调节", "提高专注", aliases=["breath pacing"]),
        ))
        validate_host_hypotheses(with_alias, request, iteration=1, existing=[])

    def test_aliases_obey_exclusions_negatives_and_ordinary_fields(self):
        request = SearchRequest.from_dict({**REQUEST, "exclusions": ["wearable"]})
        excluded = SearchHypothesis.from_dict(_host_hypothesis(
            _host_addition("physiological pacing", "focus", aliases=["wearable"]),
        ))
        with self.assertRaises(ContractError):
            validate_host_hypotheses(excluded, request, iteration=1, existing=[])
        negative = SearchHypothesis.from_dict(_host_hypothesis(
            _host_addition("physiological pacing", "focus", aliases=["heart sensor"]),
        ))
        with self.assertRaises(ContractError):
            validate_host_hypotheses(
                negative, request, iteration=1, existing=[], negatives=["heart sensor"],
            )
        repeated = SearchHypothesis.from_dict(_host_hypothesis(
            _host_addition("physiological pacing", "focus", aliases=["breath pacing"]),
            concepts=["breath pacing"],
        ))
        with self.assertRaises(ContractError):
            validate_host_hypotheses(repeated, request, iteration=1, existing=[])
        repository = SearchHypothesis.from_dict(_host_hypothesis(
            _host_addition("physiological pacing", "focus", aliases=["owner/repo"]),
        ))
        with self.assertRaises(ContractError):
            validate_host_hypotheses(repository, request, iteration=1, existing=[])

    def test_shortest_english_phrasing_leads_and_cjk_is_never_searched(self):
        request = SearchRequest.from_dict(CJK_ANCHOR_REQUEST)
        planned, skipped = plan_sidecar_queries(
            [_record("physiological pacing technique", "提高专注", aliases=["pacing", "呼吸节律"])],
            request, remaining_budget=4,
        )
        self.assertEqual(skipped, [])
        # The anchored concept has no English phrasing, so no bridge: the second query
        # tries the next phrasing of the hypothesis instead.
        self.assertEqual([item["kind"] for item in planned], ["semantic_pure", "semantic_pure"])
        self.assertEqual([_phrases(item["query"]) for item in planned], [
            ["pacing"], ["physiological pacing technique"],
        ])

    def test_bridge_uses_an_english_phrasing_of_the_anchored_concept(self):
        request = SearchRequest.from_dict(CJK_ANCHOR_REQUEST)
        planned, _skipped = plan_sidecar_queries(
            [_record("breath pacing", "减少分心")], request, remaining_budget=4,
        )
        self.assertEqual([item["kind"] for item in planned], ["semantic_pure", "semantic_bridge"])
        self.assertEqual(_phrases(planned[1]["query"]), ["breath pacing", "distraction"])
        latin = SearchRequest.from_dict(REQUEST)
        planned, _skipped = plan_sidecar_queries(
            [_record("physiological pacing", "focus management")], latin, remaining_budget=4,
        )
        self.assertEqual(_phrases(planned[1]["query"]), ["physiological pacing", "focus"])

    def test_one_phrasing_without_an_english_anchor_plans_one_query(self):
        request = SearchRequest.from_dict(CJK_ANCHOR_REQUEST)
        planned, skipped = plan_sidecar_queries(
            [_record("breath pacing", "提高专注")], request, remaining_budget=4,
        )
        self.assertEqual(len(planned), 1)
        self.assertEqual(skipped, [])

    def test_sidecar_queries_stay_within_budget_and_never_mix_scripts(self):
        requests = [SearchRequest.from_dict(REQUEST), SearchRequest.from_dict(CJK_ANCHOR_REQUEST)]
        records = [
            ("physiological pacing", ["pacing", "breath pacing"]),
            ("呼吸节律调节", ["breath pacing", "paced breathing technique"]),
            ("slow breathing", []),
        ]
        anchors = ["focus", "focus management", "提高专注", "减少分心", "distraction"]
        for request in requests:
            valid_anchors = {term for concept in request.problem_concepts for term in concept.terms()}
            for anchor in anchors:
                if anchor not in valid_anchors:
                    continue
                batch = [
                    _record(term, anchor, aliases=aliases, record_id=f"h:{index}")
                    for index, (term, aliases) in enumerate(records)
                ]
                planned, _skipped = plan_sidecar_queries(batch, request, remaining_budget=SEMANTIC_QUERY_BUDGET)
                self.assertLessEqual(len(planned), SEMANTIC_QUERY_BUDGET)
                per_record = {}
                for item in planned:
                    per_record[item["hypothesis_id"]] = per_record.get(item["hypothesis_id"], 0) + 1
                    self.assertFalse(
                        any(CJK_RE.search(phrase) for phrase in _phrases(item["query"])),
                        item["query"],
                    )
                self.assertTrue(all(count <= SEMANTIC_QUERIES_PER_HYPOTHESIS for count in per_record.values()))

    def test_one_query_hypothesis_without_evidence_is_rejected(self):
        record = {
            "id": "h1", "term": "breath pacing", "evidence_repos": [],
            "queries": [{"kind": "semantic_pure", "executed": True}],
        }
        self.assertEqual(derive_hypothesis_status(record), "rejected")
        record["queries"][0]["executed"] = False
        self.assertEqual(derive_hypothesis_status(record), "proposed")

    def test_alias_in_recorded_text_counts_as_evidence(self):
        def candidate():
            return {
                "full_name": "labs/breath", "html_url": "https://github.com/labs/breath",
                "description": "", "topics": [], "evidence": [], "mechanisms": [],
                "readme": "# Breath\nbreath pacing for calm work\n",
            }

        self.assertFalse(apply_semantic_mechanism(candidate(), "physiological pacing", "h1"))
        matched = candidate()
        self.assertTrue(apply_semantic_mechanism(
            matched, "physiological pacing", "h1", ["breath pacing"],
        ))
        mechanism = next(item for item in matched["mechanisms"] if item.get("semantic_origin"))
        self.assertEqual(mechanism["name"], "physiological pacing")
        self.assertEqual(mechanism["matched_terms"], ["breath pacing"])

    def test_cjk_term_with_english_alias_reaches_evidence_through_iterate(self):
        breath = repo("labs/breath", 40, description="breath pacing trainer", topics=["calm"])
        timer = repo("tools/timer", 300, description="pomodoro timer")
        github = FrozenGitHub(
            [("focus", [timer]), ("breath pacing", [breath])],
            readmes={
                "tools/timer": "# Timer\nA pomodoro timer.\n## Usage\nStart.",
                "labs/breath": "# Breath\nbreath pacing for deep work.\n## Usage\nBreathe.",
            },
        )
        directory = tempfile.TemporaryDirectory()
        store = Store(directory.name)
        self.addCleanup(directory.cleanup)
        self.addCleanup(store.close)
        engine = SearchEngine(store, github, relation_budget=0)
        search = engine.search(SearchRequest.from_dict(REQUEST), "deep")
        iterated = engine.iterate(search["search_id"], _host_hypothesis(
            _host_addition("呼吸节律调节", "focus", aliases=["breath pacing"]),
        ))
        hypothesis = iterated["observation"]["semantic_hypotheses"][0]
        self.assertEqual(hypothesis["aliases"], ["breath pacing"])
        self.assertEqual(hypothesis["status"], "evidence_found")
        self.assertIn("labs/breath", hypothesis["evidence_repos"])
        for query in hypothesis["queries"]:
            self.assertFalse(any(CJK_RE.search(phrase) for phrase in _phrases(query["query"])))


class FlakyGitHub(FrozenGitHub):
    """FrozenGitHub whose searches fail when the query contains every needle in ``fail_on``."""

    def __init__(self, *args, fail_on=(), error=GitHubRateLimitError, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_on = tuple(needle.lower() for needle in fail_on)
        self.error = error

    def search_repositories(self, query, per_page=10, sort="stars"):
        if self.fail_on and all(needle in query.lower() for needle in self.fail_on):
            self.request_counts["search"] += 1
            raise self.error("GitHub API rate limited request (403) github_pat_secretvalue")
        return super().search_repositories(query, per_page, sort)


class RecallFailureTests(unittest.TestCase):
    def _engine(self, github):
        directory = tempfile.TemporaryDirectory()
        store = Store(directory.name)
        self.addCleanup(directory.cleanup)
        self.addCleanup(store.close)
        return SearchEngine(store, github, relation_budget=0), store

    def _breath_github(self, **options):
        return FlakyGitHub(
            [
                ("focus", [repo("tools/timer", 300, description="pomodoro timer")]),
                ("breath pacing", [repo("labs/breath", 40, description="breath pacing trainer")]),
            ],
            readmes={
                "tools/timer": "# Timer\nA pomodoro timer.\n## Usage\nStart.",
                "labs/breath": "# Breath\nbreath pacing for deep work.\n## Usage\nBreathe.",
            },
            **options,
        )

    def test_error_description_is_one_redacted_line(self):
        text = describe_error(GitHubRateLimitError("limited\nfor github_pat_abc123 now"))
        self.assertEqual(text, "GitHubRateLimitError: limited for [redacted] now")

    def test_one_failed_sidecar_query_keeps_the_others_and_records_why(self):
        github = self._breath_github(fail_on=('"breath pacing"', '"focus"'))
        engine, store = self._engine(github)
        search = engine.search(SearchRequest.from_dict(REQUEST), "deep")
        iterated = engine.iterate(search["search_id"], _host_hypothesis(
            _host_addition("breath pacing", "focus"),
        ))
        hypothesis = iterated["observation"]["semantic_hypotheses"][0]
        queries = {query["kind"]: query for query in hypothesis["queries"]}
        self.assertTrue(queries["semantic_pure"]["executed"])
        self.assertFalse(queries["semantic_bridge"]["executed"])
        self.assertEqual(
            queries["semantic_bridge"]["error"],
            "GitHubRateLimitError: GitHub API rate limited request (403) [redacted]",
        )
        self.assertEqual(hypothesis["status"], "evidence_found")
        self.assertIn("labs/breath", hypothesis["evidence_repos"])
        self.assertEqual(iterated["observation"]["sidecar_metrics"]["semantic_queries_failed"], 1)
        history = [item for item in store.query_history(search["search_id"]) if item["kind"] == "semantic_bridge"]
        self.assertEqual([item["skip_reason"] for item in history], ["failed:GitHubRateLimitError"])
        self.assertNotIn(history[0]["fingerprint"], store.query_fingerprints(search["search_id"]))

    def test_every_sidecar_query_failing_leaves_the_hypothesis_inconclusive(self):
        github = self._breath_github(fail_on=('"breath pacing"',))
        engine, _store = self._engine(github)
        search = engine.search(SearchRequest.from_dict(REQUEST), "deep")
        iterated = engine.iterate(search["search_id"], _host_hypothesis(
            _host_addition("breath pacing", "focus"),
        ))
        hypothesis = iterated["observation"]["semantic_hypotheses"][0]
        self.assertEqual(hypothesis["status"], "inconclusive")
        self.assertTrue(all("GitHubRateLimitError" in query["error"] for query in hypothesis["queries"]))
        self.assertEqual(
            iterated["observation"]["sidecar_metrics"]["semantic_queries_failed"],
            len(hypothesis["queries"]),
        )

    def test_one_failed_base_query_keeps_the_others(self):
        github = self._breath_github(fail_on=("pomodoro",))
        engine, store = self._engine(github)
        search = engine.search(SearchRequest.from_dict(REQUEST), "deep")
        names = {item["full_name"] for item in search["candidates"]}
        self.assertIn("tools/timer", names)
        self.assertEqual(search["incomplete_phase"], "github_error:GitHubRateLimitError")
        failed = [item for item in store.query_history(search["search_id"]) if item["skip_reason"]]
        self.assertTrue(failed)
        self.assertTrue(all(item["skip_reason"] == "failed:GitHubRateLimitError" for item in failed))
        self.assertTrue(all("pomodoro" in item["query"].lower() for item in failed))

    def test_every_base_query_failing_still_raises(self):
        engine, _store = self._engine(self._breath_github(fail_on=("",)))
        with self.assertRaises(GitHubRateLimitError):
            engine.search(SearchRequest.from_dict(REQUEST), "deep")

    def test_rejected_credential_still_aborts_the_batch(self):
        engine, _store = self._engine(
            self._breath_github(fail_on=("pomodoro",), error=GitHubAuthenticationError),
        )
        with self.assertRaises(GitHubAuthenticationError):
            engine.search(SearchRequest.from_dict(REQUEST), "deep")


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
