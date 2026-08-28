import json
import base64
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from muse_shroom.github import GitHubClient, GitHubRateLimitError
from muse_shroom.storage import Store


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

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
        failure = urllib.error.HTTPError("url", 403, "limited", {}, None)
        with patch("urllib.request.urlopen", side_effect=failure):
            cached = self.client.search_repositories("music ai")
        self.assertTrue(cached.stale)
        self.assertIsNotNone(cached.cached_at)
        self.assertEqual(cached.data["items"][0]["full_name"], "a/b")

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
