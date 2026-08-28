from __future__ import annotations

from typing import Any

from muse_shroom.github import ApiResult, GitHubNotFoundError


def repo(full_name: str, stars: int, *, description: str = "", topics: list[str] | None = None,
         pushed_at: str = "2026-08-01T00:00:00Z", archived: bool = False,
         language: str | None = None) -> dict[str, Any]:
    return {
        "full_name": full_name, "html_url": f"https://github.com/{full_name}",
        "description": description, "stargazers_count": stars, "forks_count": stars // 10,
        "topics": topics or [], "pushed_at": pushed_at, "archived": archived,
        "language": language, "license": {"spdx_id": "MIT"},
    }


class FrozenGitHub:
    def __init__(self, searches: list[tuple[str, list[dict[str, Any]]]], readmes: dict[str, str] | None = None,
                 repos: dict[str, dict[str, Any]] | None = None) -> None:
        self.searches = searches
        self.readmes = {k.lower(): v for k, v in (readmes or {}).items()}
        self.repos = {k.lower(): v for k, v in (repos or {}).items()}
        self.request_counts = {"core": 0, "search": 0, "code_search": 0}
        self.rate_limits = {}

    def search_repositories(self, query: str, per_page: int = 10, sort: str = "stars") -> ApiResult:
        self.request_counts["search"] += 1
        for needle, items in self.searches:
            if needle.lower() in query.lower():
                return ApiResult({"items": items[:per_page]})
        return ApiResult({"items": []})

    def readme(self, full_name: str) -> ApiResult:
        self.request_counts["core"] += 1
        if full_name.lower() not in self.readmes:
            raise GitHubNotFoundError("README missing")
        return ApiResult(self.readmes[full_name.lower()])

    def latest_release(self, full_name: str) -> ApiResult:
        self.request_counts["core"] += 1
        raise GitHubNotFoundError("release missing")

    def search_code(self, query: str, per_page: int = 10) -> ApiResult:
        self.request_counts["code_search"] += 1
        return ApiResult({"items": []})

    def repository(self, full_name: str) -> ApiResult:
        self.request_counts["core"] += 1
        if full_name.lower() not in self.repos:
            raise GitHubNotFoundError("repository missing")
        return ApiResult(self.repos[full_name.lower()])

    def forks(self, full_name: str, per_page: int = 10) -> ApiResult:
        self.request_counts["core"] += 1
        return ApiResult([])

    def owner_repositories(self, owner: str, per_page: int = 20) -> ApiResult:
        self.request_counts["core"] += 1
        return ApiResult([])
