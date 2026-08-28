import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from muse_shroom.auth import AuthError, Credential, delete_saved_token, resolve_token, save_token, validate_token
from muse_shroom.cli import main


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class AuthTests(unittest.TestCase):
    def test_environment_token_has_priority_over_keyring(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": " env-token "}), \
             patch("keyring.get_password", return_value="stored-token") as stored:
            credential = resolve_token()
        self.assertEqual(credential, Credential("env-token", "environment"))
        stored.assert_not_called()

    def test_keyring_token_is_used_without_environment_override(self):
        with patch.dict(os.environ, {}, clear=True), patch("keyring.get_password", return_value="stored-token"):
            credential = resolve_token()
        self.assertEqual(credential, Credential("stored-token", "keyring"))

    def test_save_and_delete_use_system_keyring(self):
        with patch("keyring.set_password") as setter:
            save_token(" secret ")
        setter.assert_called_once_with("Muse-shroom", "github.com", "secret")
        with patch("keyring.get_password", return_value="secret"), patch("keyring.delete_password") as deleter:
            self.assertTrue(delete_saved_token())
        deleter.assert_called_once_with("Muse-shroom", "github.com")

    def test_validation_returns_login_without_returning_token(self):
        with patch("urllib.request.urlopen", return_value=Response({"login": "Shxiao101"})):
            result = validate_token("secret-token")
        self.assertEqual(result, {"login": "Shxiao101"})
        self.assertNotIn("secret-token", json.dumps(result))

    def test_invalid_token_is_rejected(self):
        failure = urllib.error.HTTPError("url", 401, "unauthorized", {}, None)
        with patch("urllib.request.urlopen", side_effect=failure):
            with self.assertRaises(AuthError):
                validate_token("bad-token")

    def test_cli_login_validates_before_saving_and_never_outputs_token(self):
        with tempfile.TemporaryDirectory() as directory:
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("sys.stdin", io.StringIO("secret-token\n")), \
                 patch("muse_shroom.cli.validate_token", return_value={"login": "Shxiao101"}), \
                 patch("muse_shroom.cli.save_token") as save, redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["--data-dir", directory, "auth", "login", "--no-browser", "--token-stdin"])
        self.assertEqual(code, 0)
        save.assert_called_once_with("secret-token")
        self.assertNotIn("secret-token", stdout.getvalue() + stderr.getvalue())

    def test_auth_status_without_token_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch("muse_shroom.cli.resolve_token", return_value=None), redirect_stdout(io.StringIO()):
            code = main(["--data-dir", directory, "auth", "status"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
