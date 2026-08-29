from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable

from .analyze import _is_thin_overview, github_links, make_evidence, safe_readme
from .github import (
    ApiResult, GitHubAuthenticationError, GitHubClient, GitHubError,
    GitHubNotFoundError,
)
from .models import Refinement, SearchRequest, repo_key
from .queries import build_queries, code_filename_query, indexed_groups, refinement_queries, reverse_reference_query
from .selection import (
    SHORTLIST_LIMIT, candidate_allowed, covered_core_ids, probe_select,
    shortlist_select, uncovered_core_terms,
)
from .storage import Store


PUBLIC_CANDIDATE_FIELDS = {
    "full_name", "html_url", "description", "homepage", "topics", "language",
    "stargazers_count", "archived", "pushed_at", "license", "latest_release",
    "readme_truncated",
    "discovery_paths", "matched_kinds", "evidence", "selection_lanes",
    "selection_score_components",
    "concept_matches", "selection_reason",
}

CONCEPT_MATCH_CHARS = 240
HOWTO_EXCERPT_CHARS = 220
SEARCH_OUTPUT_MAX_BYTES = 30_000


def _compact_excerpt(item: dict[str, Any], limit: int) -> dict[str, Any]:
    facts = item.get("facts") or {}
    text = " ".join(str(facts.get("text") or "").split())[:limit]
    compact = {
        "id": item.get("id"), "kind": "readme_excerpt",
        "facts": {
            **{key: facts[key] for key in (
                "snippet_type", "line_start", "line_end", "sha", "parent_evidence_id",
            ) if facts.get(key) is not None},
            "text": text,
            "untrusted_source": True,
        },
    }
    if item.get("source"):
        compact["source"] = item["source"]
    return compact


