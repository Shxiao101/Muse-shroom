"""Shared Core invocations for CLI-equivalent interfaces such as MCP."""

from __future__ import annotations

import sqlite3
from typing import Any

from . import __version__
from .auth import AuthError, resolve_token
from .github import GitHubClient
from .models import SearchRequest
from .ranking import rank_search
from .search import SearchEngine, public_candidate
from .storage import Store


class MuseCore:
    """Open a Store per call so session state lives in SQLite, not process memory."""

    def __init__(self, *, data_dir: str | None = None, github: Any | None = None) -> None:
        self.data_dir = data_dir
        self.github = github

    def _store(self) -> Store:
        return Store(self.data_dir)

    def _github(self, store: Store) -> Any:
        return self.github if self.github is not None else GitHubClient(store)

    def status(self) -> dict[str, Any]:
        store = self._store()
        try:
            try:
                configured = resolve_token() is not None
            except AuthError:
                configured = False
            try:
                store.db.execute("SELECT 1").fetchone()
                database_available = True
            except sqlite3.Error:
                database_available = False
            return {
                "version": __version__,
                "credential_configured": configured,
                "database_available": database_available,
                "data_dir": str(store.data_dir),
            }
        finally:
            store.close()

    def search(self, request: dict[str, Any], mode: str = "quick", *, refresh: bool = False) -> dict[str, Any]:
        parsed = SearchRequest.from_dict(request)
        store = self._store()
        try:
            return SearchEngine(store, self._github(store)).search(parsed, mode, refresh=refresh)
        finally:
            store.close()

    def observe(self, search_id: str) -> dict[str, Any]:
        store = self._store()
        try:
            return SearchEngine(store, None).observe(search_id)
        finally:
            store.close()

    def iterate(self, search_id: str, hypothesis: dict[str, Any]) -> dict[str, Any]:
        store = self._store()
        try:
            return SearchEngine(store, self._github(store)).iterate(search_id, hypothesis)
        finally:
            store.close()

    def rank(self, search_id: str, assessments: Any) -> dict[str, Any]:
        store = self._store()
        try:
            return rank_search(store, search_id, assessments)
        finally:
            store.close()

    def inspect(self, repo: str, search_id: str | None = None) -> dict[str, Any]:
        store = self._store()
        try:
            candidate = store.get_candidate(repo, search_id)
            if candidate is None:
                raise KeyError(f"repository not found in local snapshots: {repo}")
            history = store.star_history(repo)
            ranking_item = None
            if search_id:
                ranking = store.get_ranking(search_id)
                if ranking:
                    ranking_item = next(
                        (
                            item for bucket in ranking.get("buckets", {}).values()
                            for item in bucket
                            if item.get("repo", "").lower() == repo.lower()
                        ),
                        None,
                    )
            return {
                "schema_version": 2,
                "repository": public_candidate(candidate, detailed=True),
                "star_history": history,
                "growth_available": len(history) >= 2,
                "ranking": ranking_item,
            }
        finally:
            store.close()
