from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable

from .analyze import github_links, make_evidence, safe_readme
from .github import (
    ApiResult, GitHubAuthenticationError, GitHubClient, GitHubError,
    GitHubNotFoundError,
)
from .models import Refinement, SearchRequest, repo_key
from .queries import build_queries, code_filename_query, refinement_queries, reverse_reference_query
from .selection import balanced_select, candidate_allowed
from .storage import Store


PUBLIC_CANDIDATE_FIELDS = {
    "full_name", "html_url", "description", "homepage", "topics", "language",
    "stargazers_count", "archived", "pushed_at", "license", "latest_release",
    "readme_truncated",
    "discovery_paths", "matched_kinds", "evidence", "selection_lanes",
    "selection_score_components", "selected_for_assessment",
}

ENRICH_QUOTAS = {"core": 10, "gems": 8, "adjacent": 6, "popular": 6}
ASSESSMENT_QUOTAS = {"core": 7, "gems": 6, "adjacent": 5, "popular": 6}


def public_candidate(candidate: dict[str, Any], *, detailed: bool = False) -> dict[str, Any]:
    result = {key: value for key, value in candidate.items() if key in PUBLIC_CANDIDATE_FIELDS}
    license_value = result.get("license")
    if isinstance(license_value, dict):
        result["license"] = license_value.get("spdx_id")
    if not detailed:
        relationships = [path for path in candidate.get("discovery_paths", []) if path.get("kind") == "relationship"]
        queries = [path for path in candidate.get("discovery_paths", []) if path.get("kind") == "query"]
        queries.sort(key=lambda path: (int(path.get("position", 10)), str(path.get("query_kind", ""))))
        compact_paths = relationships[:4] + [{
            "kind": "query", "query_kind": path.get("query_kind"), "position": path.get("position")
        } for path in queries[:4]]
        result["discovery_paths"] = compact_paths
        compact_evidence = []
        for item in candidate.get("evidence", []):
            if item.get("kind") == "github_metadata":
                compact_evidence.append({
                    "id": item.get("id"), "kind": "github_metadata",
                    "source": item.get("source"),
                    "facts": {"candidate_fields": [
                        "description", "stars", "archived", "license", "topics", "pushed_at"
                    ]},
                })
            else:
                compact_evidence.append(item)
        result["evidence"] = compact_evidence
    return result


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
            for position, repo in enumerate(items, 1):
                if repo.get("private") or repo.get("visibility") not in {None, "public"}:
                    continue
                key = repo_key(repo)
                if not key:
                    continue
                candidate = candidates.setdefault(key, dict(repo))
                path = {
                    "kind": "query", "query": query_spec["query"],
                    "query_kind": query_spec["kind"], "position": position,
                }
                paths = candidate.setdefault("discovery_paths", [])
                existing_path = next((item for item in paths if (
                    item.get("kind") == "query"
                    and item.get("query") == path["query"]
                    and item.get("query_kind") == path["query_kind"]
                )), None)
                if existing_path is None:
                    paths.append(path)
                else:
                    existing_path["position"] = min(position, int(existing_path.get("position", position)))
                kinds = candidate.setdefault("matched_kinds", [])
                if query_spec["kind"] not in kinds:
                    kinds.append(query_spec["kind"])
                if len(candidates) >= self.candidate_limit:
                    return stale, cached_at
        return stale, cached_at

    def _enrich(self, candidates: dict[str, dict[str, Any]], request: SearchRequest,
                limit: int | None = None) -> tuple[bool, str | None, bool, int]:
        stale = False
        cached_at = None
        failed = False
        pending = [item for item in candidates.values() if "readme" not in item]
        selected, _ = balanced_select(pending, request, ENRICH_QUOTAS, enriched=False)
        selected = selected[:limit or self.enrich_limit]

        def fetch(candidate: dict[str, Any]) -> tuple[dict[str, Any], ApiResult | None, ApiResult | None]:
            readme_result = None
            release_result = None
            try:
                readme_result = self.github.readme(candidate["full_name"])
            except GitHubNotFoundError:
                pass
            except GitHubAuthenticationError:
                raise
            except GitHubError:
                readme_result = "error"
            try:
                release_result = self.github.latest_release(candidate["full_name"])
            except (GitHubNotFoundError, AttributeError):
                pass
            except GitHubAuthenticationError:
                raise
            except GitHubError:
                release_result = "error"
            return candidate, readme_result, release_result

        with ThreadPoolExecutor(max_workers=min(12, max(1, len(selected)))) as pool:
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
            concept_terms = [concept.term for concept in request.core_concepts + request.adjacent_concepts]
            for item in make_evidence(
                candidate, readme, truncated, concept_terms=concept_terms,
                artifact_types=request.artifact_types,
            ):
                existing[item["id"]] = item
            candidate["evidence"] = list(existing.values())
        return stale, cached_at, failed, len(selected)

    def _add_related(self, candidates: dict[str, dict[str, Any]], repo: dict[str, Any],
                     parent: str, relation: str, detail: str, request: SearchRequest) -> None:
        key = repo_key(repo)
        if repo.get("private") or repo.get("visibility") not in {None, "public"}:
            return
        if not key or (key == parent.lower() and relation != "key_file"):
            return
        if not candidate_allowed(repo, request, include_readme=False):
            return
        if key not in candidates and len(candidates) >= self.candidate_limit:
            return
        candidate = candidates.setdefault(key, dict(repo))
        path = {
            "kind": "relationship", "from": parent, "relation": relation, "detail": detail
        }
        paths = candidate.setdefault("discovery_paths", [])
        if path not in paths:
            paths.append(path)
        if relation == "key_file":
            kinds = candidate.setdefault("matched_kinds", [])
            if "key_file" not in kinds:
                kinds.append("key_file")
        evidence_id = f"relation:{parent.lower()}:{relation}:{key}"
        evidence = candidate.setdefault("evidence", [])
        if not any(item.get("id") == evidence_id for item in evidence):
            evidence.append({
                "id": evidence_id, "kind": "discovery_relation",
                "source": f"https://github.com/{parent}",
                "facts": {"from": parent, "to": candidate.get("full_name"), "relation": relation, "detail": detail},
            })

    def _expand_relations(self, search_id: str, candidates: dict[str, dict[str, Any]],
                          request: SearchRequest, seed_names: list[str] | None = None) -> tuple[bool, str | None, int, bool]:
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
                    calls += 1
                    result = self.github.repository(linked)
                    stale = stale or result.stale
                    cached_at = cached_at or result.cached_at
                    self._add_related(candidates, result.data, full_name, "readme_link", linked, request)
                except GitHubNotFoundError:
                    continue
                except GitHubAuthenticationError:
                    raise
                except GitHubError:
                    failed = True
            if calls < self.relation_budget:
                query = reverse_reference_query(full_name, request)
                try:
                    calls += 1
                    result = self.github.search_repositories(query, per_page=10)
                    stale = stale or result.stale
                    cached_at = cached_at or result.cached_at
                    items = self._items(result)
                    self.store.add_query(search_id, query, "reverse_readme", len(items))
                    for item in items:
                        self._add_related(candidates, item, full_name, "reverse_readme", query, request)
                except GitHubAuthenticationError:
                    raise
                except GitHubError:
                    failed = True
            if calls < self.relation_budget:
                try:
                    calls += 1
                    result = self.github.forks(full_name, per_page=5)
                    stale = stale or result.stale
                    cached_at = cached_at or result.cached_at
                    for item in self._items(result):
                        self._add_related(candidates, item, full_name, "fork", "GitHub forks endpoint", request)
                except GitHubAuthenticationError:
                    raise
                except GitHubError:
                    failed = True
            if calls < self.relation_budget:
                try:
                    owner = full_name.split("/", 1)[0]
                    calls += 1
                    result = self.github.owner_repositories(owner, per_page=10)
                    stale = stale or result.stale
                    cached_at = cached_at or result.cached_at
                    for item in self._items(result):
                        self._add_related(candidates, item, full_name, "same_owner", owner, request)
                except GitHubAuthenticationError:
                    raise
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
        enriched_count = 0
        try:
            s, cache_time = self._recall(search_id, build_queries(request), candidates)
            stale, cached_at = stale or s, cached_at or cache_time
            candidates = {
                name: item for name, item in candidates.items()
                if candidate_allowed(item, request, include_readme=False)
            }
            s, cache_time, enrich_failed, enriched_count = self._enrich(candidates, request)
            stale, cached_at = stale or s, cached_at or cache_time
            if enrich_failed:
                incomplete = "enrichment_partial_failure"
        except GitHubAuthenticationError:
            self.store.mark_search(search_id, stale=False, incomplete_phase="github_authentication_error")
            raise
        except GitHubError as exc:
            incomplete = f"github_error:{type(exc).__name__}"
            if not candidates:
                self.store.mark_search(search_id, stale=stale, incomplete_phase=incomplete)
                raise
        output = self._finish(
            search_id, candidates, request, stale, cached_at, incomplete,
            enriched_count=enriched_count, relation_calls=0, code_calls=0,
        )
        output["next_action"] = "expand" if mode == "deep" else "rank"
        return output

    def expand(self, search_id: str, refinement: dict[str, Any]) -> dict[str, Any]:
        session = self.store.load_search(search_id)
        parsed_refinement = Refinement.from_dict(refinement)
        request_data = dict(session["request"])
        request_data["exclusions"] = list(request_data.get("exclusions", [])) + parsed_refinement.exclude
        request = SearchRequest.from_dict(request_data)
        candidates = {repo_key(item): item for item in session["candidates"]}
        stale = bool(session["stale"])
        cached_at = None
        recall_failed = False
        try:
            recalled_stale, recalled_cache = self._recall(
                search_id, refinement_queries(parsed_refinement, request), candidates
            )
            stale, cached_at = stale or recalled_stale, recalled_cache
        except GitHubAuthenticationError:
            raise
        except GitHubError:
            recall_failed = True
        candidates = {
            name: item for name, item in candidates.items()
            if candidate_allowed(item, request, include_readme="readme" in item)
        }
        code_calls = 0
        code_failed = False
        for filename in parsed_refinement.filenames:
            query = code_filename_query(filename, parsed_refinement.concepts[0] if parsed_refinement.concepts else None)
            code_calls += 1
            try:
                result = self.github.search_code(query, per_page=10)
                stale = stale or result.stale
                cached_at = cached_at or result.cached_at
                items = self._items(result)
                self.store.add_query(search_id, query, "key_file", len(items))
                for item in items:
                    repository = item.get("repository", item)
                    self._add_related(
                        candidates, repository, repository.get("full_name", "unknown"),
                        "key_file", filename, request,
                    )
            except GitHubAuthenticationError:
                raise
            except GitHubError:
                code_failed = True
        s, cache_time, first_enrich_failed, first_enriched = self._enrich(candidates, request)
        stale, cached_at = stale or s, cached_at or cache_time
        s, cache_time, calls, relation_failed = self._expand_relations(
            search_id, candidates, request, parsed_refinement.seeds or None
        )
        stale, cached_at = stale or s, cached_at or cache_time
        s, cache_time, second_enrich_failed, second_enriched = self._enrich(candidates, request)
        stale, cached_at = stale or s, cached_at or cache_time
        if calls >= self.relation_budget:
            incomplete = "relationship_budget_reached"
        elif recall_failed or code_failed:
            incomplete = "refinement_partial_failure"
        elif first_enrich_failed or second_enrich_failed:
            incomplete = "enrichment_partial_failure"
        elif relation_failed:
            incomplete = "relationship_partial_failure"
        else:
            incomplete = None
        output = self._finish(
            search_id, candidates, request, stale, cached_at, incomplete,
            enriched_count=first_enriched + second_enriched,
            relation_calls=calls, code_calls=code_calls,
        )
        output["next_action"] = "rank"
        return output

    def _finish(self, search_id: str, candidates: dict[str, dict[str, Any]], request: SearchRequest,
                stale: bool, cached_at: str | None, incomplete: str | None, *,
                enriched_count: int, relation_calls: int, code_calls: int) -> dict[str, Any]:
        candidates = {
            name: item for name, item in candidates.items()
            if candidate_allowed(item, request, include_readme="readme" in item)
        }
        concept_terms = [concept.term for concept in request.core_concepts + request.adjacent_concepts]
        for candidate in candidates.values():
            candidate.setdefault("evidence", make_evidence(
                candidate, "", False, concept_terms=concept_terms,
                artifact_types=request.artifact_types,
            ))
            candidate["matched_kinds"] = sorted(set(candidate.get("matched_kinds", [])))
            candidate["selected_for_assessment"] = False
        assessable = [item for item in candidates.values() if "readme" in item]
        selected, lane_counts = balanced_select(
            assessable, request, ASSESSMENT_QUOTAS, enriched=True, max_per_owner=3
        )
        for candidate in selected:
            candidate["selected_for_assessment"] = True
        self.store.retain_search_candidates(search_id, list(candidates))
        for candidate in candidates.values():
            self.store.save_candidate(search_id, candidate)
        self.store.mark_search(search_id, stale=stale, incomplete_phase=incomplete)
        coverage = {
            "queries_executed": self.store.query_count(search_id),
            "enriched": sum("readme" in item for item in candidates.values()),
            "enriched_this_phase": enriched_count,
            "omitted": max(0, len(candidates) - len(selected)),
            "relation_calls": relation_calls, "code_search_calls": code_calls,
            "lanes": lane_counts,
            "api_calls": dict(getattr(self.github, "request_counts", {})),
            "rate_limits": dict(getattr(self.github, "rate_limits", {})),
        }
        return self._search_output(
            search_id, candidates.values(), selected, stale, cached_at, incomplete, coverage
        )

    @staticmethod
    def _search_output(search_id: str, all_candidates: Iterable[dict[str, Any]],
                       selected: Iterable[dict[str, Any]], stale: bool,
                       cached_at: str | None, incomplete: str | None,
                       coverage: dict[str, Any]) -> dict[str, Any]:
        # Full GitHub responses and README text stay in SQLite for later expansion
        # and ranking, while the wire response exposes only the stable fields the
        # host agent needs for assessment.
        all_candidates = list(all_candidates)
        items = []
        for candidate in selected:
            items.append(public_candidate(candidate))
        items.sort(key=lambda item: (
            -max(item.get("selection_score_components", {}).get("recall", 0),
                 item.get("selection_score_components", {}).get("adjacent_concept", 0)),
            item.get("full_name", "").lower(),
        ))
        return {
            "schema_version": 2, "search_id": search_id, "stale": stale,
            "cache_time": cached_at, "incomplete_phase": incomplete,
            "candidate_count": len(all_candidates), "assessment_candidate_count": len(items),
            "candidates": items, "coverage": coverage,
        }
