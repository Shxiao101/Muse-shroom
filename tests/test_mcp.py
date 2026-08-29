import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from muse_shroom.cli import main
from muse_shroom.models import SearchRequest
from muse_shroom.search import SearchEngine
from muse_shroom.storage import Store

from tests.helpers import FrozenGitHub, repo


def _mcp_installed() -> bool:
    return importlib.util.find_spec("mcp") is not None


def _dedicated_mcp_run() -> bool:
    args = sys.argv
    if "discover" in args:
        return False
    return Path(sys.argv[0]).name == "test_mcp.py" or any(
        token == "tests.test_mcp"
        or token.startswith("tests.test_mcp.")
        or Path(str(token)).name == "test_mcp.py"
        for token in args
    )


# Skip only when the mcp package is absent during a full Core discover.
# `python -m unittest tests.test_mcp` without the extra, or an installed but
# incompatible SDK, must fail rather than skip.
if not _mcp_installed():
    if _dedicated_mcp_run():
        raise ImportError(
            "MCP tests require the optional extra. "
            "Install with: python -m pip install -e '.[mcp]' "
            "then run: python -m unittest tests.test_mcp -v"
        )
else:
    from mcp import Client, StdioServerParameters
    from muse_shroom.mcp_server import create_server


SECRET = "ghp_TESTTOKEN_DO_NOT_LEAK_9x7k"
REQUEST = {
    "request": "focus",
    "problem_concepts": ["focus"],
    "mechanisms": ["pomodoro"],
}
ROOT = Path(__file__).resolve().parents[1]


def _github() -> FrozenGitHub:
    item = repo("focus/timer", 4, description="Pomodoro timer")
    return FrozenGitHub(
        [("focus", [item]), ("pomodoro", [item]), ("biofeedback", [item])],
        readmes={"focus/timer": "# Timer\nPomodoro.\n## Usage\nRun it."},
    )


def _excerpt(store: Store, search_id: str, name: str = "focus/timer") -> str:
    stored = store.get_candidate(name, search_id)
    return next(
        evidence["id"] for evidence in stored["evidence"]
        if evidence["kind"] == "readme_excerpt"
    )


def _assessment(excerpt: str, name: str = "focus/timer") -> dict:
    return {
        "repo": name, "relevance": 90, "uniqueness": 70, "usability": 80,
        "difficulty": "easy", "use_case": "Pomodoro workflow",
        "category": "focus", "artifact_type": "application",
        "reasons": [{"text": "Documented workflow", "evidence_ids": [excerpt]}],
        "risks": [{"text": "Check metadata", "evidence_ids": [f"repo:{name}:metadata"]}],
    }


def _payload(result) -> dict:
    if getattr(result, "is_error", False):
        raise AssertionError("tool error: " + _error_text(result))
    data = result.structured_content
    if isinstance(data, dict) and "search_id" not in data and isinstance(data.get("result"), dict):
        return data["result"]
    if data is None:
        for item in result.content or []:
            text = getattr(item, "text", None)
            if text:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
        raise AssertionError("tool returned no structured payload")
    return data


def _error_text(result) -> str:
    return " ".join(getattr(item, "text", str(item)) for item in (result.content or []))


def _tool_names(listed) -> list[str]:
    tools = listed.tools if hasattr(listed, "tools") else listed
    return [tool.name for tool in tools]


def _dump(value) -> str:
    try:
        return json.dumps(value, default=str)
    except TypeError:
        return str(value)