def _unique(items: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result = []
    for item in items:
        identity = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    return result


def _pack_evidence(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = None
    by_type: dict[str, dict[str, Any]] = {}
    for item in candidate.get("evidence", []):
        kind = item.get("kind")
        if kind == "github_metadata" and metadata is None:
            metadata = {
                "id": item.get("id"), "kind": "github_metadata",
                "facts": {"candidate_fields": [
                    "description", "stars", "archived", "license", "topics", "pushed_at"
                ]},
            }
        elif kind == "readme_excerpt":
            snippet_type = str((item.get("facts") or {}).get("snippet_type") or "")
            if snippet_type and snippet_type not in by_type:
                by_type[snippet_type] = item
    chosen: list[dict[str, Any]] = []
    if metadata:
        chosen.append(metadata)
    concept = by_type.get("concept_match")
    overview = by_type.get("overview")
    if overview and _is_thin_overview(str((overview.get("facts") or {}).get("text") or "")):
        overview = None
    primary = concept or overview
    if primary:
        limit = CONCEPT_MATCH_CHARS if (primary.get("facts") or {}).get("snippet_type") == "concept_match" else HOWTO_EXCERPT_CHARS
        chosen.append(_compact_excerpt(primary, limit))
    howto = by_type.get("usage") or by_type.get("installation")
    if howto and howto is not primary:
        chosen.append(_compact_excerpt(howto, HOWTO_EXCERPT_CHARS))
    return chosen[:3]


def _wire_size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _sync_output_bytes(output: dict[str, Any]) -> int:
    coverage = output["coverage"]
    coverage.setdefault("output_bytes", 0)
    for _ in range(4):
        size = _wire_size(output)
        if coverage["output_bytes"] == size:
            return size
        coverage["output_bytes"] = size
    return _wire_size(output)


def _compact_search_output(output: dict[str, Any], limit: int = SEARCH_OUTPUT_MAX_BYTES) -> None:
    if _sync_output_bytes(output) <= limit:
        return
    output["coverage"]["output_compacted"] = True
    candidates = list(reversed(output.get("candidates") or []))

    def apply(mutations: Iterable[tuple[dict[str, Any], str, Any]]) -> bool:
        for item, key, value in mutations:
            if value is None:
                item.pop(key, None)
            else:
                item[key] = value
            if _sync_output_bytes(output) <= limit:
                return True
        return False

    stages: list[list[tuple[dict[str, Any], str, Any]]] = []
    stages.append([
        (item, "topics", item["topics"][:3])
        for item in candidates if isinstance(item.get("topics"), list) and len(item["topics"]) > 3
    ])
    stages.append([
        (item, "selection_score_components", {
            key: item["selection_score_components"][key]
            for key in ("recall", "core_concept", "adjacent_concept")
            if key in item["selection_score_components"]
        })
        for item in candidates if isinstance(item.get("selection_score_components"), dict)
        and len(item["selection_score_components"]) > 3
    ])
    stages.append([
        (item, "discovery_paths", item["discovery_paths"][:1])
        for item in candidates
        if isinstance(item.get("discovery_paths"), list) and len(item["discovery_paths"]) > 1
    ])
    stages.append([
        (item, "concept_matches", item["concept_matches"][:1])
        for item in candidates
        if isinstance(item.get("concept_matches"), list) and len(item["concept_matches"]) > 1
    ])
    stages.append([
        (item, "selection_lanes", item["selection_lanes"][:1])
        for item in candidates
        if isinstance(item.get("selection_lanes"), list) and len(item["selection_lanes"]) > 1
    ])
    stages.append([
        (item, "matched_kinds", None)
        for item in candidates if item.get("matched_kinds")
    ])
    stages.append([
        (item, "evidence", [
            evidence for index, evidence in enumerate(item["evidence"])
            if evidence.get("kind") != "readme_excerpt" or index <= next(
                (position for position, value in enumerate(item["evidence"])
                 if value.get("kind") == "readme_excerpt"),
                len(item["evidence"]),
            )
        ])
        for item in candidates
        if sum(evidence.get("kind") == "readme_excerpt" for evidence in item.get("evidence", [])) > 1
    ])
    stages.append([
        (item, "topics", item["topics"][:1])
        for item in candidates if isinstance(item.get("topics"), list) and len(item["topics"]) > 1
    ])
    stages.append([
        (item, "description", str(item["description"])[:96])
        for item in candidates if len(str(item.get("description") or "")) > 96
    ])
    stages.append([
        (item, "selection_score_components", {
            "recall": item["selection_score_components"].get("recall", 0),
        })
        for item in candidates if isinstance(item.get("selection_score_components"), dict)
        and len(item["selection_score_components"]) > 1
    ])

    for stage in stages:
        if apply(stage):
            return
    if _sync_output_bytes(output) > limit:
        raise ValueError(f"compact search output exceeds {limit} bytes after optional-field compaction")


def public_candidate(candidate: dict[str, Any], *, detailed: bool = False) -> dict[str, Any]:
    result = {key: value for key, value in candidate.items() if key in PUBLIC_CANDIDATE_FIELDS}
    license_value = result.get("license")
    if isinstance(license_value, dict):
        result["license"] = license_value.get("spdx_id")
    description = str(result.get("description") or "")
    if len(description) > 160:
        result["description"] = description[:157].rstrip() + "..."
    topics = result.get("topics")
    if isinstance(topics, list):
        result["topics"] = topics[:6]
    matches = result.get("concept_matches")
    if isinstance(matches, list):
        result["concept_matches"] = _unique(matches)[:3]
    lanes = result.get("selection_lanes")
    if isinstance(lanes, list):
        result["selection_lanes"] = list(dict.fromkeys(lanes))
    scores = result.get("selection_score_components")
    if isinstance(scores, dict):
        result["selection_score_components"] = {
            key: scores[key] for key in (
                "recall", "core_concept", "adjacent_concept", "underexposure", "evidence_completeness",
            ) if key in scores
        }
    for empty_key in ("homepage", "latest_release", "language"):
        if not result.get(empty_key):
            result.pop(empty_key, None)
    if result.get("readme_truncated") is False:
        result.pop("readme_truncated", None)
    release = result.get("latest_release")
    if isinstance(release, dict):
        release_id = f"repo:{str(candidate.get('full_name', '')).lower()}:release"
        if any(item.get("id") == release_id for item in candidate.get("evidence", [])):
            result["latest_release"] = {**release, "evidence_id": release_id}
    if not detailed:
        relationships = [path for path in candidate.get("discovery_paths", []) if path.get("kind") == "relationship"]
        queries = [path for path in candidate.get("discovery_paths", []) if path.get("kind") == "query"]
        queries.sort(key=lambda path: (int(path.get("position", 10)), str(path.get("query_kind", ""))))
        compact_paths = _unique(relationships[:2] + [{
            key: value for key, value in {
                "kind": "query", "query_kind": path.get("query_kind"),
                "position": path.get("position"), "concept_id": path.get("concept_id"),
            }.items() if value is not None
        } for path in queries[:2]])
        result["discovery_paths"] = compact_paths
        result["evidence"] = _pack_evidence(candidate)
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

    def _recall(self, search_id: str, queries: Iterable[dict[str, Any]],
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
                if query_spec.get("concept_id"):
                    path["concept_id"] = query_spec["concept_id"]
                if query_spec.get("term"):
                    path["term"] = query_spec["term"]
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

    @staticmethod
    def _concept_terms(request: SearchRequest) -> list[str]:
        terms: list[str] = []
        seen: set[str] = set()
        for concept in request.core_concepts + request.adjacent_concepts:
            for term in concept.terms():
                key = term.casefold()
                if key in seen:
                    continue
                seen.add(key)
                terms.append(term)
        return terms

    def _apply_readme(self, candidate: dict[str, Any], result: ApiResult | None,
                      request: SearchRequest) -> None:
        full_name = candidate["full_name"]
        readme = ""
        truncated = False
        if result is not None:
            payload = result.data
            if isinstance(payload, dict):
                candidate["readme_sha"] = payload.get("sha")
                raw_readme = str(payload.get("text", ""))
            else:
                raw_readme = str(payload)
            readme, truncated = safe_readme(raw_readme)
        candidate["readme"] = readme
        candidate["readme_truncated"] = truncated
        candidate["readme_links"] = github_links(readme, full_name)
        existing = {item.get("id"): item for item in candidate.get("evidence", [])}
        for item in make_evidence(
            candidate, readme, truncated, concept_terms=self._concept_terms(request),
            artifact_types=request.artifact_types,
        ):
            existing[item["id"]] = item
        candidate["evidence"] = list(existing.values())

    def _enrich(self, candidates: dict[str, dict[str, Any]], request: SearchRequest,
                limit: int | None = None) -> tuple[bool, str | None, bool, int]:
        stale = False
        cached_at = None
        failed = False
        missing = [item for item in candidates.values() if "readme" not in item]
        if not missing:
            return False, None, False, 0
        selected, _ = probe_select(missing, request, limit=limit or self.enrich_limit)
        pending = selected

        def fetch(candidate: dict[str, Any]) -> tuple[dict[str, Any], ApiResult | None | str]:
            try:
                return candidate, self.github.readme(candidate["full_name"])
            except GitHubNotFoundError:
                return candidate, None
            except GitHubAuthenticationError:
                raise
            except GitHubError:
                return candidate, "error"

        if pending:
            with ThreadPoolExecutor(max_workers=min(12, max(1, len(pending)))) as pool:
                fetched = list(pool.map(fetch, pending))
        else:
            fetched = []
        for candidate, result in fetched:
            if result == "error":
                failed = True
                result = None
            if result is not None:
                stale = stale or result.stale
                cached_at = cached_at or result.cached_at
            self._apply_readme(candidate, result, request)
        return stale, cached_at, failed, len(pending)

    def _enrich_releases(self, selected: list[dict[str, Any]]) -> tuple[bool, str | None, bool]:
        stale = False
        cached_at = None
        failed = False
        pending = [item for item in selected if not item.get("release_checked")]

        def fetch(candidate: dict[str, Any]) -> tuple[dict[str, Any], ApiResult | None | str]:
            try:
                return candidate, self.github.latest_release(candidate["full_name"])
            except (GitHubNotFoundError, AttributeError):
                return candidate, None
            except GitHubAuthenticationError:
                raise
            except GitHubError:
                return candidate, "error"

        if pending:
            with ThreadPoolExecutor(max_workers=min(8, max(1, len(pending)))) as pool:
                fetched = list(pool.map(fetch, pending))
        else:
            fetched = []
        for candidate, result in fetched:
            candidate["release_checked"] = True
            if result == "error":
                failed = True
                result = None
            if result is None:
                continue
            stale = stale or result.stale
            cached_at = cached_at or result.cached_at
            if isinstance(result.data, dict):
                candidate["latest_release"] = {
                    "tag_name": result.data.get("tag_name"),
                    "published_at": result.data.get("published_at"),
                    "html_url": result.data.get("html_url"),
                }
                existing = {item.get("id"): item for item in candidate.get("evidence", [])}
                release_id = f"repo:{candidate['full_name'].lower()}:release"
                existing[release_id] = {
                    "id": release_id, "kind": "github_release",
                    "source": candidate["latest_release"].get("html_url"),
                    "facts": dict(candidate["latest_release"]),
                }
                candidate["evidence"] = list(existing.values())
        return stale, cached_at, failed

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

    def search(self, request: SearchRequest, mode: str, *, refresh: bool = False) -> dict[str, Any]:
        if mode not in {"quick", "deep"}:
            raise ValueError("mode must be quick or deep")
        fingerprint = request.fingerprint(mode)
        if not refresh:
            existing = self.store.find_complete_search(fingerprint, mode)
            if existing:
                return self._reused_output(existing, mode)
        search_id = uuid.uuid4().hex
        self.store.create_search(search_id, request.to_dict(), mode, fingerprint)
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
        _compact_search_output(output)
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
        readme_budget = self.enrich_limit
        s, cache_time, first_enrich_failed, first_enriched = self._enrich(
            candidates, request, limit=readme_budget,
        )
        stale, cached_at = stale or s, cached_at or cache_time
        s, cache_time, calls, relation_failed = self._expand_relations(
            search_id, candidates, request, parsed_refinement.seeds or None
        )
        stale, cached_at = stale or s, cached_at or cache_time
        remaining = max(0, readme_budget - first_enriched)
        second_enrich_failed = False
        second_enriched = 0
        if remaining:
            s, cache_time, second_enrich_failed, second_enriched = self._enrich(
                candidates, request, limit=remaining,
            )
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
        _compact_search_output(output)
        return output

    def _coverage(self, search_id: str, candidates: Iterable[dict[str, Any]],
                  selected: list[dict[str, Any]], request: SearchRequest, *,
                  enriched_count: int, relation_calls: int, code_calls: int,
                  lane_counts: dict[str, int]) -> dict[str, Any]:
        items = list(candidates)
        core_total = len(indexed_groups(request.core_concepts, "core"))
        core_covered = len(covered_core_ids(selected, request))
        return {
            "queries_executed": self.store.query_count(search_id),
            "enriched": sum("readme" in item for item in items),
            "enriched_this_phase": enriched_count,
            "omitted": max(0, len(items) - len(selected)),
            "relation_calls": relation_calls, "code_search_calls": code_calls,
            "lanes": lane_counts,
            "api_calls": dict(getattr(self.github, "request_counts", {})),
            "core_concepts_total": core_total,
            "core_concepts_covered": core_covered,
            "uncovered_core_concepts": uncovered_core_terms(selected, request),
        }

    def _finish(self, search_id: str, candidates: dict[str, dict[str, Any]], request: SearchRequest,
                stale: bool, cached_at: str | None, incomplete: str | None, *,
                enriched_count: int, relation_calls: int, code_calls: int) -> dict[str, Any]:
        candidates = {
            name: item for name, item in candidates.items()
            if candidate_allowed(item, request, include_readme="readme" in item)
        }
        concept_terms = self._concept_terms(request)
        for candidate in candidates.values():
            candidate.setdefault("evidence", make_evidence(
                candidate, "", False, concept_terms=concept_terms,
                artifact_types=request.artifact_types,
            ))
            candidate["matched_kinds"] = sorted(set(candidate.get("matched_kinds", [])))
            candidate["selected_for_assessment"] = False
        assessable = [item for item in candidates.values() if "readme" in item]
        selected, lane_counts = shortlist_select(assessable, request)
        selected = selected[:SHORTLIST_LIMIT]
        try:
            release_stale, release_cache, release_failed = self._enrich_releases(selected)
            stale = stale or release_stale
            cached_at = cached_at or release_cache
            if release_failed and incomplete is None:
                incomplete = "enrichment_partial_failure"
        except GitHubAuthenticationError:
            raise
        except GitHubError:
            if incomplete is None:
                incomplete = "enrichment_partial_failure"
        for candidate in selected:
            candidate["selected_for_assessment"] = True
        self.store.retain_search_candidates(search_id, list(candidates))
        for candidate in candidates.values():
            self.store.save_candidate(search_id, candidate)
        self.store.mark_search(search_id, stale=stale, incomplete_phase=incomplete)
        coverage = self._coverage(
            search_id, candidates.values(), selected, request,
            enriched_count=enriched_count, relation_calls=relation_calls,
            code_calls=code_calls, lane_counts=lane_counts,
        )
        return self._search_output(
            search_id, candidates.values(), selected, stale, cached_at, incomplete, coverage
        )

    def _reused_output(self, search_id: str, mode: str) -> dict[str, Any]:
        session = self.store.load_search(search_id)
        request = SearchRequest.from_dict(session["request"])
        selected = [item for item in session["candidates"] if item.get("selected_for_assessment")]
        lane_counts: dict[str, int] = {}
        for item in selected:
            lane = (item.get("selection_reason") or {}).get("lane")
            if lane:
                lane_counts[lane] = lane_counts.get(lane, 0) + 1
        coverage = self._coverage(
            search_id, session["candidates"], selected, request,
            enriched_count=0, relation_calls=0, code_calls=0,
            lane_counts=lane_counts,
        )
        coverage["reused"] = True
        coverage["api_calls"] = {}
        output = self._search_output(
            search_id, session["candidates"], selected, bool(session["stale"]),
            session.get("updated_at"), session.get("incomplete_phase"), coverage,
        )
        output["reused"] = True
        output["next_action"] = "expand" if mode == "deep" else "rank"
        _compact_search_output(output)
        return output

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
        output = {
            "schema_version": 2, "search_id": search_id, "stale": stale,
            "cache_time": cached_at, "incomplete_phase": incomplete,
            "candidate_count": len(all_candidates), "assessment_candidate_count": len(items),
            "candidates": items, "coverage": coverage,
        }
        return output
