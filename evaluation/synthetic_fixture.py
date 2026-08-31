from __future__ import annotations

from typing import Any


class SyntheticFixtureGitHub:
    """Public-data-shaped deterministic source used only to build the CI cassette."""

    def __init__(self, github_module: Any) -> None:
        self.api_result = github_module.ApiResult
        self.not_found = github_module.GitHubNotFoundError
        self.request_counts = {"core": 0, "search": 0, "code_search": 0}
        self.rate_limits: dict[str, Any] = {}
        self.repos = [
            {
                "full_name": "muse-shroom-fixtures/adaptive-focus",
                "html_url": "https://github.com/muse-shroom-fixtures/adaptive-focus",
                "description": "Focus timer with adaptive pacing for sustainable sessions",
                "stargazers_count": 120, "forks_count": 8,
                "topics": ["focus-timer", "adaptive-pacing"],
                "pushed_at": "2026-01-01T00:00:00Z", "archived": False,
                "language": "Python", "license": {"spdx_id": "MIT"},
            },
            {
                "full_name": "muse-shroom-fixtures/pacing-lab",
                "html_url": "https://github.com/muse-shroom-fixtures/pacing-lab",
                "description": "Adaptive pacing experiments for focus sessions",
                "stargazers_count": 18, "forks_count": 2,
                "topics": ["adaptive-pacing"],
                "pushed_at": "2026-01-02T00:00:00Z", "archived": False,
                "language": "TypeScript", "license": {"spdx_id": "MIT"},
            },
        ]

    def search_repositories(self, query: str, per_page: int = 10, sort: str = "stars") -> Any:
        self.request_counts["search"] += 1
        return self.api_result({"items": self.repos[:per_page]})

    def repository(self, full_name: str) -> Any:
        self.request_counts["core"] += 1
        for repo in self.repos:
            if repo["full_name"].casefold() == full_name.casefold():
                return self.api_result(repo)
        raise self.not_found("synthetic repository missing")

    def readme(self, full_name: str) -> Any:
        self.request_counts["core"] += 1
        if not any(repo["full_name"].casefold() == full_name.casefold() for repo in self.repos):
            raise self.not_found("synthetic README missing")
        return self.api_result(
            "# Adaptive Focus\n\n## Overview\nFocus sessions.\n\n"
            "## Features\nAdaptive pacing changes session length using observed fatigue.\n"
        )

    def latest_release(self, full_name: str) -> Any:
        self.request_counts["core"] += 1
        raise self.not_found("synthetic release missing")

    def search_code(self, query: str, per_page: int = 10) -> Any:
        self.request_counts["code_search"] += 1
        return self.api_result({"items": []})

    def forks(self, full_name: str, per_page: int = 10) -> Any:
        self.request_counts["core"] += 1
        return self.api_result([])

    def owner_repositories(self, owner: str, per_page: int = 20) -> Any:
        self.request_counts["core"] += 1
        return self.api_result([])
