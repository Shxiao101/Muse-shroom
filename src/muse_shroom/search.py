from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable

from .analyze import _is_thin_overview, github_links, make_evidence, safe_readme
from .boundary import annotate_candidate_mechanisms, build_boundary
from .github import (
    ApiResult, GitHubAuthenticationError, GitHubClient, GitHubError,
    GitHubNotFoundError,
)
from .iteration import (
    apply_hypothesis_to_request, build_observation, default_session_state,
    hard_stop_reason, iteration_stop_reasons, meaningful_gain, merge_unique,
    remaining_budget,
)
from .models import (
    DEFAULT_CONSECUTIVE_NO_GAIN, DEFAULT_DEEP_CANDIDATE_LIMIT,
    DEFAULT_MAX_ITERATIONS, DEFAULT_QUERIES_PER_ITERATION,
    DEFAULT_QUICK_CANDIDATE_LIMIT, DEFAULT_README_ENRICH_PER_ITERATION,
    DEFAULT_SESSION_QUERY_BUDGET, HARD_STOP_REASONS,
    Refinement, SearchHypothesis, SearchRequest, repo_key,
)
from .queries import (
    build_queries, code_filename_query, hypothesis_queries, indexed_groups,
    query_fingerprint, reverse_reference_query,
)
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
    "mechanisms",
}

CONCEPT_MATCH_CHARS = 240
HOWTO_EXCERPT_CHARS = 220
MECHANISM_EVIDENCE_CHARS = 180
PUBLIC_MECHANISM_LIMIT = 3
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


def _compact_mechanism_evidence(item: dict[str, Any], names: set[str]) -> dict[str, Any]:
    facts = item.get("facts") or {}
    matches = []
    for match in facts.get("mechanisms") or []:
        if str(match.get("mechanism") or "").casefold() not in names:
            continue
        matches.append({
            key: (
                " ".join(str(value).split())[:MECHANISM_EVIDENCE_CHARS]
                if key == "text" else value
            )
            for key, value in match.items()
            if value is not None
        })
    return {
        "id": item.get("id"), "kind": "mechanism_match",
        **({"source": item.get("source")} if item.get("source") else {}),
        "facts": {
            "mechanisms": matches,
            "untrusted_source": any(bool(match.get("untrusted_source")) for match in matches),
        },
    }


