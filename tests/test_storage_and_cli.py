import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from muse_shroom.cli import main
from muse_shroom.storage import Store

from tests.helpers import repo


class StorageAndCliTests(unittest.TestCase):
    def test_doctor_reports_missing_token_without_printing_value(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), \
             patch("muse_shroom.cli.resolve_token", return_value=None):
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["--data-dir", directory, "doctor"])
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 2)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["github_token"], "missing")

    def test_star_growth_requires_two_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.create_search("a", {"request": "x"}, "quick")
            store.save_candidate("a", repo("owner/repo", 1))
            self.assertEqual(len(store.star_history("owner/repo")), 1)
            store.close()

    def test_bad_json_contract_returns_machine_readable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            request_path = os.path.join(directory, "bad.json")
            with open(request_path, "w", encoding="utf-8") as handle:
                json.dump({"request": "missing concepts"}, handle)
            stderr = io.StringIO()
            with redirect_stderr(stderr), patch.dict(os.environ, {"GITHUB_TOKEN": "not-printed"}):
                code = main(["--data-dir", directory, "search", "--request", request_path])
            self.assertEqual(code, 2)
            self.assertNotIn("not-printed", stderr.getvalue())
            self.assertEqual(json.loads(stderr.getvalue())["error"], "ContractError")

    def test_feedback_accepts_json_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            stdin = io.StringIO('{"repo":"Owner/Repo","relevant":true,"interesting":false,"too_hard":null}')
            stdout = io.StringIO()
            with patch("sys.stdin", stdin), redirect_stdout(stdout):
                code = main(["--data-dir", directory, "feedback", "--input", "-"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["repo"], "owner/repo")

    def test_json_output_supports_non_gbk_characters(self):
        from muse_shroom.cli import _emit

        output = io.StringIO()
        with redirect_stdout(output):
            _emit({"description": "music 🎶 工具"})
        self.assertEqual(json.loads(output.getvalue())["description"], "music 🎶 工具")

    def test_interactive_stdin_is_rejected_for_json(self):
        class Tty(io.StringIO):
            def isatty(self) -> bool:
                return True

        with tempfile.TemporaryDirectory() as directory:
            stderr = io.StringIO()
            with patch("sys.stdin", Tty()), redirect_stderr(stderr):
                code = main(["--data-dir", directory, "feedback", "--input", "-"])
            self.assertEqual(code, 2)
            payload = json.loads(stderr.getvalue())
            self.assertEqual(payload["error"], "ContractError")
            self.assertIn("UTF-8", payload["message"])

    def test_search_writes_output_file_and_prints_receipt(self):
        from muse_shroom.search import SearchEngine
        from muse_shroom.models import SearchRequest
        from tests.helpers import FrozenGitHub, repo

        with tempfile.TemporaryDirectory() as directory:
            request_path = os.path.join(directory, "request.json")
            output_path = os.path.join(directory, "search.json")
            with open(request_path, "w", encoding="utf-8") as handle:
                json.dump({"request": "music", "core_concepts": ["music"]}, handle)
            github = FrozenGitHub(
                [("music", [repo("tools/useful", 10, description="Useful music tool")])],
                readmes={"tools/useful": "# Useful\n## Installation\nRun it."},
            )
            stdout = io.StringIO()
            with patch("muse_shroom.cli.GitHubClient", return_value=github), redirect_stdout(stdout):
                code = main([
                    "--data-dir", directory, "search", "--request", request_path,
                    "--output", output_path,
                ])
            self.assertEqual(code, 0)
            receipt = json.loads(stdout.getvalue())
            self.assertTrue(receipt["ok"])
            self.assertEqual(receipt["output"], output_path)
            saved = json.loads(Path(output_path).read_text(encoding="utf-8"))
            self.assertEqual(saved["search_id"], receipt["search_id"])
            self.assertEqual(saved["next_action"], "rank")
            self.assertEqual(receipt["next_action"], "rank")
            self.assertLessEqual(len(saved["candidates"][0]["evidence"]), 3)

    def test_identical_search_is_reused_without_github(self):
        from muse_shroom.search import SearchEngine
        from muse_shroom.models import SearchRequest
        from tests.helpers import FrozenGitHub, repo

        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            github = FrozenGitHub(
                [("music", [repo("tools/useful", 10, description="Useful music tool")])],
                readmes={"tools/useful": "# Useful\n## Installation\nRun it."},
            )
            engine = SearchEngine(store, github)
            request = SearchRequest.from_dict({"request": "music", "core_concepts": ["music"]})
            first = engine.search(request, "quick")
            search_calls = github.request_counts["search"]
            second = engine.search(request, "quick")
            self.assertTrue(second["reused"])
            self.assertEqual(second["search_id"], first["search_id"])
            self.assertEqual(github.request_counts["search"], search_calls)
            refreshed = engine.search(request, "quick", refresh=True)
            self.assertNotEqual(refreshed["search_id"], first["search_id"])
            self.assertGreater(github.request_counts["search"], search_calls)
            store.close()


if __name__ == "__main__":
    unittest.main()