@unittest.skipUnless(
    _mcp_installed(),
    "mcp extra is not installed; Core tests do not require it. "
    "For MCP tests: python -m pip install -e '.[mcp]' && python -m unittest tests.test_mcp -v",
)
class McpAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_in_memory_lists_expected_tools(self):
        mcp = create_server(data_dir=tempfile.mkdtemp(), github=_github(), log_level="ERROR")
        async with Client(mcp) as client:
            names = _tool_names(await client.list_tools())
        for name in ("muse_search", "muse_observe", "muse_iterate", "muse_rank", "muse_status"):
            self.assertIn(name, names)
        self.assertIn("muse_inspect", names)
        self.assertNotIn("muse_expand", names)
        self.assertNotIn("expand", names)

    async def test_core_cli_mcp_parity_on_search_observe_iterate_rank(self):
        github = _github()
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            try:
                core = SearchEngine(store, github).search(SearchRequest.from_dict(REQUEST), "deep")
                excerpt = _excerpt(store, core["search_id"])
            finally:
                store.close()
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["--data-dir", directory, "observe", "--search-id", core["search_id"]])
            self.assertEqual(code, 0)
            cli_observe = json.loads(stdout.getvalue())
            mcp = create_server(data_dir=directory, github=github, log_level="ERROR")
            async with Client(mcp) as client:
                mcp_observe = _payload(await client.call_tool(
                    "muse_observe", {"search_id": core["search_id"]},
                ))
                mcp_iterate = _payload(await client.call_tool("muse_iterate", {
                    "search_id": core["search_id"],
                    "hypothesis": {
                        "decision": "continue",
                        "reason": "cover biofeedback",
                        "concepts": ["biofeedback"],
                    },
                }))
                mcp_rank = _payload(await client.call_tool("muse_rank", {
                    "search_id": core["search_id"],
                    "assessments": [_assessment(excerpt)],
                }))
            with tempfile.TemporaryDirectory() as other:
                mcp_fresh = create_server(data_dir=other, github=_github(), log_level="ERROR")
                async with Client(mcp_fresh) as client:
                    mcp_search = _payload(await client.call_tool("muse_search", {
                        "request": REQUEST, "mode": "deep",
                    }))
            self.assertEqual(mcp_observe["search_id"], core["search_id"])
            self.assertEqual(mcp_observe["next_action"], cli_observe["next_action"])
            self.assertEqual(mcp_observe["can_iterate"], cli_observe["can_iterate"])
            self.assertEqual(mcp_observe["mode"], cli_observe["mode"])
            self.assertEqual(mcp_observe["iteration"], cli_observe["iteration"])
            self.assertEqual(cli_observe["mode"], "deep")
            self.assertEqual(
                [item["full_name"] for item in mcp_search["candidates"]],
                [item["full_name"] for item in core["candidates"]],
            )
            self.assertEqual(mcp_search["next_action"], core["next_action"])
            self.assertEqual(mcp_iterate["search_id"], core["search_id"])
            self.assertEqual(mcp_rank["next_action"], "done")
            self.assertEqual(mcp_rank["search_id"], core["search_id"])
            self.assertIn("focus/timer", mcp_rank["display_order"])
            self.assertEqual(set(mcp_rank["buckets"]), {"popular", "gems", "adjacent"})

    async def test_observe_is_read_only(self):
        github = _github()
        with tempfile.TemporaryDirectory() as directory:
            mcp = create_server(data_dir=directory, github=github, log_level="ERROR")
            async with Client(mcp) as client:
                searched = _payload(await client.call_tool("muse_search", {
                    "request": REQUEST, "mode": "deep",
                }))
                search_id = searched["search_id"]
                store = Store(directory)
                try:
                    snapshots_before = len(store.boundary_snapshots(search_id))
                    queries_before = store.query_count(search_id)
                    calls_before = dict(github.request_counts)
                    viewed = _payload(await client.call_tool("muse_observe", {"search_id": search_id}))
                    snapshots_after = len(store.boundary_snapshots(search_id))
                    queries_after = store.query_count(search_id)
                finally:
                    store.close()
            self.assertEqual(viewed["search_id"], search_id)
            self.assertNotIn("candidates", viewed)
            self.assertEqual(github.request_counts, calls_before)
            self.assertEqual(snapshots_after, snapshots_before)
            self.assertEqual(queries_after, queries_before)

    async def test_session_survives_new_client_and_new_server(self):
        github = _github()
        with tempfile.TemporaryDirectory() as directory:
            mcp = create_server(data_dir=directory, github=github, log_level="ERROR")
            async with Client(mcp) as client:
                searched = _payload(await client.call_tool("muse_search", {
                    "request": REQUEST, "mode": "deep",
                }))
            search_id = searched["search_id"]
            async with Client(mcp) as client:
                restored = _payload(await client.call_tool("muse_observe", {"search_id": search_id}))
            restarted = create_server(data_dir=directory, github=github, log_level="ERROR")
            async with Client(restarted) as client:
                after_restart = _payload(await client.call_tool("muse_observe", {"search_id": search_id}))
        self.assertEqual(restored["search_id"], search_id)
        self.assertEqual(after_restart["search_id"], search_id)
        self.assertEqual(after_restart["mode"], "deep")
        self.assertEqual(after_restart["next_action"], searched["next_action"])

    async def test_user_requested_iterate_after_rank(self):
        github = _github()
        with tempfile.TemporaryDirectory() as directory:
            mcp = create_server(data_dir=directory, github=github, log_level="ERROR")
            async with Client(mcp) as client:
                searched = _payload(await client.call_tool("muse_search", {
                    "request": REQUEST, "mode": "deep",
                }))
                search_id = searched["search_id"]
                store = Store(directory)
                try:
                    excerpt = _excerpt(store, search_id)
                finally:
                    store.close()
                ranked = _payload(await client.call_tool("muse_rank", {
                    "search_id": search_id,
                    "assessments": [_assessment(excerpt)],
                }))
                after_rank = _payload(await client.call_tool("muse_observe", {"search_id": search_id}))
                continued = _payload(await client.call_tool("muse_iterate", {
                    "search_id": search_id,
                    "hypothesis": {
                        "decision": "continue",
                        "reason": "user asked for more",
                        "concepts": ["biofeedback"],
                    },
                }))
        self.assertEqual(ranked["next_action"], "done")
        self.assertEqual(after_rank["next_action"], "done")
        self.assertTrue(after_rank["can_iterate"])
        self.assertEqual(continued["search_id"], search_id)

    async def test_contract_errors_are_tool_errors_and_do_not_create_sessions(self):
        github = _github()
        with tempfile.TemporaryDirectory() as directory:
            mcp = create_server(data_dir=directory, github=github, log_level="ERROR")
            async with Client(mcp) as client:
                missing = await client.call_tool("muse_observe", {"search_id": "missing-id"})
                bad_request = await client.call_tool("muse_search", {
                    "request": {"request": "missing concepts"},
                })
                searched = _payload(await client.call_tool("muse_search", {
                    "request": REQUEST, "mode": "quick",
                }))
                bad_hypothesis = await client.call_tool("muse_iterate", {
                    "search_id": searched["search_id"],
                    "hypothesis": {"decision": "maybe"},
                })
                bad_evidence = await client.call_tool("muse_rank", {
                    "search_id": searched["search_id"],
                    "assessments": [_assessment("made-up")],
                })
                bad_mechanism = await client.call_tool("muse_rank", {
                    "search_id": searched["search_id"],
                    "assessments": [{
                        **_assessment("repo:focus/timer:metadata"),
                        "use_case": "unknown",
                        "mechanism": "not-a-real-mechanism",
                        "reasons": [{
                            "text": "metadata only",
                            "evidence_ids": ["repo:focus/timer:metadata"],
                        }],
                    }],
                })
                store = Store(directory)
                try:
                    rows = store.db.execute("SELECT id FROM searches").fetchall()
                    ids = {row[0] for row in rows}
                finally:
                    store.close()
            self.assertTrue(missing.is_error)
            self.assertIn("KeyError", _error_text(missing))
            self.assertNotIn("{\"ok\": false", _error_text(missing))
            self.assertTrue(bad_request.is_error)
            self.assertIn("ContractError", _error_text(bad_request))
            self.assertTrue(bad_hypothesis.is_error)
            self.assertIn("ContractError", _error_text(bad_hypothesis))
            self.assertTrue(bad_evidence.is_error)
            self.assertIn("ContractError", _error_text(bad_evidence))
            self.assertTrue(bad_mechanism.is_error)
            self.assertIn("ContractError", _error_text(bad_mechanism))
            self.assertEqual(ids, {searched["search_id"]})
            self.assertNotIn("missing-id", ids)

    async def test_credentials_never_appear_in_schema_result_or_error(self):
        github = _github()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"GITHUB_TOKEN": SECRET}):
            mcp = create_server(data_dir=directory, github=github, log_level="ERROR")
            async with Client(mcp) as client:
                listed = await client.list_tools()
                status = await client.call_tool("muse_status", {})
                failed = await client.call_tool("muse_observe", {"search_id": "missing-id"})
                payload = _payload(status)
            blob = " ".join((
                _dump(listed),
                _dump(status.structured_content),
                _error_text(failed),
                _dump(payload),
            ))
            for tool in (listed.tools if hasattr(listed, "tools") else listed):
                schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
                blob += _dump(schema)
                blob += f" {getattr(tool, 'name', '')} {getattr(tool, 'description', '') or ''}"
        self.assertTrue(payload["credential_configured"])
        self.assertNotIn("token", payload)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("ghp_TESTTOKEN", blob)
        self.assertNotIn("Bearer", blob)

    async def test_stdio_lists_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
            env.pop("GITHUB_TOKEN", None)
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "muse_shroom.mcp_server", "--data-dir", directory],
                env=env,
                cwd=str(ROOT),
            )
            async with Client(params) as client:
                names = _tool_names(await client.list_tools())
        for name in ("muse_search", "muse_observe", "muse_iterate", "muse_rank"):
            self.assertIn(name, names)
        self.assertNotIn("muse_expand", names)


if __name__ == "__main__":
    unittest.main()
