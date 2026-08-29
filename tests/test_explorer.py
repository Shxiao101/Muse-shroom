import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from muse_shroom.explorer.read_model import ExplorerReadModel, MAX_GRAPH_REPOS
from muse_shroom.explorer.server import build_server
from muse_shroom.models import SearchRequest
from muse_shroom.ranking import rank_search
from muse_shroom.search import SearchEngine
from muse_shroom.storage import Store

from tests.helpers import FrozenGitHub, repo


REQUEST = {
    "request": "focus tools",
    "problem_concepts": ["focus"],
    "mechanisms": ["pomodoro"],
    "exploration_directions": ["biofeedback", "commitment device"],
}


def _github() -> FrozenGitHub:
    item = repo("focus/timer", 4, description="Pomodoro timer for focus", topics=["pomodoro"])
    extra = repo("well/bio", 8, description="Biofeedback wearable for attention", topics=["biofeedback"])
    return FrozenGitHub(
        [
            ("focus", [item, extra]),
            ("pomodoro", [item]),
            ("biofeedback", [extra]),
            ("commitment", [extra]),
        ],
        readmes={
            "focus/timer": "# Timer\nPomodoro.\n## Usage\nRun it.",
            "well/bio": "# Bio\nBiofeedback.\n## Usage\nWear it.",
        },
    )


def _excerpt(store: Store, search_id: str, name: str) -> str:
    stored = store.get_candidate(name, search_id)
    return next(
        evidence["id"] for evidence in stored["evidence"]
        if evidence["kind"] == "readme_excerpt"
    )


def _assessment(excerpt: str, name: str) -> dict:
    return {
        "repo": name, "relevance": 90, "uniqueness": 70, "usability": 80,
        "difficulty": "easy", "use_case": "Documented workflow",
        "category": "focus", "artifact_type": "application",
        "reasons": [{"text": "Documented workflow", "evidence_ids": [excerpt]}],
        "risks": [{"text": "Check metadata", "evidence_ids": [f"repo:{name}:metadata"]}],
    }


def _session(directory: str, *, iterate: bool = False, rank: bool = False):
    store = Store(directory)
    github = _github()
    engine = SearchEngine(store, github, relation_budget=0)
    searched = engine.search(SearchRequest.from_dict(REQUEST), "deep")
    search_id = searched["search_id"]
    if iterate:
        engine.iterate(search_id, {
            "decision": "continue",
            "reason": "cover biofeedback",
            "target_direction": "biofeedback",
            "concepts": ["biofeedback"],
            "negative_directions": ["DOM focus"],
            "rejected_directions": ["timer toy"],
        })
    if rank:
        names = ["focus/timer"]
        if store.get_candidate("well/bio", search_id):
            names.append("well/bio")
        assessments = []
        for name in names:
            try:
                excerpt = _excerpt(store, search_id, name)
            except StopIteration:
                continue
            assessments.append(_assessment(excerpt, name))
        if assessments:
            rank_search(store, search_id, assessments)
    return store, github, search_id