def _pack_evidence(candidate: dict[str, Any], mechanism_names: set[str] | None = None
                   ) -> list[dict[str, Any]]:
    metadata = None
    mechanism = None
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
        elif kind == "mechanism_match" and mechanism is None:
            names = mechanism_names or {
                str(value.get("name") or "").casefold()
                for value in candidate.get("mechanisms") or []
            }
            mechanism = _compact_mechanism_evidence(item, names)
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
    if mechanism and (mechanism.get("facts") or {}).get("mechanisms"):
        chosen.append(mechanism)
    howto = by_type.get("usage") or by_type.get("installation")
    if howto and howto is not primary and len(chosen) < 3:
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
        (item, "mechanisms", item["mechanisms"][:2])
        for item in candidates
        if isinstance(item.get("mechanisms"), list) and len(item["mechanisms"]) > 2
    ])
    stages.append([
        (match, "text", str(match.get("text") or "")[:100])
        for item in candidates for evidence in item.get("evidence") or []
        if evidence.get("kind") == "mechanism_match"
        for match in (evidence.get("facts") or {}).get("mechanisms") or []
        if len(str(match.get("text") or "")) > 100
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
        mechanisms = list(result.get("mechanisms") or [])[:PUBLIC_MECHANISM_LIMIT]
        result["mechanisms"] = mechanisms
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
        result["evidence"] = _pack_evidence(
            candidate, {str(item.get("name") or "").casefold() for item in mechanisms},
        )
    return result


class SearchEngine:
    def __init__(self, store: Store, github: GitHubClient | None = None, *,
                 candidate_limit: int | None = None,
                 enrich_limit: int = 30, relation_budget: int = 40,
                 max_iterations: int = DEFAULT_MAX_ITERATIONS,
                 queries_per_iteration: int = DEFAULT_QUERIES_PER_ITERATION,
                 session_query_budget: int = DEFAULT_SESSION_QUERY_BUDGET,
                 readme_enrich_per_iteration: int = DEFAULT_README_ENRICH_PER_ITERATION,
                 consecutive_no_gain_limit: int = DEFAULT_CONSECUTIVE_NO_GAIN) -> None:
        self.store = store
        self.github = github
        self.candidate_limit = candidate_limit
        self.enrich_limit = enrich_limit
        self.relation_budget = relation_budget
        self.max_iterations = max_iterations
        self.queries_per_iteration = queries_per_iteration
        self.session_query_budget = session_query_budget
        self.readme_enrich_per_iteration = readme_enrich_per_iteration
        self.consecutive_no_gain_limit = consecutive_no_gain_limit
        self._pool_cap = candidate_limit or DEFAULT_QUICK_CANDIDATE_LIMIT

    def _limit_for(self, mode: str | None = None, state: dict[str, Any] | None = None) -> int:
        if self.candidate_limit is not None:
            return self.candidate_limit
        if state and state.get("candidate_limit"):
            return int(state["candidate_limit"])
        if mode == "deep":
            return DEFAULT_DEEP_CANDIDATE_LIMIT
        return DEFAULT_QUICK_CANDIDATE_LIMIT

    @staticmethod
    def _items(result: ApiResult) -> list[dict[str, Any]]:
        data = result.data
        return list(data.get("items", [])) if isinstance(data, dict) else list(data)

    def _recall(self, search_id: str, queries: Iterable[dict[str, Any]],
                candidates: dict[str, dict[str, Any]], *,
                iteration: int = 0,
                known_fingerprints: set[str] | None = None
                ) -> tuple[bool, str | None, list[dict[str, Any]], list[dict[str, Any]]]:
        stale = False
        cached_at = None
        known = set(known_fingerprints or [])
        executable: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for spec in queries:
            fingerprint = str(spec.get("fingerprint") or query_fingerprint(spec["query"]))
            item = {**spec, "fingerprint": fingerprint}
            if fingerprint in known:
                skipped.append({**item, "skipped": True, "skip_reason": "duplicate"})
                self.store.add_query_history(
                    search_id, item["query"], item["kind"], 0,
                    iteration=iteration, fingerprint=fingerprint, skipped=True,
                    skip_reason="duplicate",
                )
                continue
            known.add(fingerprint)
            executable.append(item)
        query_list = executable
        if not query_list:
            return stale, cached_at, executable, skipped
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
            self.store.add_query_history(
                search_id, query_spec["query"], query_spec["kind"], len(items),
                iteration=iteration, fingerprint=query_spec["fingerprint"], skipped=False,
            )
            for position, repo in enumerate(items, 1):
                if repo.get("private") or repo.get("visibility") not in {None, "public"}:
                    continue
                key = repo_key(repo)
                if not key:
                    continue
                candidate = candidates.setdefault(key, dict(repo))
                if "first_seen_iteration" not in candidate:
                    candidate["first_seen_iteration"] = iteration
                candidate["last_seen_iteration"] = iteration
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
                lane_kind = query_spec.get("lane_kind", query_spec["kind"])
                if lane_kind not in kinds:
                    kinds.append(lane_kind)
                if len(candidates) >= self._pool_cap:
                    return stale, cached_at, executable, skipped
        return stale, cached_at, executable, skipped

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
                     parent: str, relation: str, detail: str, request: SearchRequest,
                     *, iteration: int = 0) -> None:
        key = repo_key(repo)
        if repo.get("private") or repo.get("visibility") not in {None, "public"}:
            return
        if not key or (key == parent.lower() and relation != "key_file"):
            return
        if not candidate_allowed(repo, request, include_readme=False):
            return
        if key not in candidates and len(candidates) >= self._pool_cap:
            return
        candidate = candidates.setdefault(key, dict(repo))
        if "first_seen_iteration" not in candidate:
            candidate["first_seen_iteration"] = iteration
        candidate["last_seen_iteration"] = iteration
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
                          request: SearchRequest, seed_names: list[str] | None = None,
                          *, iteration: int = 0) -> tuple[bool, str | None, int, bool]:
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
                    self._add_related(
                        candidates, result.data, full_name, "readme_link", linked, request,
                        iteration=iteration,
                    )
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
                    self.store.add_query_history(
                        search_id, query, "reverse_readme", len(items),
                        iteration=iteration, fingerprint=query_fingerprint(query), skipped=False,
                    )
                    for item in items:
                        self._add_related(
                            candidates, item, full_name, "reverse_readme", query, request,
                            iteration=iteration,
                        )
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
                        self._add_related(
                            candidates, item, full_name, "fork", "GitHub forks endpoint", request,
                            iteration=iteration,
                        )
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
                        self._add_related(
                            candidates, item, full_name, "same_owner", owner, request,
                            iteration=iteration,
                        )
                except GitHubAuthenticationError:
                    raise
                except GitHubError:
                    failed = True
        return stale, cached_at, calls, failed

    @staticmethod
    def _query_summary(executed: list[dict[str, Any]], skipped: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "executed": [
                {key: item[key] for key in ("query", "kind", "term") if key in item}
                for item in executed
            ],
            "skipped": [
                {"query": item.get("query"), "reason": item.get("skip_reason", "duplicate")}
                for item in skipped
            ],
            "executed_count": len(executed),
            "skipped_count": len(skipped),
            "duplicate_rate": round(
                sum(1 for item in skipped if item.get("skip_reason", "duplicate") == "duplicate")
                / max(1, len(executed) + len(skipped)),
                3,
            ),
        }

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
        state = default_session_state()
        self._pool_cap = self._limit_for(mode)
        state["candidate_limit"] = self._pool_cap
        state["max_iterations"] = self.max_iterations
        state["session_query_budget"] = self.session_query_budget
        state["queries_per_iteration"] = self.queries_per_iteration
        state["readme_enrich_per_iteration"] = self.readme_enrich_per_iteration
        state["relation_budget"] = self.relation_budget
        self.store.save_session_state(search_id, state)
        candidates: dict[str, dict[str, Any]] = {}
        stale = False
        cached_at = None
        incomplete = None
        enriched_count = 0
        executed: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        try:
            s, cache_time, executed, skipped = self._recall(
                search_id, build_queries(request), candidates, iteration=0,
            )
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
            stage="search", rejected_directions=[], iteration=0,
            query_summary=self._query_summary(executed, skipped),
        )
        output["next_action"] = "iterate" if mode == "deep" else "rank"
        if output.get("observation"):
            output["observation"]["stop"]["should_stop"] = False
        _compact_search_output(output)
        return output

    def expand(self, search_id: str, refinement: dict[str, Any]) -> dict[str, Any]:
        return self._run_iteration(
            search_id, SearchHypothesis.from_refinement(Refinement.from_dict(refinement)),
            stage="expand",
        )

    def iterate(self, search_id: str, refinement: dict[str, Any]) -> dict[str, Any]:
        return self._run_iteration(search_id, SearchHypothesis.from_dict(refinement), stage="iterate")

    def observe(self, search_id: str) -> dict[str, Any]:
        session = self.store.load_search(search_id)
        request = SearchRequest.from_dict(session["request"])
        for candidate in session["candidates"]:
            annotate_candidate_mechanisms(candidate, request)
        selected = [item for item in session["candidates"] if item.get("selected_for_assessment")]
        snapshot = self.store.latest_boundary_snapshot(search_id) or {}
        state = self.store.get_session_state(search_id)
        boundary = build_boundary(
            session["candidates"], selected, request,
            rejected_directions=(snapshot.get("boundary") or {}).get("rejected_directions", []),
            negative_directions=state.get("negative_directions") or [],
        ).to_dict()
        remaining = remaining_budget(
            iteration=int(state.get("iteration") or 0),
            queries_used=self.store.query_count(search_id),
            relation_calls_used=int(state.get("relation_calls_used") or 0),
            max_iterations=int(state["max_iterations"]) if "max_iterations" in state else self.max_iterations,
            queries_per_iteration=(
                int(state["queries_per_iteration"]) if "queries_per_iteration" in state
                else self.queries_per_iteration
            ),
            session_query_budget=(
                int(state["session_query_budget"]) if "session_query_budget" in state
                else self.session_query_budget
            ),
            readme_enrich_per_iteration=(
                int(state["readme_enrich_per_iteration"]) if "readme_enrich_per_iteration" in state
                else self.readme_enrich_per_iteration
            ),
            relation_budget=int(state["relation_budget"]) if "relation_budget" in state else self.relation_budget,
        )
        coverage = {
            "queries_executed": self.store.query_count(search_id),
            "mechanism_count": len(boundary.get("recalled_mechanisms") or []),
            "presented_mechanism_count": len(boundary.get("presented_mechanisms") or []),
            "direction_coverage": round(
                len(boundary.get("explored_directions") or [])
                / max(1, len(boundary.get("explored_directions") or [])
                      + len(boundary.get("unexplored_directions") or [])),
                3,
            ),
        }
        stop_reason = state.get("stop_reason")
        hard = stop_reason in HARD_STOP_REASONS
        observation = build_observation(
            iteration=int(state.get("iteration") or 0),
            boundary=boundary,
            boundary_delta=snapshot.get("boundary_delta") or {},
            coverage=coverage,
            query_summary={"executed": [], "skipped": [], "executed_count": 0},
            candidates=session["candidates"], selected=selected, request=request,
            remaining=remaining,
            stop_reasons=[stop_reason] if hard and stop_reason else [],
            hard_stop=hard,
            exploration_additions=list(state.get("exploration_additions") or []),
            consecutive_no_gain=int(state.get("consecutive_no_gain") or 0),
        )
        mode = str(session.get("mode") or "quick")
        can_iterate = (
            mode == "deep"
            and remaining["iterations"] > 0
            and remaining["queries"] > 0
            and not hard
        )
        if self.store.get_ranking(search_id):
            next_action = "done"
        elif mode != "deep":
            next_action = "rank"
        elif not can_iterate:
            next_action = "rank"
        else:
            next_action = "iterate"
        return {
            "schema_version": 2,
            "search_id": search_id,
            "mode": mode,
            "iteration": int(state.get("iteration") or 0),
            "observation": observation,
            "boundary": boundary,
            "remaining_budget": remaining,
            "next_action": next_action,
            "can_iterate": can_iterate,
            "stale": bool(session["stale"]),
            "incomplete_phase": session.get("incomplete_phase"),
        }

    def _run_iteration(self, search_id: str, hypothesis: SearchHypothesis, *, stage: str) -> dict[str, Any]:
        session = self.store.load_search(search_id)
        state = self.store.get_session_state(search_id)
        request = SearchRequest.from_dict(session["request"])
        candidates = {repo_key(item): item for item in session["candidates"]}
        previous_snapshot = self.store.latest_boundary_snapshot(search_id) or {}
        previous_boundary = previous_snapshot.get("boundary") or {}
        previous_origins = previous_boundary.get("mechanism_origins") or {}
        queries_used = self.store.query_count(search_id)
        remaining = remaining_budget(
            iteration=state["iteration"],
            queries_used=queries_used,
            relation_calls_used=int(state.get("relation_calls_used") or 0),
            max_iterations=self.max_iterations,
            queries_per_iteration=self.queries_per_iteration,
            session_query_budget=self.session_query_budget,
            readme_enrich_per_iteration=self.readme_enrich_per_iteration,
            relation_budget=self.relation_budget,
        )
        self._pool_cap = self._limit_for(session.get("mode"), state)
        hard = hard_stop_reason(
            iteration=state["iteration"], queries_used=queries_used,
            max_iterations=self.max_iterations,
            session_query_budget=self.session_query_budget,
            decision=hypothesis.decision,
        )
        if hard:
            event = "stop" if hypothesis.decision == "stop" else "refuse"
            state["stop_reason"] = hard
            self.store.save_session_state(search_id, state)
            self.store.save_iteration(
                search_id, int(state["iteration"]), stage, hypothesis.to_dict(),
                self._query_summary([], []), hard, event=event,
            )
            output = self._decision_event_output(
                search_id, session, request, candidates, hypothesis, remaining, hard,
            )
            _compact_search_output(output)
            return output

        iteration = int(state["iteration"]) + 1
        negatives = merge_unique(state.get("negative_directions") or [], hypothesis.negative_directions)
        rejected = merge_unique(
            previous_boundary.get("rejected_directions") or [], hypothesis.rejected_directions,
        )
        request, additions = apply_hypothesis_to_request(
            request, hypothesis, iteration=iteration,
            existing_additions=list(state.get("exploration_additions") or []),
        )
        self.store.update_search_request(search_id, request.to_dict())
        strategies = hypothesis.resolved_strategies()
        stale = bool(session["stale"])
        cached_at = None
        executed: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        recall_failed = False
        query_limit = 10 if stage == "expand" else remaining["queries_this_round"]
        if "keyword" in strategies and query_limit:
            planned, blocked = hypothesis_queries(
                hypothesis, request, negatives=negatives,
                known_fingerprints=self.store.query_fingerprints(search_id),
                limit=query_limit,
            )
            for item in blocked:
                self.store.add_query_history(
                    search_id, item["query"], item["kind"], 0,
                    iteration=iteration, fingerprint=item["fingerprint"], skipped=True,
                    skip_reason=str(item.get("skip_reason") or "duplicate"),
                )
            skipped.extend(blocked)
            try:
                recalled_stale, recalled_cache, executed, recall_skipped = self._recall(
                    search_id, planned, candidates, iteration=iteration,
                    known_fingerprints=self.store.query_fingerprints(search_id),
                )
                stale, cached_at = stale or recalled_stale, recalled_cache
                skipped.extend(recall_skipped)
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
        if "code" in strategies:
            for filename in hypothesis.filenames:
                query = code_filename_query(
                    filename, hypothesis.concepts[0] if hypothesis.concepts else None,
                )
                code_calls += 1
                try:
                    result = self.github.search_code(query, per_page=10)
                    stale = stale or result.stale
                    cached_at = cached_at or result.cached_at
                    items = self._items(result)
                    self.store.add_query(search_id, query, "key_file", len(items))
                    self.store.add_query_history(
                        search_id, query, "key_file", len(items),
                        iteration=iteration, fingerprint=query_fingerprint(query), skipped=False,
                    )
                    for item in items:
                        repository = item.get("repository", item)
                        self._add_related(
                            candidates, repository, repository.get("full_name", "unknown"),
                            "key_file", filename, request, iteration=iteration,
                        )
                except GitHubAuthenticationError:
                    raise
                except GitHubError:
                    code_failed = True
        readme_budget = self.enrich_limit if stage == "expand" else self.readme_enrich_per_iteration
        s, cache_time, first_enrich_failed, first_enriched = self._enrich(
            candidates, request, limit=readme_budget,
        )
        stale, cached_at = stale or s, cached_at or cache_time
        calls = 0
        relation_failed = False
        want_relations = any(name in strategies for name in ("relationship", "seed", "owner"))
        if want_relations and remaining["relation_calls"]:
            original_budget = self.relation_budget
            if stage == "iterate":
                self.relation_budget = remaining["relation_calls"]
            try:
                s, cache_time, calls, relation_failed = self._expand_relations(
                    search_id, candidates, request,
                    hypothesis.seeds or None, iteration=iteration,
                )
            finally:
                self.relation_budget = original_budget
            stale, cached_at = stale or s, cached_at or cache_time
        leftover = max(0, readme_budget - first_enriched)
        second_enrich_failed = False
        second_enriched = 0
        if leftover:
            s, cache_time, second_enrich_failed, second_enriched = self._enrich(
                candidates, request, limit=leftover,
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
        state["iteration"] = iteration
        state["negative_directions"] = negatives
        state["exploration_additions"] = additions
        state["relation_calls_used"] = int(state.get("relation_calls_used") or 0) + calls
        after_remaining = remaining_budget(
            iteration=iteration,
            queries_used=self.store.query_count(search_id),
            relation_calls_used=state["relation_calls_used"],
            max_iterations=self.max_iterations,
            queries_per_iteration=self.queries_per_iteration,
            session_query_budget=self.session_query_budget,
            readme_enrich_per_iteration=self.readme_enrich_per_iteration,
            relation_budget=self.relation_budget,
        )
        output = self._finish(
            search_id, candidates, request, stale, cached_at, incomplete,
            enriched_count=first_enriched + second_enriched,
            relation_calls=calls, code_calls=code_calls, stage=stage,
            rejected_directions=rejected, iteration=iteration,
            negative_directions=negatives, hypothesis=hypothesis.to_dict(),
            query_summary=self._query_summary(executed, skipped),
            remaining=after_remaining, exploration_additions=additions,
        )
        delta = output.get("boundary_delta") or {}
        executed_any = bool(executed) or calls > 0 or code_calls > 0
        skipped_all = bool(skipped) and not executed_any
        gained = meaningful_gain(
            delta, previous_origins, (output.get("boundary") or {}).get("mechanism_origins"),
        )
        if executed_any and not gained:
            state["consecutive_no_gain"] = int(state.get("consecutive_no_gain") or 0) + 1
        elif executed_any:
            state["consecutive_no_gain"] = 0
        pre_hard = "duplicate_queries" if skipped_all else None
        if after_remaining["iterations"] <= 0:
            pre_hard = pre_hard or "max_iterations"
        hard_reasons, signals = iteration_stop_reasons(
            hard_reason=pre_hard,
            delta=delta, boundary=output.get("boundary") or {},
            skipped_all=skipped_all, executed=executed_any,
            previous_origins=previous_origins,
            current_origins=(output.get("boundary") or {}).get("mechanism_origins"),
            consecutive_no_gain=int(state.get("consecutive_no_gain") or 0),
            consecutive_limit=self.consecutive_no_gain_limit,
        )
        hard_after = bool(hard_reasons)
        next_action = "rank" if (stage == "expand" or hard_after) else "iterate"
        stop_reason = hard_reasons[0] if hard_after else None
        state["stop_reason"] = stop_reason
        self.store.save_session_state(search_id, state)
        self.store.save_iteration(
            search_id, iteration, stage, hypothesis.to_dict(),
            self._query_summary(executed, skipped), stop_reason, event=stage,
        )
        if output.get("observation"):
            output["observation"]["stop"] = {
                "should_stop": hard_after,
                "hard": hard_after,
                "reasons": hard_reasons,
                "signals": signals,
                "consecutive_no_gain": int(state.get("consecutive_no_gain") or 0),
            }
            output["observation"]["remaining_budget"] = after_remaining
        output["next_action"] = next_action
        if stop_reason:
            output["stop_reason"] = stop_reason
        _compact_search_output(output)
        return output

    def _decision_event_output(self, search_id: str, session: dict[str, Any],
                               request: SearchRequest, candidates: dict[str, dict[str, Any]],
                               hypothesis: SearchHypothesis, remaining: dict[str, int],
                               reason: str) -> dict[str, Any]:
        selected = [item for item in candidates.values() if item.get("selected_for_assessment")]
        if not selected:
            selected = list(candidates.values())[:12]
        snapshot = self.store.latest_boundary_snapshot(search_id) or {}
        boundary = snapshot.get("boundary") or {}
        delta = snapshot.get("boundary_delta") or {}
        state = self.store.get_session_state(search_id)
        coverage = self._coverage(
            search_id, candidates.values(), selected, request,
            enriched_count=0, relation_calls=0, code_calls=0, lane_counts={},
        )
        coverage.update({
            "iteration": int(state.get("iteration") or 0),
            "mechanism_count": len(boundary.get("recalled_mechanisms") or []),
            "presented_mechanism_count": len(boundary.get("presented_mechanisms") or []),
            "direction_coverage": round(
                len(boundary.get("explored_directions") or [])
                / max(1, len(boundary.get("explored_directions") or [])
                      + len(boundary.get("unexplored_directions") or [])),
                3,
            ),
        })
        output = self._search_output(
            search_id, candidates.values(), selected, bool(session["stale"]),
            session.get("updated_at"), session.get("incomplete_phase"), coverage,
            boundary, delta,
        )
        output["iteration"] = int(state.get("iteration") or 0)
        output["observation"] = build_observation(
            iteration=output["iteration"], boundary=boundary, boundary_delta=delta,
            coverage=coverage, query_summary=self._query_summary([], []),
            candidates=candidates.values(), selected=selected, request=request,
            remaining=remaining, stop_reasons=[reason], hard_stop=True,
            exploration_additions=list(state.get("exploration_additions") or []),
            consecutive_no_gain=int(state.get("consecutive_no_gain") or 0),
        )
        output["next_action"] = "rank"
        output["stop_reason"] = reason
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
                enriched_count: int, relation_calls: int, code_calls: int,
                stage: str, rejected_directions: list[str],
                iteration: int = 0,
                negative_directions: list[str] | None = None,
                hypothesis: dict[str, Any] | None = None,
                query_summary: dict[str, Any] | None = None,
                stop_reasons: list[str] | None = None,
                hard_stop: bool = False,
                remaining: dict[str, int] | None = None,
                exploration_additions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
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
            annotate_candidate_mechanisms(candidate, request)
            candidate["matched_kinds"] = sorted(set(candidate.get("matched_kinds", [])))
            candidate["selected_for_assessment"] = False
        assessable = [item for item in candidates.values() if "readme" in item]
        mode = "quick"
        try:
            mode = str(self.store.load_search(search_id).get("mode") or "quick")
        except KeyError:
            pass
        selected, lane_counts = shortlist_select(assessable, request, mode=mode)
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
        negatives = list(negative_directions or [])
        boundary = build_boundary(
            candidates.values(), selected, request,
            rejected_directions=rejected_directions,
            negative_directions=negatives,
        ).to_dict()
        coverage.update({
            "iteration": iteration,
            "mechanism_count": len(boundary["recalled_mechanisms"]),
            "presented_mechanism_count": len(boundary["presented_mechanisms"]),
            "direction_coverage": round(
                len(boundary["explored_directions"])
                / max(1, len(boundary["explored_directions"]) + len(boundary["unexplored_directions"])),
                3,
            ),
        })
        summary = query_summary or {"executed": [], "skipped": [], "executed_count": 0, "skipped_count": 0}
        boundary_delta = self.store.save_boundary_snapshot(
            search_id, stage, boundary, iteration=iteration,
            hypothesis=hypothesis, query_summary=summary,
        )
        budget = remaining or remaining_budget(
            iteration=iteration,
            queries_used=self.store.query_count(search_id),
            relation_calls_used=relation_calls,
            max_iterations=self.max_iterations,
            queries_per_iteration=self.queries_per_iteration,
            session_query_budget=self.session_query_budget,
            readme_enrich_per_iteration=self.readme_enrich_per_iteration,
            relation_budget=self.relation_budget,
        )
        output = self._search_output(
            search_id, candidates.values(), selected, stale, cached_at, incomplete, coverage,
            boundary, boundary_delta,
        )
        reasons = list(stop_reasons or [])
        output["iteration"] = iteration
        output["observation"] = build_observation(
            iteration=iteration, boundary=boundary, boundary_delta=boundary_delta,
            coverage=coverage, query_summary=summary,
            candidates=candidates.values(), selected=selected, request=request,
            remaining=budget, stop_reasons=reasons, hard_stop=hard_stop,
            exploration_additions=exploration_additions,
        )
        return output

    def _reused_output(self, search_id: str, mode: str) -> dict[str, Any]:
        session = self.store.load_search(search_id)
        request = SearchRequest.from_dict(session["request"])
        for candidate in session["candidates"]:
            annotate_candidate_mechanisms(candidate, request)
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
        snapshot = self.store.latest_boundary_snapshot(search_id, ("search", "expand", "iterate")) or {}
        state = self.store.get_session_state(search_id)
        boundary = build_boundary(
            session["candidates"], selected, request,
            rejected_directions=(snapshot.get("boundary") or {}).get("rejected_directions", []),
            negative_directions=state.get("negative_directions") or [],
        ).to_dict()
        explored = boundary.get("explored_directions", [])
        unexplored = boundary.get("unexplored_directions", [])
        coverage.update({
            "iteration": state.get("iteration") or 0,
            "mechanism_count": len(boundary.get("recalled_mechanisms", [])),
            "presented_mechanism_count": len(boundary.get("presented_mechanisms", [])),
            "direction_coverage": round(len(explored) / max(1, len(explored) + len(unexplored)), 3),
        })
        output = self._search_output(
            search_id, session["candidates"], selected, bool(session["stale"]),
            session.get("updated_at"), session.get("incomplete_phase"), coverage,
            boundary, snapshot.get("boundary_delta", {}),
        )
        remaining = remaining_budget(
            iteration=int(state.get("iteration") or 0),
            queries_used=self.store.query_count(search_id),
            relation_calls_used=int(state.get("relation_calls_used") or 0),
            max_iterations=self.max_iterations,
            queries_per_iteration=self.queries_per_iteration,
            session_query_budget=self.session_query_budget,
            readme_enrich_per_iteration=self.readme_enrich_per_iteration,
            relation_budget=self.relation_budget,
        )
        output["iteration"] = int(state.get("iteration") or 0)
        output["observation"] = build_observation(
            iteration=output["iteration"], boundary=boundary,
            boundary_delta=snapshot.get("boundary_delta") or {},
            coverage=coverage, query_summary={"executed": [], "skipped": [], "executed_count": 0},
            candidates=session["candidates"], selected=selected, request=request,
            remaining=remaining, stop_reasons=[], hard_stop=False,
            exploration_additions=list(state.get("exploration_additions") or []),
        )
        output["reused"] = True
        output["next_action"] = "iterate" if mode == "deep" else "rank"
        _compact_search_output(output)
        return output

    @staticmethod
    def _search_output(search_id: str, all_candidates: Iterable[dict[str, Any]],
                       selected: Iterable[dict[str, Any]], stale: bool,
                       cached_at: str | None, incomplete: str | None,
                       coverage: dict[str, Any], boundary: dict[str, Any],
                       boundary_delta: dict[str, Any]) -> dict[str, Any]:
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
            "boundary": boundary, "boundary_delta": boundary_delta,
        }
        return output
