import tempfile
import unittest
from pathlib import Path

from evaluation.cassette import CassetteGitHub
from muse_shroom import github as github_module
from muse_shroom.iteration import session_loop_diagnostics
from muse_shroom.models import ContractError, SearchHypothesis, SearchRequest
from muse_shroom.queries import hypothesis_queries, query_fingerprint
from muse_shroom.ranking import rank_search
from muse_shroom.search import SearchEngine
from muse_shroom.storage import Store

from tests.helpers import FrozenGitHub, repo


FOCUS_REQUEST = {
    "request": "提高专注力的工具",
    "problem_concepts": [{"term": "focus", "aliases": ["focus management"]}],
    "mechanisms": ["pomodoro", "distraction blocking"],
    "exploration_directions": ["commitment device", "digital wellbeing"],
    "artifact_types": ["application"],
}


def _dom_repos() -> list[dict]:
    return [
        repo("web/focus-trap", 800, description="Trap DOM focus in a modal",
             topics=["javascript", "accessibility", "dom"]),
        repo("web/keyboard-focus", 400, description="Keyboard focus management for widgets",
             topics=["keyboard", "accessibility"]),
        repo("web/focus-visible", 1200, description="Polyfill for accessibility focus-visible",
             topics=["css", "accessibility"]),
        repo("web/timer-widget", 60, description="Generic countdown timer widget",
             topics=["timer", "productivity"]),
    ]


class IterationContractTests(unittest.TestCase):
    def test_query_fingerprint_ignores_case_and_token_order(self):
        left = '"foo" "bar" in:readme is:public'
        right = '"Bar" "FOO" is:public in:readme'
        self.assertEqual(query_fingerprint(left), query_fingerprint(right))

    def test_hypothesis_requires_an_explicit_decision(self):
        with self.assertRaises(ContractError):
            SearchHypothesis.from_dict({"concepts": ["pomodoro"]})
        stopped = SearchHypothesis.from_dict({
            "decision": "stop", "stop_reason": "low expected gain",
            "remaining_unexplored_directions": ["biofeedback"],
        })
        self.assertEqual(stopped.decision, "stop")

    def test_negative_terms_are_not_scheduled(self):
        request = SearchRequest.from_dict(FOCUS_REQUEST)
        hypothesis = SearchHypothesis.from_dict({
            "decision": "continue",
            "reason": "shift away from UI focus",
            "negative_directions": ["DOM focus", "keyboard focus", "accessibility focus"],
            "target_mechanism": "distraction blocking",
            "concepts": ["focus", "website blocker"],
        })
        executed, skipped = hypothesis_queries(
            hypothesis, request, negatives=hypothesis.negative_directions, limit=6,
        )
        terms = {item["term"].casefold() for item in executed}
        self.assertIn("website blocker", terms)
        self.assertNotIn("focus", terms)
        self.assertTrue(all("focus" not in item["query"].casefold() or "blocker" in item["query"].casefold()
                            for item in executed))
        self.assertEqual(skipped, [])


