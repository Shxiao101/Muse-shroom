import json
import base64
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from muse_shroom.github import (
    GitHubAuthenticationError, GitHubClient, GitHubError,
    GitHubNotFoundError, GitHubRateLimitError,
)
from muse_shroom.storage import Store


class Response:
    def __init__(self, payload, headers=None):
        self.payload = json.dumps(payload).encode()
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class GitHubClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.client = GitHubClient(self.store, token="secret-test-token")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_cached_response_is_explicitly_stale_on_403(self):
        with patch("urllib.request.urlopen", return_value=Response({"items": [{"full_name": "a/b"}]})):
            fresh = self.client.search_repositories("music ai")
        self.assertFalse(fresh.stale)
        failure = urllib.error.HTTPError("url", 403, "limited", {"X-RateLimit-Remaining": "0"}, None)
        with patch("urllib.request.urlopen", side_effect=failure):
            cached = self.client.search_repositories("music ai")
        self.assertTrue(cached.stale)
        self.assertIsNotNone(cached.cached_at)
        self.assertEqual(cached.data["items"][0]["full_name"], "a/b")

    def test_credential_rejection_never_uses_cache(self):
        with patch("urllib.request.urlopen", return_value=Response({"items": []})):
            self.client.search_repositories("music ai")
        failure = urllib.error.HTTPError("url", 401, "bad credentials", {}, None)
        with patch("urllib.request.urlopen", side_effect=failure):
            with self.assertRaises(GitHubAuthenticationError):
                self.client.search_repositories("music ai")

    def test_permission_denial_is_not_treated_as_rate_limit(self):
        failure = urllib.error.HTTPError("url", 403, "forbidden", {"X-RateLimit-Remaining": "10"}, None)
        with patch("urllib.request.urlopen", side_effect=failure):
            with self.assertRaises(GitHubError):
                self.client.search_repositories("permission denied")

    def test_not_found_never_uses_cache(self):
        with patch("urllib.request.urlopen", return_value=Response({"full_name": "owner/repo"})):
            self.client.repository("owner/repo")
        failure = urllib.error.HTTPError("url", 404, "missing", {}, None)
        with patch("urllib.request.urlopen", side_effect=failure):
            with self.assertRaises(GitHubNotFoundError):
                self.client.repository("owner/repo")

    def test_server_failure_can_use_explicit_stale_cache(self):
        with patch("urllib.request.urlopen", return_value=Response({"items": []})):
            self.client.search_repositories("server failure")
        failure = urllib.error.HTTPError("url", 503, "unavailable", {}, None)
        with patch("urllib.request.urlopen", side_effect=failure):
            result = self.client.search_repositories("server failure")
        self.assertTrue(result.stale)

    def test_request_counts_keep_rate_limit_resources_separate(self):
        with patch("urllib.request.urlopen", return_value=Response({"items": []})):
            self.client.search_repositories("x")
            self.client.search_code("x")
        with patch("urllib.request.urlopen", return_value=Response({"full_name": "owner/repo"})):
            self.client.repository("owner/repo")
        self.assertEqual(self.client.request_counts, {"core": 1, "search": 1, "code_search": 1})

    def test_rate_limit_without_cache_fails(self):
        failure = urllib.error.HTTPError("url", 429, "limited", {}, None)
        with patch("urllib.request.urlopen", side_effect=failure):
            with self.assertRaises(GitHubRateLimitError):
                self.client.search_repositories("uncached")

    def test_readme_content_and_sha_are_preserved(self):
        payload = {"encoding": "base64", "content": base64.b64encode("# Hello".encode()).decode(), "sha": "abc123"}
        with patch("urllib.request.urlopen", return_value=Response(payload)):
            result = self.client.readme("owner/repo")
        self.assertEqual(result.data, {"text": "# Hello", "sha": "abc123"})


if __name__ == "__main__":
    unittest.main()