class ExplorerReadModelTests(unittest.TestCase):
    def test_summary_matches_sqlite_and_omits_candidate_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _github, search_id = _session(directory)
            try:
                listed = ExplorerReadModel(data_dir=directory).list_searches()
                summary = ExplorerReadModel(data_dir=directory).search_summary(search_id)
                row = store.load_search(search_id)
            finally:
                store.close()
            item = listed["searches"][0]
            self.assertEqual(item["search_id"], search_id)
            self.assertEqual(item["request"], "focus tools")
            self.assertEqual(item["mode"], "deep")
            self.assertEqual(item["status"], "searched")
            self.assertNotIn("candidates", item)
            self.assertEqual(summary["search_id"], search_id)
            self.assertEqual(summary["mode"], row["mode"])
            self.assertIn("focus", [c["term"] for c in summary["problem_concepts"]])
            self.assertNotIn("candidates", summary)
            self.assertLess(summary["mechanism_count"] + 1, 250)

    def test_boundary_states_do_not_mix_and_discovered_terms_are_not_confirmed(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _github, search_id = _session(directory, iterate=True)
            try:
                view = ExplorerReadModel(data_dir=directory).boundary_view(search_id)
            finally:
                store.close()
            by_name = {item["name"].casefold(): item for item in view["mechanisms"]}
            if "pomodoro" in by_name:
                self.assertIn("requested", by_name["pomodoro"]["states"])
                self.assertEqual(by_name["pomodoro"]["origin"], "requested_mechanism")
            unexplored = {name.casefold() for name in view["overview"]["unexplored"]}
            recalled = {name.casefold() for name in view["overview"]["recalled"]}
            self.assertFalse(unexplored & recalled)
            for term in view["overview"]["discovered_terms"]:
                node = by_name.get(term.casefold())
                if node:
                    self.assertTrue(node["confirmed"])
                else:
                    self.assertNotIn(term.casefold(), recalled)
            rejected = {name.casefold() for name in view["overview"]["rejected"]}
            negative = {name.casefold() for name in view["overview"]["negative"]}
            self.assertIn("timer toy", rejected)
            self.assertIn("dom focus", negative)
            self.assertIn("rejected", by_name["timer toy"]["states"])
            self.assertIn("negative", by_name["dom focus"]["states"])

    def test_timeline_order_and_delta_match_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _github, search_id = _session(directory, iterate=True, rank=True)
            try:
                snapshots = store.boundary_snapshots(search_id)
                timeline = ExplorerReadModel(data_dir=directory).iteration_timeline(search_id)
            finally:
                store.close()
            kinds = [step["kind"] for step in timeline["steps"]]
            self.assertEqual(kinds[0], "initial")
            self.assertIn("iteration", kinds)
            self.assertEqual(kinds[-1], "rank")
            self.assertEqual(len(timeline["steps"]), len(snapshots))
            for step, snapshot in zip(timeline["steps"], snapshots):
                delta = snapshot["boundary_delta"]
                self.assertEqual(step["new_mechanisms"], delta.get("new_mechanisms") or [])
                self.assertEqual(step["new_directions"], delta.get("new_directions") or [])

    def test_display_order_matches_rank_result_exactly(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _github, search_id = _session(directory, rank=True)
            try:
                ranking = store.get_ranking(search_id)
                view = ExplorerReadModel(data_dir=directory).result_view(search_id)
            finally:
                store.close()
            self.assertTrue(view["ranked"])
            summary = ExplorerReadModel(data_dir=directory).search_summary(search_id)
            self.assertEqual(summary["status"], "ranked")
            self.assertEqual(view["display_order"], ranking["display_order"])
            self.assertEqual([item["repo"] for item in view["items"]], ranking["display_order"])
            self.assertNotIn("selection_order", view)
            dumped = json.dumps(view)
            self.assertNotIn("MMR", dumped)
            self.assertNotIn("selection penalty", dumped)
            for item in view["items"]:
                self.assertIn("boundary_role", item)
                self.assertIn("new_mechanisms", item)
                self.assertIn("why_different", item)
                self.assertNotIn("components", item.get("scores") or {})

    def test_explorer_views_are_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            store, github, search_id = _session(directory, iterate=True, rank=True)
            try:
                snapshots = len(store.boundary_snapshots(search_id))
                queries = store.query_count(search_id)
                ranked = store.get_ranking(search_id) is not None
                calls = dict(github.request_counts)
                model = ExplorerReadModel(data_dir=directory)
                model.list_searches()
                model.search_summary(search_id)
                model.boundary_view(search_id)
                model.iteration_timeline(search_id)
                model.result_view(search_id)
                model.repo_detail(search_id, "focus/timer")
                self.assertEqual(len(store.boundary_snapshots(search_id)), snapshots)
                self.assertEqual(store.query_count(search_id), queries)
                self.assertEqual(store.get_ranking(search_id) is not None, ranked)
                self.assertEqual(github.request_counts, calls)
            finally:
                store.close()

    def test_large_session_does_not_send_the_full_candidate_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _github, search_id = _session(directory)
            try:
                for index in range(40):
                    store.save_candidate(search_id, repo(
                        f"pool/extra-{index}", 1, description="noise candidate",
                    ))
                view = ExplorerReadModel(data_dir=directory).boundary_view(search_id, debug=True)
                listed = ExplorerReadModel(data_dir=directory).list_searches()["searches"][0]
            finally:
                store.close()
            repo_nodes = [node for node in view["graph"]["nodes"] if node["kind"] == "repository"]
            self.assertLessEqual(len(repo_nodes), MAX_GRAPH_REPOS)
            labels = {node["label"] for node in repo_nodes}
            self.assertFalse(any(name.startswith("pool/extra-") for name in labels))
            self.assertGreaterEqual(view["candidate_count"], 40)
            self.assertNotIn("pool/extra-0", json.dumps(view["graph"]))
            self.assertNotIn("candidates", listed)


class ExplorerHttpTests(unittest.TestCase):
    def test_http_api_is_get_only_and_serves_ui(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _github, search_id = _session(directory, rank=True)
            store.close()
            server = build_server(data_dir=directory, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                base = f"http://127.0.0.1:{port}"
                home = urllib.request.urlopen(base + "/", timeout=5).read().decode("utf-8")
                self.assertIn("Muse-shroom Explorer", home)
                searches = json.loads(urllib.request.urlopen(base + "/api/searches", timeout=5).read())
                self.assertEqual(searches["searches"][0]["search_id"], search_id)
                result = json.loads(urllib.request.urlopen(
                    base + f"/api/searches/{search_id}/result", timeout=5,
                ).read())
                self.assertEqual(result["display_order"], json.loads(urllib.request.urlopen(
                    base + f"/api/searches/{search_id}/result", timeout=5,
                ).read())["display_order"])
                request = urllib.request.Request(base + "/api/searches", method="POST")
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(raised.exception.code, 501)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