class AgenticLoopTests(unittest.TestCase):
    def test_focus_ambiguity_observation_supports_a_corrective_hypothesis(self):
        blocker = repo("tools/website-blocker", 80, description="Website blocker for deep work")
        commitment = repo("tools/commitment-device", 20, description="Commitment device that locks apps")
        github = FrozenGitHub(
            [
                ("website blocker", [blocker]),
                ("commitment", [commitment]),
                ("focus", _dom_repos()),
            ],
            readmes={
                "web/focus-trap": "# Trap\nKeep DOM focus inside a dialog.\n## Usage\nImport it.",
                "web/keyboard-focus": "# Keyboard\nKeyboard focus helpers.\n## Usage\nTab through widgets.",
                "web/focus-visible": "# Visible\nAccessibility focus polyfill.\n## Usage\nUse CSS.",
                "web/timer-widget": "# Timer\nA generic countdown timer.\n## Usage\nStart it.",
                "tools/website-blocker": "# Blocker\nA website blocker.\n## Usage\nInstall the app.",
                "tools/commitment-device": "# Stick\nA commitment device.\n## Usage\nLock the session.",
            },
        )
        request = SearchRequest.from_dict(FOCUS_REQUEST)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                engine = SearchEngine(store, github, relation_budget=0)
                first = engine.search(request, "deep")
                signals = {item["kind"] for item in first["observation"]["ambiguity_signals"]}
                second = engine.iterate(first["search_id"], {
                    "decision": "continue",
                    "reason": "first round hit DOM and keyboard focus, not productivity",
                    "negative_directions": [
                        "DOM focus", "keyboard focus", "accessibility focus",
                    ],
                    "rejected_directions": ["countdown timer"],
                    "target_mechanism": "distraction blocking",
                    "target_direction": "commitment device",
                    "concepts": ["website blocker"],
                })
                snapshots = store.boundary_snapshots(first["search_id"])
            finally:
                store.close()

        self.assertEqual(first["next_action"], "iterate")
        self.assertIn("problem_without_mechanism", signals)
        self.assertTrue(first["observation"]["unexplored_directions"])
        self.assertIn("commitment device", second["boundary_delta"]["new_mechanisms"]
                      + second["boundary"]["recalled_mechanisms"])
        self.assertEqual(second["boundary"]["negative_directions"], [
            "DOM focus", "keyboard focus", "accessibility focus",
        ])
        self.assertEqual(second["boundary"]["rejected_directions"], ["countdown timer"])
        self.assertNotEqual(second["boundary"]["rejected_directions"], second["boundary"]["negative_directions"])
        self.assertEqual([item["stage"] for item in snapshots[:2]], ["search", "iterate"])
        self.assertEqual(snapshots[1]["iteration"], 1)
        returned = {item["full_name"] for item in second["candidates"]}
        self.assertEqual(returned, {"tools/website-blocker", "tools/commitment-device"})
        self.assertLess(second["candidate_count"], first["candidate_count"])

    def test_redundant_pomodoro_round_explores_an_uncovered_mechanism(self):
        pomodoros = [
            repo(f"pomo/timer{index}", 12 + index, description="Pomodoro timer")
            for index in range(8)
        ]
        blocker = repo("block/sites", 9, description="Website blocker")
        commitment = repo("habits/commit", 6, description="Commitment device for self-control")
        github = FrozenGitHub(
            [
                ("commitment", [commitment]),
                ("pomodoro", pomodoros),
                ("blocking", [blocker]),
                ("focus", pomodoros + [blocker]),
            ],
            readmes={
                **{item["full_name"]: "# Timer\nPomodoro workflow.\n## Usage\nRun it." for item in pomodoros},
                "block/sites": "# Block\nA website blocker.\n## Usage\nBlock sites.",
                "habits/commit": "# Commit\nA commitment device.\n## Usage\nLock the goal.",
            },
        )
        request = SearchRequest.from_dict({
            "request": "stay focused",
            "problem_concepts": ["focus"],
            "mechanisms": ["pomodoro", "blocking"],
            "exploration_directions": ["biofeedback"],
        })
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                engine = SearchEngine(store, github, relation_budget=0)
                first = engine.search(request, "deep")
                first_redundancy = first["observation"]["mechanism_redundancy"]
                second = engine.iterate(first["search_id"], {
                    "decision": "continue",
                    "reason": "pomodoro already dominates; explore uncovered direction",
                    "target_direction": "commitment device",
                    "concepts": ["commitment device"],
                    "add_exploration_directions": [{
                        "term": "commitment device",
                        "reason": "self-control mechanism not covered by pomodoro",
                    }],
                })
            finally:
                store.close()

        self.assertGreaterEqual(first["observation"]["mechanism_distribution"].get("pomodoro", 0), 5)
        self.assertIn("mechanism_monopoly", {item["kind"] for item in first["observation"]["ambiguity_signals"]})
        self.assertIn("commitment device", second["boundary_delta"]["new_mechanisms"])
        self.assertGreater(len(second["boundary_delta"]["new_mechanisms"]), 0)
        self.assertLessEqual(second["observation"]["mechanism_redundancy"], first_redundancy)

    def test_discovered_terms_are_not_mechanisms_until_promoted_and_evidenced(self):
        timer = repo("focus/timer", 4, description="Pomodoro timer", topics=["digital-wellbeing"])
        wellbeing = repo("habits/wellbeing", 3, description="Digital wellbeing dashboard")
        github = FrozenGitHub(
            [
                ("digital wellbeing", [wellbeing]),
                ("pomodoro", [timer]),
                ("focus", [timer]),
            ],
            readmes={
                "focus/timer": "# Timer\nA Pomodoro workflow.\n## Usage\nRun it.",
                "habits/wellbeing": "# Wellbeing\nDigital wellbeing tracker.\n## Usage\nOpen the dashboard.",
            },
        )
        request = SearchRequest.from_dict({
            "request": "focus",
            "problem_concepts": ["focus"],
            "mechanisms": ["pomodoro"],
            "exploration_directions": ["biofeedback"],
        })
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                engine = SearchEngine(store, github, relation_budget=0)
                first = engine.search(request, "deep")
                self.assertIn("digital wellbeing", first["boundary"]["discovered_terms"])
                self.assertNotIn("digital wellbeing", first["boundary"]["recalled_mechanisms"])
                second = engine.iterate(first["search_id"], {
                    "decision": "continue",
                    "reason": "README topics suggest digital wellbeing is related",
                    "promote_discovered_terms": ["digital wellbeing"],
                    "concepts": ["digital wellbeing"],
                })
            finally:
                store.close()

        self.assertIn("digital wellbeing", second["boundary"]["recalled_mechanisms"])
        self.assertIn("digital wellbeing", second["boundary"]["explored_directions"])
        additions = second["observation"]["exploration_additions"]
        self.assertTrue(any(item["term"] == "digital wellbeing" for item in additions))
        self.assertEqual(additions[0]["source_iteration"], 1)

    def test_stop_conditions_prevent_unbounded_loops(self):
        item = repo("focus/timer", 4, description="Pomodoro timer")
        github = FrozenGitHub(
            [("focus", [item]), ("pomodoro", [item])],
            readmes={"focus/timer": "# Timer\nPomodoro.\n## Usage\nRun it."},
        )
        request = SearchRequest.from_dict({
            "request": "focus",
            "problem_concepts": ["focus"],
            "mechanisms": ["pomodoro"],
            "exploration_directions": ["pomodoro"],
        })
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                engine = SearchEngine(
                    store, github, relation_budget=0, max_iterations=1, session_query_budget=40,
                )
                first = engine.search(request, "deep")
                queries_after_search = store.query_count(first["search_id"])
                covered = engine.iterate(first["search_id"], {
                    "decision": "continue",
                    "reason": "confirm remaining direction",
                    "concepts": ["pomodoro timer"],
                })
                agent_stopped = engine.iterate(first["search_id"], {
                    "decision": "stop",
                    "stop_reason": "remaining directions are low value",
                    "remaining_unexplored_directions": [],
                })
                # max_iterations=1 already consumed by the continue above; next continue is refused.
                refused = engine.iterate(first["search_id"], {
                    "decision": "continue",
                    "reason": "try again",
                    "concepts": ["deep work"],
                })
                tight = SearchEngine(
                    store, github, relation_budget=0, session_query_budget=queries_after_search,
                )
                budgeted = tight.search(request, "deep", refresh=True)
                budget_stop = tight.iterate(budgeted["search_id"], {
                    "decision": "continue",
                    "reason": "no remaining query budget",
                    "concepts": ["biofeedback"],
                })
                queries_after_loop = store.query_count(first["search_id"])
                events = store.list_iterations(first["search_id"])
            finally:
                store.close()

        self.assertIn("directions_covered", covered["observation"]["stop"]["signals"])
        self.assertNotIn("directions_covered", covered["observation"]["stop"]["reasons"])
        self.assertEqual(agent_stopped["stop_reason"], "agent_stop")
        self.assertEqual(agent_stopped["next_action"], "rank")
        self.assertEqual(refused["stop_reason"], "max_iterations")
        self.assertEqual(refused["next_action"], "rank")
        self.assertEqual(queries_after_loop, queries_after_search + 1)
        self.assertEqual(budget_stop["stop_reason"], "query_budget_exhausted")
        self.assertEqual([item["event"] for item in events], ["iterate", "stop", "refuse"])
        self.assertEqual(events[0]["hypothesis"]["concepts"], ["pomodoro timer"])
        self.assertEqual(events[1]["hypothesis"]["decision"], "stop")
        self.assertEqual(events[2]["hypothesis"]["concepts"], ["deep work"])

    def test_duplicate_queries_are_skipped_and_can_stop_the_loop(self):
        item = repo("focus/timer", 4, description="Pomodoro timer")
        github = FrozenGitHub(
            [("pomodoro", [item]), ("focus", [item])],
            readmes={"focus/timer": "# Timer\nPomodoro.\n## Usage\nRun it."},
        )
        request = SearchRequest.from_dict({
            "request": "focus",
            "problem_concepts": ["focus"],
            "mechanisms": ["pomodoro"],
        })
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                engine = SearchEngine(store, github, relation_budget=0)
                first = engine.search(request, "deep")
                second = engine.iterate(first["search_id"], {
                    "decision": "continue",
                    "reason": "repeat the same mechanism wording",
                    "concepts": ["pomodoro"],
                })
            finally:
                store.close()

        self.assertGreaterEqual(second["observation"]["query_summary"]["skipped_count"], 1)
        self.assertIn("duplicate_queries", second["observation"]["stop"]["reasons"])
        self.assertTrue(second["observation"]["stop"]["should_stop"])
        self.assertEqual(second["next_action"], "rank")

    def test_stop_does_not_overwrite_executed_iteration_history(self):
        item = repo("focus/timer", 4, description="Pomodoro timer")
        github = FrozenGitHub(
            [("focus", [item]), ("wellbeing", [item]), ("pomodoro", [item])],
            readmes={"focus/timer": "# Timer\nPomodoro.\n## Usage\nRun it."},
        )
        request = SearchRequest.from_dict({
            "request": "focus",
            "problem_concepts": ["focus"],
            "mechanisms": ["pomodoro"],
        })
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                engine = SearchEngine(store, github, relation_budget=0)
                first = engine.search(request, "deep")
                continued = engine.iterate(first["search_id"], {
                    "decision": "continue",
                    "reason": "try a new wording",
                    "concepts": ["digital wellbeing"],
                })
                stopped = engine.iterate(first["search_id"], {
                    "decision": "stop",
                    "stop_reason": "enough coverage",
                })
                events = store.list_iterations(first["search_id"])
            finally:
                store.close()

        self.assertEqual([item["event"] for item in events], ["iterate", "stop"])
        self.assertEqual(events[0]["hypothesis"]["concepts"], ["digital wellbeing"])
        self.assertEqual(events[1]["event"], "stop")
        self.assertEqual(stopped["stop_reason"], "agent_stop")
        self.assertNotEqual(events[0]["hypothesis"], events[1]["hypothesis"])
        self.assertEqual(continued["iteration"], events[0]["iteration"])
        self.assertEqual(events[1]["iteration"], events[0]["iteration"])

    def test_single_low_gain_round_is_advisory_until_it_repeats(self):
        item = repo("focus/timer", 4, description="Pomodoro timer")
        github = FrozenGitHub(
            [("focus", [item]), ("pomodoro", [item]), ("timer", [item]), ("interval", [item])],
            readmes={"focus/timer": "# Timer\nPomodoro.\n## Usage\nRun it."},
        )
        request = SearchRequest.from_dict({
            "request": "focus",
            "problem_concepts": ["focus"],
            "mechanisms": ["pomodoro"],
        })
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                engine = SearchEngine(store, github, relation_budget=0, max_iterations=3)
                first = engine.search(request, "deep")
                second = engine.iterate(first["search_id"], {
                    "decision": "continue",
                    "reason": "same mechanism, new wording",
                    "concepts": ["pomodoro timer"],
                })
                third = engine.iterate(first["search_id"], {
                    "decision": "continue",
                    "reason": "still the same mechanism",
                    "concepts": ["interval timer"],
                })
            finally:
                store.close()

        self.assertIn("no_new_mechanism", second["observation"]["stop"]["signals"])
        self.assertFalse(second["observation"]["stop"]["should_stop"])
        self.assertEqual(second["next_action"], "iterate")
        self.assertEqual(second["observation"]["stop"]["consecutive_no_gain"], 1)
        self.assertTrue(third["observation"]["stop"]["should_stop"])
        self.assertIn("consecutive_no_gain", third["observation"]["stop"]["reasons"])
        self.assertEqual(third["next_action"], "rank")

    def test_duplicate_query_rate_ignores_round_budget_skips(self):
        item = repo("focus/timer", 4, description="Pomodoro timer")
        github = FrozenGitHub(
            [("focus", [item]), ("pomodoro", [item]), ("wellbeing", [item]), ("biofeedback", [item])],
            readmes={"focus/timer": "# Timer\nPomodoro.\n## Usage\nRun it."},
        )
        request = SearchRequest.from_dict({
            "request": "focus",
            "problem_concepts": ["focus"],
            "mechanisms": ["pomodoro"],
        })
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                engine = SearchEngine(store, github, relation_budget=0, queries_per_iteration=1)
                first = engine.search(request, "deep")
                engine.iterate(first["search_id"], {
                    "decision": "continue",
                    "reason": "one executed, extras are round budget",
                    "concepts": ["digital wellbeing", "biofeedback", "commitment device"],
                })
                history = store.query_history(first["search_id"])
                loop = session_loop_diagnostics(store, first["search_id"])
            finally:
                store.close()

        reasons = {item.get("skip_reason") for item in history if item.get("skipped")}
        self.assertIn("round_budget", reasons)
        skipped = [item for item in history if item.get("skipped")]
        duplicates = [item for item in history if item.get("skip_reason") == "duplicate"]
        self.assertEqual(loop["duplicate_query_rate"], round(len(duplicates) / max(1, len(history)), 3))
        self.assertLess(loop["duplicate_query_rate"], len(skipped) / max(1, len(history)))
        self.assertEqual(loop["skipped_by_reason"].get("round_budget"), 2)

    def test_observe_restores_session_without_github_or_writes(self):
        item = repo("focus/timer", 4, description="Pomodoro timer")
        github = FrozenGitHub(
            [("focus", [item]), ("pomodoro", [item])],
            readmes={"focus/timer": "# Timer\nPomodoro.\n## Usage\nRun it."},
        )
        request = SearchRequest.from_dict({
            "request": "focus",
            "problem_concepts": ["focus"],
            "mechanisms": ["pomodoro"],
        })
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                engine = SearchEngine(store, github, relation_budget=0)
                first = engine.search(request, "deep")
                snapshots_before = len(store.boundary_snapshots(first["search_id"]))
                queries_before = store.query_count(first["search_id"])
                calls_before = dict(github.request_counts)
                viewed = SearchEngine(store, None).observe(first["search_id"])
                snapshots_after = len(store.boundary_snapshots(first["search_id"]))
                queries_after = store.query_count(first["search_id"])
            finally:
                store.close()

        self.assertEqual(viewed["search_id"], first["search_id"])
        self.assertEqual(viewed["mode"], "deep")
        self.assertEqual(viewed["next_action"], "iterate")
        self.assertTrue(viewed["can_iterate"])
        self.assertIn("observation", viewed)
        self.assertIn("remaining_budget", viewed)
        self.assertIn("boundary", viewed)
        self.assertNotIn("candidates", viewed)
        self.assertEqual(github.request_counts, calls_before)
        self.assertEqual(snapshots_after, snapshots_before)
        self.assertEqual(queries_after, queries_before)

    def test_observe_allows_user_requested_iterate_after_rank(self):
        item = repo("focus/timer", 4, description="Pomodoro timer")
        github = FrozenGitHub(
            [("focus", [item]), ("pomodoro", [item]), ("biofeedback", [item])],
            readmes={"focus/timer": "# Timer\nPomodoro.\n## Usage\nRun it."},
        )
        request = SearchRequest.from_dict({
            "request": "focus",
            "problem_concepts": ["focus"],
            "mechanisms": ["pomodoro"],
        })
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                engine = SearchEngine(store, github, relation_budget=0)
                first = engine.search(request, "deep")
                stored = store.get_candidate("focus/timer", first["search_id"])
                excerpt = next(
                    evidence["id"] for evidence in stored["evidence"]
                    if evidence["kind"] == "readme_excerpt"
                )
                rank_search(store, first["search_id"], [{
                    "repo": "focus/timer", "relevance": 90, "uniqueness": 70,
                    "usability": 80, "difficulty": "easy", "use_case": "Pomodoro workflow",
                    "category": "focus", "artifact_type": "application",
                    "reasons": [{"text": "Documented workflow", "evidence_ids": [excerpt]}],
                    "risks": [{
                        "text": "Check metadata",
                        "evidence_ids": ["repo:focus/timer:metadata"],
                    }],
                }])
                after_rank = SearchEngine(store, None).observe(first["search_id"])
                continued = engine.iterate(first["search_id"], {
                    "decision": "continue",
                    "reason": "user asked for more",
                    "concepts": ["biofeedback"],
                })
                exhausted = SearchEngine(
                    store, github, relation_budget=0, max_iterations=0,
                ).search(request, "deep", refresh=True)
                blocked = SearchEngine(store, None).observe(exhausted["search_id"])
            finally:
                store.close()

        self.assertEqual(after_rank["next_action"], "done")
        self.assertTrue(after_rank["can_iterate"])
        self.assertEqual(continued["search_id"], first["search_id"])
        self.assertFalse(blocked["can_iterate"])

    def test_deep_mode_uses_a_larger_candidate_pool_than_quick(self):
        item = repo("focus/timer", 4, description="Pomodoro timer")
        github = FrozenGitHub(
            [("focus", [item]), ("pomodoro", [item])],
            readmes={"focus/timer": "# Timer\nPomodoro.\n## Usage\nRun it."},
        )
        request = SearchRequest.from_dict({
            "request": "focus",
            "problem_concepts": ["focus"],
            "mechanisms": ["pomodoro"],
        })
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                engine = SearchEngine(store, github, relation_budget=0)
                deep = engine.search(request, "deep")
                quick = engine.search(request, "quick", refresh=True)
                deep_limit = store.get_session_state(deep["search_id"])["candidate_limit"]
                quick_limit = store.get_session_state(quick["search_id"])["candidate_limit"]
            finally:
                store.close()

        self.assertEqual(deep_limit, 250)
        self.assertEqual(quick_limit, 100)

    def test_cassette_replay_matches_iterate_output(self):
        class Delegate:
            rate_limits = {}

            def search_repositories(self, query, per_page=10, sort="stars"):
                if "wellbeing" in query.lower():
                    return github_module.ApiResult({"items": [repo(
                        "habits/wellbeing", 3, description="Digital wellbeing dashboard",
                    )]})
                return github_module.ApiResult({"items": [repo(
                    "focus/timer", 4, description="Pomodoro focus timer",
                    topics=["digital-wellbeing"],
                )]})

            def readme(self, full_name):
                if full_name.lower() == "habits/wellbeing":
                    return github_module.ApiResult("# Wellbeing\nDigital wellbeing.\n## Usage\nRun it.")
                return github_module.ApiResult("# Timer\nA Pomodoro focus timer.\n## Usage\nRun it.")

            def latest_release(self, full_name):
                raise github_module.GitHubNotFoundError("missing")

        request = SearchRequest.from_dict({
            "request": "focus",
            "problem_concepts": ["focus"],
            "mechanisms": ["pomodoro"],
            "exploration_directions": ["biofeedback"],
        })
        hypothesis = {
            "decision": "continue",
            "reason": "promote discovered wellbeing term",
            "promote_discovered_terms": ["digital wellbeing"],
            "concepts": ["digital wellbeing"],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cassette = root / "iterate.json.gz"
            record_store = Store(root / "record")
            try:
                recorder = CassetteGitHub(github_module, cassette, delegate=Delegate())
                engine = SearchEngine(record_store, recorder, relation_budget=0)
                recorded_search = engine.search(request, "deep")
                recorded = engine.iterate(recorded_search["search_id"], hypothesis)
                recorder.save()
                recorded_loop = session_loop_diagnostics(record_store, recorded_search["search_id"])
            finally:
                record_store.close()
            replay_store = Store(root / "replay")
            try:
                replay = CassetteGitHub(github_module, cassette, delegate=None)
                engine = SearchEngine(replay_store, replay, relation_budget=0)
                replayed_search = engine.search(request, "deep")
                replayed = engine.iterate(replayed_search["search_id"], hypothesis)
                replayed_loop = session_loop_diagnostics(replay_store, replayed_search["search_id"])
            finally:
                replay_store.close()

        self.assertEqual(replayed["boundary"], recorded["boundary"])
        self.assertEqual(replayed["boundary_delta"], recorded["boundary_delta"])
        self.assertEqual(replayed["observation"]["query_summary"], recorded["observation"]["query_summary"])
        self.assertEqual(replayed_loop["mode"], "agentic-loop")
        self.assertEqual(replayed_loop["iterations_used"], recorded_loop["iterations_used"])


if __name__ == "__main__":
    unittest.main()
