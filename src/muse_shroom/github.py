from __future__ import annotations

import hashlib
import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .auth import AuthError, resolve_token
from .storage import Store


API_VERSION = "2026-03-10"


class GitHubError(RuntimeError):
    pass


class GitHubRateLimitError(GitHubError):
    pass


class GitHubNotFoundError(GitHubError):
    pass


@dataclass(slots=True)
class ApiResult:
    data: Any
    stale: bool = False
    cached_at: str | None = None


class GitHubClient:
    def __init__(self, store: Store, token: str | None = None,
                 base_url: str = "https://api.github.com", timeout: float = 15.0) -> None:
        self.store = store
        try:
            credential = resolve_token(token)
        except AuthError as exc:
            raise GitHubError(str(exc)) from exc
        if credential is None:
            raise GitHubError("GitHub authentication is required; run 'muse-shroom auth login' or set GITHUB_TOKEN")
        self.token = credential.token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, params: dict[str, Any] | None = None,
                 *, accept: str = "application/vnd.github+json") -> ApiResult:
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        cache_key = hashlib.sha256(f"{accept}\0{url}".encode()).hexdigest()
        request = urllib.request.Request(url, headers={
            "Accept": accept,
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "Muse-shroom/0.1",
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                data = raw.decode("utf-8", errors="replace") if "raw" in accept else json.loads(raw)
                self.store.set_cache(cache_key, data)
                return ApiResult(data)
        except urllib.error.HTTPError as exc:
            cached = self.store.get_cache(cache_key)
            if cached:
                return ApiResult(cached[0], stale=True, cached_at=cached[1])
            if exc.code == 404:
                raise GitHubNotFoundError("GitHub resource was not found (404)") from exc
            if exc.code in {403, 429}:
                raise GitHubRateLimitError(f"GitHub API rate limited request ({exc.code})") from exc
            raise GitHubError(f"GitHub API request failed ({exc.code})") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            cached = self.store.get_cache(cache_key)
            if cached:
                return ApiResult(cached[0], stale=True, cached_at=cached[1])
            raise GitHubError(f"GitHub API network failure: {exc.reason if hasattr(exc, 'reason') else exc}") from exc

    def search_repositories(self, query: str, per_page: int = 10, sort: str = "stars") -> ApiResult:
        return self._request("/search/repositories", {
            "q": query, "sort": sort, "order": "desc", "per_page": min(per_page, 100)
        })

    def repository(self, full_name: str) -> ApiResult:
        return self._request(f"/repos/{full_name}")

    def readme(self, full_name: str) -> ApiResult:
        result = self._request(f"/repos/{full_name}/readme")
        payload = result.data
        if isinstance(payload, dict) and payload.get("encoding") == "base64":
            try:
                text = base64.b64decode(payload.get("content", ""), validate=False).decode("utf-8", errors="replace")
            except (ValueError, TypeError):
                text = ""
            result.data = {"text": text, "sha": payload.get("sha")}
        return result

    def latest_release(self, full_name: str) -> ApiResult:
        return self._request(f"/repos/{full_name}/releases/latest")

    def search_code(self, query: str, per_page: int = 10) -> ApiResult:
        return self._request("/search/code", {"q": query, "per_page": min(per_page, 100)})

    def forks(self, full_name: str, per_page: int = 10) -> ApiResult:
        return self._request(f"/repos/{full_name}/forks", {"sort": "stargazers", "per_page": per_page})

    def owner_repositories(self, owner: str, per_page: int = 20) -> ApiResult:
        return self._request(f"/users/{owner}/repos", {"sort": "updated", "per_page": per_page})
