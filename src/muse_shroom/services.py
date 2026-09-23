"""Shared Core invocations for CLI-equivalent interfaces such as MCP."""

from __future__ import annotations

import sqlite3
import threading
from collections import defaultdict
from contextlib import contextmanager
from typing import Any

from . import __version__
from .agent_view import ShownCandidates, rank_view, session_view
from .auth import AuthError, resolve_token
from .github import GitHubClient
from .models import SearchHypothesis, SearchRequest
from .ranking import find_candidate, rank_search
from .search import SearchEngine, public_candidate
from .storage import Store


_SESSION_LOCKS: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
_SESSION_LOCKS_GUARD = threading.Lock()


@contextmanager
def session_lock(search_id: str):
    """Hold one session while a call reads its state, changes it and writes it back.

    An MCP host runs a synchronous tool in a thread, and each call opens its own
    Store, so two supplies of the same session read the same state and the second
    write erased the first: both repositories were saved, but the session listed
    one, and the count it enforces its own limit with was wrong from then on.
    """
    with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS[str(search_id)]
    with lock:
        yield


class MuseCore:
    """Open a Store per call so session state lives in SQLite, not process memory.

    Outputs are the Agent view (agent_view.py). The only process memory is which
    candidates this server already returned, so iterate can skip unchanged ones,
    and one lock per session so calls that change it do not overwrite each other.
    """

    def __init__(self, *, data_dir: str | None = None, github: Any | None = None) -> None:
        self.data_dir = data_dir
        self.github = github
        self.shown = ShownCandidates()

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
        parsed = SearchRequest.from_dict(request, strict=True)
        store = self._store()
        try:
            result = SearchEngine(store, self._github(store)).search(parsed, mode, refresh=refresh)
        finally:
            store.close()
        if parsed.legacy_schema:
            result["legacy_schema"] = True
            result["contract_warning"] = (
                "This request used deprecated v0.3 fields core_concepts/"
                "adjacent_concepts. Prefer v0.4 fields problem_concepts, "
                "mechanisms, and exploration_directions."
            )
        view = session_view(result)
        self.shown.reset(str(view.get("search_id") or ""), view.get("candidates"))
        return view

    def observe(self, search_id: str) -> dict[str, Any]:
        store = self._store()
        try:
            return session_view(SearchEngine(store, None).observe(search_id))
        finally:
            store.close()

    def iterate(self, search_id: str, hypothesis: dict[str, Any]) -> dict[str, Any]:
        parsed = SearchHypothesis.from_dict(hypothesis, strict=True)
        with session_lock(search_id):
            store = self._store()
            try:
                result = SearchEngine(store, self._github(store)).iterate(
                    search_id, parsed.to_dict(),
                )
            finally:
                store.close()
        return self.shown.omit_unchanged(session_view(result))

    def supply(self, search_id: str, repositories: Any, reason: Any) -> dict[str, Any]:
        with session_lock(search_id):
            store = self._store()
            try:
                result = SearchEngine(store, self._github(store)).supply(
                    search_id, repositories, reason,
                )
            finally:
                store.close()
        self.shown.remember(search_id, result.get("supplied"))
        return result

    def rank(self, search_id: str, selection: Any) -> dict[str, Any]:
        with session_lock(search_id):
            store = self._store()
            try:
                result = rank_search(store, search_id, selection, strict=True)
            finally:
                store.close()
        from .explorer.launcher import ensure_explorer
        explorer = ensure_explorer(search_id, data_dir=self.data_dir)
        result["explorer_url"] = explorer["url"]
        result["explorer_running"] = explorer["running"]
        return rank_view(result)

    def inspect(self, repo: str, search_id: str | None = None) -> dict[str, Any]:
        store = self._store()
        try:
            candidate = find_candidate(store, repo, search_id)
            if candidate is None:
                raise KeyError(f"repository not found in local snapshots: {repo}")
            history = store.star_history(repo)
            ranking_item = None
            if search_id:
                ranking = store.get_ranking(search_id)
                if ranking:
                    ranked_items = list(ranking.get("items") or [])
                    ranking_item = next((
                        item for item in ranked_items
                        if item.get("repo", "").lower() == repo.lower()
                    ), None)
            return {
                "schema_version": 2,
                "repository": public_candidate(candidate, detailed=True),
                "star_history": history,
                "growth_available": len(history) >= 2,
                "ranking": ranking_item,
            }
        finally:
            store.close()
