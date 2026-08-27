from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable

from .analyze import github_links, make_evidence, safe_readme
from .github import ApiResult, GitHubClient, GitHubError, GitHubNotFoundError
from .models import SearchRequest, repo_key
from .queries import build_queries, refinement_queries, reverse_reference_query
from .storage import Store


class SearchEngine:
    def __init__(self, store: Store, github: GitHubClient, *, candidate_limit: int = 100,
                 enrich_limit: int = 30, relation_budget: int = 40) -> None:
        self.store = store
        self.github = github
        self.candidate_limit = candidate_limit
        self.enrich_limit = enrich_limit
        self.relation_budget = relation_budget

    @staticmethod
    def _items(result: ApiResult) -> list[dict[str, Any]]:
        data = result.data
        return list(data.get("items", [])) if isinstance(data, dict) else list(data)

    @staticmethod
    def _apply_exclusions(candidates: dict[str, dict[str, Any]], exclusions: Iterable[str]) -> dict[str, dict[str, Any]]:
        terms = [str(term).strip().lower() for term in exclusions if str(term).strip()]
        if not terms:
            return candidates
        kept = {}
        for name, candidate in candidates.items():
            haystack = " ".join([
                name, str(candidate.get("description", "")), " ".join(candidate.get("topics", [])),
                str(candidate.get("readme", "")),
            ]).lower()
            if not any(term in haystack for term in terms):
                kept[name] = candidate
        return kept

    def _recall(self, search_id: str, queries: Iterable[dict[str, str]],
                candidates: dict[str, dict[str, Any]]) -> tuple[bool, str | None]:
        stale = False
        cached_at = None
        query_list = list(queries)
        with ThreadPoolExecutor(max_workers=min(6, max(1, len(query_list)))) as pool:
            futures = {
                pool.submit(self.github.search_repositories, spec["query"], 10, spec.get("sort", "stars")): index
                for index, spec in enumerate(query_list)
            }
            results: list[tuple[dict[str, str], ApiResult] | None] = [None] * len(query_list)
            for future in as_completed(futures):
                index = futures[future]
                results[index] = (query_list[index], future.result())
        for completed in results:
            if completed is None:
                continue
            query_spec, result = completed
            stale = stale or result.stale
            cached_at = cached_at or result.cached_at
            items = self._items(result)
            self.store.add_query(search_id, query_spec["query"], query_spec["kind"], len(items))
            for repo in items:
                if repo.get("private") or repo.get("visibility") not in {None, "public"}:
                    continue
                key = repo_key(repo)
                if not key:
                    continue
                candidate = candidates.setdefault(key, dict(repo))
                candidate.setdefault("discovery_paths", []).append({
                    "kind": "query", "query": query_spec["query"], "query_kind": query_spec["kind"]
                })
                candidate.setdefault("matched_kinds", []).append(query_spec["kind"])
                if len(candidates) >= self.candidate_limit:
                    return stale, cached_at
        return stale, cached_at

    def _enrich(self, candidates: dict[str, dict[str, Any]], limit: int | None = None) -> tuple[bool, str | None, bool]:
        stale = False
        cached_at = None
        failed = False
        ordered = sorted(candidates.values(), key=lambda item: item.get("stargazers_count", 0), reverse=True)
        selected = ordered[:limit or self.enrich_limit]

        def fetch(candidate: dict[str, Any]) -> tuple[dict[str, Any], ApiResult | None, ApiResult | None]:
            readme_result = None
            release_result = None
            try:
                readme_result = self.github.readme(candidate["full_name"])
            except GitHubNotFoundError:
                pass
            except GitHubError:
                readme_result = "error"
            try:
                release_result = self.github.latest_release(candidate["full_name"])
            except (GitHubNotFoundError, AttributeError):
                pass
            except GitHubError:
                release_result = "error"
            return candidate, readme_result, release_result

        with ThreadPoolExecutor(max_workers=min(8, max(1, len(selected)))) as pool:
            fetched = list(pool.map(fetch, selected))
        for candidate, result, release_result in fetched:
            if result == "error" or release_result == "error":
                failed = True
            if result == "error":
                result = None
            if release_result == "error":
                release_result = None
            full_name = candidate["full_name"]
            readme = ""
            truncated = False
            if result is not None:
                stale = stale or result.stale
                cached_at = cached_at or result.cached_at
                payload = result.data
                if isinstance(payload, dict):
                    candidate["readme_sha"] = payload.get("sha")
                    raw_readme = str(payload.get("text", ""))
                else:
                    raw_readme = str(payload)
                readme, truncated = safe_readme(raw_readme)
            if release_result is not None:
                stale = stale or release_result.stale
                cached_at = cached_at or release_result.cached_at
                if isinstance(release_result.data, dict):
                    candidate["latest_release"] = {
                        "tag_name": release_result.data.get("tag_name"),
                        "published_at": release_result.data.get("published_at"),
                        "html_url": release_result.data.get("html_url"),
                    }
            candidate["readme"] = readme
            candidate["readme_truncated"] = truncated
            candidate["readme_links"] = github_links(readme, full_name)
            existing = {item.get("id"): item for item in candidate.get("evidence", [])}
            for item in make_evidence(candidate, readme, truncated):
                existing[item["id"]] = item
            candidate["evidence"] = list(existing.values())
        return stale, cached_at, failed

    def _add_related(self, candidates: dict[str, dict[str, Any]], repo: dict[str, Any],
                     parent: str, relation: str, detail: str) -> None:
        key = repo_key(repo)
        if repo.get("private") or repo.get("visibility") not in {None, "public"}:
            return
        if not key or (key == parent.lower() and relation != "key_file"):
            return
        if key not in candidates and len(candidates) >= self.candidate_limit:
            return
        candidate = candidates.setdefault(key, dict(repo))
        candidate.setdefault("discovery_paths", []).append({
            "kind": "relationship", "from": parent, "relation": relation, "detail": detail
        })
        evidence_id = f"relation:{parent.lower()}:{relation}:{key}"
        evidence = candidate.setdefault("evidence", [])
        if not any(item.get("id") == evidence_id for item in evidence):
            evidence.append({
                "id": evidence_id, "kind": "discovery_relation",
                "source": f"https://github.com/{parent}",
                "facts": {"from": parent, "to": candidate.get("full_name"), "relation": relation, "detail": detail},
            })

    def _expand_relations(self, search_id: str, candidates: dict[str, dict[str, Any]],
                          seed_names: list[str] | None = None) -> tuple[bool, str | None, int, bool]:
        stale = False
        cached_at = None
        calls = 0
        failed = False
        if seed_names:
            seeds = [candidates[name.lower()] for name in seed_names if name.lower() in candidates][:8]
        else:
            seeds = sorted(candidates.values(), key=lambda item: item.get("stargazers_count", 0), reverse=True)[:5]
        for seed in seeds:
            if calls >= self.relation_budget:
                break
            full_name = seed["full_name"]
            for linked in seed.get("readme_links", [])[:8]:
                if calls >= self.relation_budget:
                    break
                try:
                    result = self.github.repository(linked)
                    calls += 1
                    stale = stale or result.stale
                    cached_at = cached_at or result.cached_at
                    self._add_related(candidates, result.data, full_name, "readme_link", linked)
                except GitHubNotFoundError:
                    continue
                except GitHubError:
                    failed = True
            if calls < self.relation_budget:
                query = reverse_reference_query(full_name)
                try:
                    result = self.github.search_repositories(query, per_page=10)
                    calls += 1
                    stale = stale or result.stale
                    cached_at = cached_at or result.cached_at
                    items = self._items(result)
                    self.store.add_query(search_id, query, "reverse_readme", len(items))
                    for item in items:
                        self._add_related(candidates, item, full_name, "reverse_readme", query)
                except GitHubError:
                    failed = True
            if calls < self.relation_budget:
                try:
                    result = self.github.forks(full_name, per_page=5)
                    calls += 1
                    stale = stale or result.stale
                    cached_at = cached_at or result.cached_at
                    for item in self._items(result):
                        self._add_related(candidates, item, full_name, "fork", "GitHub forks endpoint")
                except GitHubError:
                    failed = True
            if calls < self.relation_budget:
                try:
                    owner = full_name.split("/", 1)[0]
                    result = self.github.owner_repositories(owner, per_page=10)
                    calls += 1
                    stale = stale or result.stale
                    cached_at = cached_at or result.cached_at
                    for item in self._items(result):
                        self._add_related(candidates, item, full_name, "same_owner", owner)
                except GitHubError:
                    failed = True
        return stale, cached_at, calls, failed

    def search(self, request: SearchRequest, mode: str) -> dict[str, Any]:
        if mode not in {"quick", "deep"}:
            raise ValueError("mode must be quick or deep")
        search_id = uuid.uuid4().hex
        self.store.create_search(search_id, request.to_dict(), mode)
        candidates: dict[str, dict[str, Any]] = {}
        stale = False
        cached_at = None
        incomplete = None
        try:
            s, cache_time = self._recall(search_id, build_queries(request), candidates)
            stale, cached_at = stale or s, cached_at or cache_time
            s, cache_time, enrich_failed = self._enrich(candidates)
            stale, cached_at = stale or s, cached_at or cache_time
            if enrich_failed:
                incomplete = "enrichment_partial_failure"
        except GitHubError as exc:
            incomplete = f"github_error:{type(exc).__name__}"
            if not candidates:
                self.store.mark_search(search_id, stale=stale, incomplete_phase=incomplete)
                raise
        if not request.constraints.get("include_archived", False):
            candidates = {name: item for name, item in candidates.items() if not item.get("archived", False)}
        candidates = self._apply_exclusions(candidates, request.exclusions)
        for candidate in candidates.values():
            candidate.setdefault("evidence", make_evidence(candidate, "", False))
            candidate["matched_kinds"] = sorted(set(candidate.get("matched_kinds", [])))
            self.store.save_candidate(search_id, candidate)
        self.store.mark_search(search_id, stale=stale, incomplete_phase=incomplete)
        output = self._search_output(search_id, candidates.values(), stale, cached_at, incomplete)
        output["next_action"] = "expand" if mode == "deep" else "rank"
        return output

    def expand(self, search_id: str, refinement: dict[str, Any]) -> dict[str, Any]:
        session = self.store.load_search(search_id)
        candidates = {repo_key(item): item for item in session["candidates"]}
        stale = bool(session["stale"])
        cached_at = None
        recalled_stale, recalled_cache = self._recall(search_id, refinement_queries(refinement), candidates)
        stale, cached_at = stale or recalled_stale, recalled_cache
        for filename in [str(v).strip() for v in refinement.get("filenames", []) if str(v).strip()][:5]:
            concepts = [str(v).strip() for v in refinement.get("concepts", []) if str(v).strip()]
            query = f"is:public filename:{filename}" + (f" {concepts[0]}" if concepts else "")
            result = self.github.search_code(query, per_page=10)
            stale = stale or result.stale
            cached_at = cached_at or result.cached_at
            items = self._items(result)
            self.store.add_query(search_id, query, "key_file", len(items))
            for item in items:
                repository = item.get("repository", item)
                self._add_related(candidates, repository, repository.get("full_name", "unknown"), "key_file", filename)
        s, cache_time, first_enrich_failed = self._enrich(candidates)
        stale, cached_at = stale or s, cached_at or cache_time
        seeds = [str(v).strip() for v in refinement.get("seeds", []) if str(v).strip()]
        s, cache_time, calls, relation_failed = self._expand_relations(search_id, candidates, seeds or None)
        stale, cached_at = stale or s, cached_at or cache_time
        s, cache_time, second_enrich_failed = self._enrich(candidates)
        stale, cached_at = stale or s, cached_at or cache_time
        if calls >= self.relation_budget:
            incomplete = "relationship_budget_reached"
        elif first_enrich_failed or second_enrich_failed:
            incomplete = "enrichment_partial_failure"
        elif relation_failed:
            incomplete = "relationship_partial_failure"
        else:
            incomplete = None
        if not session["request"].get("constraints", {}).get("include_archived", False):
            candidates = {name: item for name, item in candidates.items() if not item.get("archived", False)}
        candidates = self._apply_exclusions(
            candidates, list(session["request"].get("exclusions", [])) + list(refinement.get("exclude", []))
        )
        for candidate in candidates.values():
            candidate.setdefault("evidence", make_evidence(candidate, "", False))
            self.store.save_candidate(search_id, candidate)
        self.store.mark_search(search_id, stale=stale, incomplete_phase=incomplete)
        output = self._search_output(search_id, candidates.values(), stale, cached_at, incomplete)
        output["next_action"] = "rank"
        return output

    @staticmethod
    def _search_output(search_id: str, candidates: Iterable[dict[str, Any]], stale: bool,
                       cached_at: str | None, incomplete: str | None) -> dict[str, Any]:
        items = sorted(candidates, key=lambda item: item.get("stargazers_count", 0), reverse=True)
        return {
            "schema_version": 1, "search_id": search_id, "stale": stale,
            "cache_time": cached_at, "incomplete_phase": incomplete,
            "candidate_count": len(items), "candidates": items,
        }
