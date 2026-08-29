"""Stable read-only Explorer views. Does not search, iterate, rank, or call GitHub."""

from __future__ import annotations

from typing import Any

from ..models import HARD_STOP_REASONS, SearchRequest
from ..search import public_candidate
from ..storage import Store

MAX_GRAPH_REPOS = 12
MAX_EXTRA_RECALLED = 8
ADVISORY_SIGNALS = ("no_new_mechanism", "no_boundary_gain", "directions_covered")


def _key(value: str) -> str:
    return str(value or "").strip().casefold()


def _terms(concepts: list[Any]) -> list[dict[str, Any]]:
    result = []
    for concept in concepts:
        result.append({
            "term": concept.term,
            "aliases": list(concept.aliases or []),
            "weight": concept.weight,
        })
    return result


def _parse_request(raw: dict[str, Any]) -> SearchRequest:
    try:
        return SearchRequest.from_dict(raw)
    except Exception:
        text = str((raw or {}).get("request") or "legacy search")
        return SearchRequest.from_dict({"request": text, "problem_concepts": [text]})


def _status(*, incomplete: str | None, ranked: bool, iteration: int) -> str:
    if ranked:
        return "ranked"
    if incomplete:
        return "incomplete"
    if iteration > 0:
        return "iterating"
    return "searched"


def _snapshot_at(snapshots: list[dict[str, Any]], at: str | None) -> dict[str, Any] | None:
    if not snapshots:
        return None
    token = (at or "final").strip().lower()
    if token in {"final", "rank", "latest"}:
        return snapshots[-1]
    if token in {"initial", "search", "iteration-0", "iteration:0"}:
        for item in snapshots:
            if item.get("stage") == "search" or int(item.get("iteration") or 0) == 0:
                return item
        return snapshots[0]
    number = None
    for prefix in ("iteration-", "iteration:", "iter-"):
        if token.startswith(prefix):
            try:
                number = int(token[len(prefix):])
            except ValueError:
                number = None
            break
    if number is None:
        return snapshots[-1]
    match = None
    for item in snapshots:
        if item.get("stage") == "rank":
            continue
        if int(item.get("iteration") or 0) == number:
            match = item
    return match or snapshots[-1]


def _first_seen(snapshots: list[dict[str, Any]], field: str) -> dict[str, int]:
    seen: dict[str, int] = {}
    for item in snapshots:
        iteration = int(item.get("iteration") or 0)
        for raw in (item.get("boundary") or {}).get(field) or []:
            key = _key(str(raw))
            if key and key not in seen:
                seen[key] = iteration
    return seen


def _union_terms(snapshots: list[dict[str, Any]], field: str) -> set[str]:
    values: set[str] = set()
    for item in snapshots:
        for raw in (item.get("boundary") or {}).get(field) or []:
            key = _key(str(raw))
            if key:
                values.add(key)
    return values


def _promoted_keys(snapshots: list[dict[str, Any]]) -> set[str]:
    promoted: set[str] = set()
    for item in snapshots:
        hyp = item.get("hypothesis") or {}
        if not isinstance(hyp, dict):
            continue
        for raw in hyp.get("promote_discovered_terms") or []:
            key = _key(str(raw))
            if key:
                promoted.add(key)
        for addition in hyp.get("add_exploration_directions") or []:
            term = addition.get("term") if isinstance(addition, dict) else addition
            key = _key(str(term or ""))
            if key:
                promoted.add(key)
    return promoted


def _origin_for(
    name: str,
    *,
    requested: set[str],
    exploration: set[str],
    requested_origin: set[str],
    exploration_origin: set[str],
    discovered: set[str],
    promoted: set[str],
    first_iteration: int,
) -> str:
    key = _key(name)
    if key in requested or key in requested_origin:
        return "requested_mechanism"
    if key in exploration_origin or key in exploration:
        return "exploration_direction"
    if key in promoted:
        return "iteration_promotion"
    if key in discovered:
        return "discovered_term"
    if first_iteration > 0:
        return "iteration_promotion"
    return "requested_mechanism"


def _states_for(
    name: str,
    *,
    requested: set[str],
    recalled: set[str],
    presented: set[str],
    unexplored: set[str],
    rejected: set[str],
    negative: set[str],
) -> list[str]:
    key = _key(name)
    states: list[str] = []
    if key in requested:
        states.append("requested")
    if key in recalled and key not in requested:
        states.append("discovered")
    if key in presented:
        states.append("presented")
    if key in unexplored:
        states.append("unexplored")
    if key in rejected:
        states.append("rejected")
    if key in negative:
        states.append("negative")
    return states


def _concept_keys(concepts: list[Any]) -> set[str]:
    keys: set[str] = set()
    for concept in concepts:
        for term in concept.terms():
            key = _key(term)
            if key:
                keys.add(key)
    return keys


def _bucket_of(repo: str, buckets: dict[str, list]) -> str | None:
    needle = _key(repo)
    for name in ("popular", "gems", "adjacent"):
        for item in buckets.get(name) or []:
            if _key(item.get("repo") or "") == needle:
                return name
    return None


def _public_mechanisms(raw: Any) -> list[dict[str, Any]]:
    result = []
    for item in raw or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        result.append({
            "name": name,
            "role": item.get("role"),
            "matched_terms": list(item.get("matched_terms") or [])[:6],
            "sources": list(item.get("sources") or [])[:4],
            "evidence_ids": list(item.get("evidence_ids") or [])[:4],
        })
    return result


def _public_ranked_item(item: dict[str, Any], *, bucket: str | None, debug: bool) -> dict[str, Any]:
    assessment = item.get("assessment") or {}
    payload = {
        "repo": item.get("repo"),
        "url": item.get("url"),
        "description": item.get("description"),
        "stars": item.get("stars"),
        "topics": list(item.get("topics") or [])[:8],
        "bucket": bucket,
        "boundary_role": item.get("boundary_role"),
        "new_mechanisms": list(item.get("new_mechanisms") or []),
        "why_different": item.get("why_different") or "",
        "artifact_type": assessment.get("artifact_type"),
        "mechanisms": _public_mechanisms(item.get("mechanisms")),
        "relevance": assessment.get("relevance"),
        "transferability": (
            item.get("transferability")
            if item.get("transferability") is not None
            else assessment.get("transferability")
        ),
        "boundary_value": assessment.get("boundary_value"),
        "inspiration_score": item.get("inspiration_score"),
        "use_case": assessment.get("use_case"),
        "difficulty": assessment.get("difficulty"),
        "category": assessment.get("category"),
    }
    if debug:
        scores = item.get("scores") or {}
        payload["scores"] = {
            "popular": scores.get("popular"),
            "gem": scores.get("gem"),
            "adjacent": scores.get("adjacent"),
            "components": scores.get("components") or {},
        }
    return payload


def _visible_repos(
    ranking: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if ranking:
        by_repo = {}
        buckets = ranking.get("buckets") or {}
        for bucket_name, items in buckets.items():
            for item in items:
                repo = str(item.get("repo") or "")
                if repo:
                    by_repo[_key(repo)] = {**item, "_bucket": bucket_name}
        ordered = []
        for name in ranking.get("display_order") or []:
            item = by_repo.get(_key(name))
            if item:
                ordered.append(item)
        return ordered[:MAX_GRAPH_REPOS]
    selected = [item for item in candidates if item.get("selected_for_assessment")]
    selected.sort(key=lambda item: str(item.get("full_name") or "").lower())
    return [
        {
            "repo": item.get("full_name"),
            "url": item.get("html_url"),
            "description": item.get("description"),
            "stars": item.get("stargazers_count"),
            "topics": item.get("topics") or [],
            "mechanisms": item.get("mechanisms") or [],
            "boundary_role": None,
            "_bucket": None,
        }
        for item in selected[:MAX_GRAPH_REPOS]
    ]


def _query_executed(summary: dict[str, Any] | None) -> int:
    if not summary:
        return 0
    if "executed_count" in summary:
        try:
            return int(summary["executed_count"])
        except (TypeError, ValueError):
            return 0
    executed = summary.get("executed") or []
    return len(executed) if isinstance(executed, list) else 0


def _advisory_from_delta(delta: dict[str, Any], boundary: dict[str, Any], executed: int) -> list[str]:
    signals: list[str] = []
    if executed and not (delta.get("new_mechanisms") or []):
        signals.append("no_new_mechanism")
    if executed and not any(delta.get(name) for name in (
        "new_mechanisms", "new_presented_mechanisms", "new_directions", "new_terms",
    )):
        signals.append("no_boundary_gain")
    if not (boundary.get("unexplored_directions") or []):
        signals.append("directions_covered")
    return [name for name in ADVISORY_SIGNALS if name in signals]


class ExplorerReadModel:
    def __init__(self, *, data_dir: str | None = None) -> None:
        self.data_dir = data_dir

    def _store(self) -> Store:
        return Store(self.data_dir)

    def list_searches(self) -> dict[str, Any]:
        store = self._store()
        try:
            items = []
            for row in store.list_search_index():
                request = _parse_request(row["request"])
                state = row.get("session_state") or {}
                iteration = int(state.get("iteration") or 0)
                snapshot = store.latest_boundary_snapshot(row["id"])
                boundary = (snapshot or {}).get("boundary") or {}
                ranking = store.get_ranking(row["id"]) if row["ranked"] else None
                result_count = len((ranking or {}).get("display_order") or [])
                items.append({
                    "search_id": row["id"],
                    "request": request.request,
                    "mode": row["mode"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "iteration": iteration,
                    "status": _status(
                        incomplete=row.get("incomplete_phase"),
                        ranked=bool(row["ranked"]),
                        iteration=iteration,
                    ),
                    "mechanism_count": len(boundary.get("recalled_mechanisms") or []),
                    "result_count": result_count,
                    "stale": row["stale"],
                    "incomplete_phase": row.get("incomplete_phase"),
                })
            return {"searches": items, "count": len(items)}
        finally:
            store.close()

    def search_summary(self, search_id: str, *, debug: bool = False) -> dict[str, Any]:
        store = self._store()
        try:
            session = store.load_search(search_id)
            request = _parse_request(session["request"])
            state = store.get_session_state(search_id)
            ranking = store.get_ranking(search_id)
            snapshot = store.latest_boundary_snapshot(search_id)
            boundary = (snapshot or {}).get("boundary") or {}
            iteration = int(state.get("iteration") or 0)
            ranked = ranking is not None
            remaining = {
                "iterations": max(0, int(state["max_iterations"]) - iteration)
                if "max_iterations" in state else None,
                "queries": None,
            }
            if "session_query_budget" in state:
                remaining["queries"] = max(
                    0, int(state["session_query_budget"]) - store.query_count(search_id),
                )
            hard = str(state.get("stop_reason") or "") in HARD_STOP_REASONS
            can_iterate = (
                str(session.get("mode") or "") == "deep"
                and int(remaining["iterations"] or 0) > 0
                and int(remaining["queries"] if remaining["queries"] is not None else 1) > 0
                and not hard
            )
            if ranked:
                next_action = "done"
            elif str(session.get("mode") or "") != "deep" or not can_iterate:
                next_action = "rank"
            else:
                next_action = "iterate"
            payload = {
                "search_id": search_id,
                "request": request.request,
                "problem_concepts": _terms(request.problem_concepts),
                "mechanisms": _terms(request.mechanisms),
                "exploration_directions": _terms(request.exploration_directions),
                "mode": session.get("mode"),
                "status": _status(
                    incomplete=session.get("incomplete_phase"),
                    ranked=ranked,
                    iteration=iteration,
                ),
                "created_at": session.get("created_at"),
                "updated_at": session.get("updated_at"),
                "iteration": iteration,
                "stale": bool(session.get("stale")),
                "incomplete_phase": session.get("incomplete_phase"),
                "mechanism_count": len(boundary.get("recalled_mechanisms") or []),
                "presented_mechanism_count": len(boundary.get("presented_mechanisms") or []),
                "result_count": len((ranking or {}).get("display_order") or []),
                "next_action": next_action,
                "can_iterate": can_iterate,
            }
            if debug:
                payload["candidate_count"] = len(session.get("candidates") or [])
                payload["remaining_budget"] = remaining
                payload["stop_reason"] = state.get("stop_reason")
            return payload
        finally:
            store.close()

    def boundary_view(self, search_id: str, *, at: str | None = None, debug: bool = False) -> dict[str, Any]:
        store = self._store()
        try:
            session = store.load_search(search_id)
            request = _parse_request(session["request"])
            snapshots = store.boundary_snapshots(search_id)
            snapshot = _snapshot_at(snapshots, at)
            ranking = store.get_ranking(search_id)
            boundary = (snapshot or {}).get("boundary") or {}
            delta = (snapshot or {}).get("boundary_delta") or {}
            candidates = session.get("candidates") or []
            requested = _concept_keys(request.mechanisms)
            exploration = _concept_keys(request.exploration_directions)
            origins = boundary.get("mechanism_origins") or {}
            requested_origin = {_key(name) for name in origins.get("requested_mechanisms") or []}
            exploration_origin = {
                _key(name) for name in origins.get("confirmed_exploration_directions") or []
            }
            recalled = [_ for _ in (boundary.get("recalled_mechanisms") or []) if str(_).strip()]
            presented = [_ for _ in (boundary.get("presented_mechanisms") or []) if str(_).strip()]
            unexplored = [_ for _ in (boundary.get("unexplored_directions") or []) if str(_).strip()]
            rejected = [_ for _ in (boundary.get("rejected_directions") or []) if str(_).strip()]
            negative = [_ for _ in (boundary.get("negative_directions") or []) if str(_).strip()]
            discovered_terms = [_ for _ in (boundary.get("discovered_terms") or []) if str(_).strip()]
            recalled_keys = {_key(name) for name in recalled}
            presented_keys = {_key(name) for name in presented}
            unexplored_keys = {_key(name) for name in unexplored}
            rejected_keys = {_key(name) for name in rejected}
            negative_keys = {_key(name) for name in negative}
            first_recalled = _first_seen(snapshots, "recalled_mechanisms")
            all_discovered = _union_terms(snapshots, "discovered_terms")
            promoted = _promoted_keys(snapshots)

            canonical: dict[str, str] = {}
            for name in (
                [concept.term for concept in request.mechanisms]
                + recalled + presented + unexplored + rejected + negative
            ):
                key = _key(name)
                if key:
                    canonical.setdefault(key, str(name).strip())

            presented_set = presented_keys
            important_recalled = []
            extra_recalled = []
            for name in recalled:
                key = _key(name)
                if key in presented_set or key in requested or key in requested_origin:
                    important_recalled.append(name)
                else:
                    extra_recalled.append(name)
            visible_extra = extra_recalled[:MAX_EXTRA_RECALLED]
            hidden_extra = extra_recalled[MAX_EXTRA_RECALLED:]

            visible_repos = _visible_repos(ranking, candidates)
            repo_by_mechanism: dict[str, list[str]] = {}
            for repo_item in visible_repos:
                repo_name = str(repo_item.get("repo") or "")
                for mechanism in repo_item.get("mechanisms") or []:
                    key = _key(mechanism.get("name") if isinstance(mechanism, dict) else mechanism)
                    if key:
                        repo_by_mechanism.setdefault(key, []).append(repo_name)

            mechanism_nodes = []
            for key, name in canonical.items():
                first = first_recalled.get(key, 0)
                states = _states_for(
                    name, requested=requested, recalled=recalled_keys, presented=presented_keys,
                    unexplored=unexplored_keys, rejected=rejected_keys, negative=negative_keys,
                )
                if not states:
                    continue
                origin = _origin_for(
                    name, requested=requested, exploration=exploration,
                    requested_origin=requested_origin, exploration_origin=exploration_origin,
                    discovered=all_discovered, promoted=promoted, first_iteration=first,
                )
                default_visible = (
                    key in presented_keys
                    or key in requested
                    or key in unexplored_keys
                    or key in rejected_keys
                    or key in negative_keys
                    or name in visible_extra
                    or name in important_recalled
                )
                repos = repo_by_mechanism.get(key) or []
                evidence = []
                for repo_item in visible_repos:
                    if str(repo_item.get("repo") or "") not in repos:
                        continue
                    for match in repo_item.get("mechanisms") or []:
                        if isinstance(match, dict) and _key(match.get("name") or "") == key:
                            evidence.append({
                                "repo": repo_item.get("repo"),
                                "sources": list(match.get("sources") or []),
                                "matched_terms": list(match.get("matched_terms") or []),
                            })
                mechanism_nodes.append({
                    "id": f"mechanism:{key}",
                    "kind": "mechanism",
                    "name": name,
                    "states": states,
                    "origin": origin,
                    "first_iteration": first,
                    "repo_count": len(repos),
                    "repos": repos,
                    "evidence": evidence,
                    "confirmed": key in recalled_keys or key in presented_keys,
                    "default_visible": default_visible,
                })

            graph_nodes: list[dict[str, Any]] = []
            graph_edges: list[dict[str, Any]] = []
            for concept in request.problem_concepts:
                node_id = f"problem:{_key(concept.term)}"
                graph_nodes.append({
                    "id": node_id, "kind": "problem", "label": concept.term,
                    "default_visible": True,
                })
            for concept in request.exploration_directions:
                node_id = f"direction:{_key(concept.term)}"
                graph_nodes.append({
                    "id": node_id, "kind": "direction", "label": concept.term,
                    "default_visible": True,
                    "states": ["unexplored"] if _key(concept.term) in unexplored_keys else [],
                })
            for node in mechanism_nodes:
                graph_nodes.append({
                    "id": node["id"], "kind": "mechanism", "label": node["name"],
                    "states": node["states"], "origin": node["origin"],
                    "default_visible": node["default_visible"],
                    "confirmed": node["confirmed"],
                })
                key = _key(node["name"])
                if node["origin"] == "requested_mechanism":
                    for concept in request.problem_concepts:
                        graph_edges.append({
                            "source": f"problem:{_key(concept.term)}",
                            "target": node["id"],
                            "kind": "problem_mechanism",
                        })
                elif node["origin"] == "exploration_direction":
                    match = next(
                        (concept for concept in request.exploration_directions if _key(concept.term) == key),
                        None,
                    )
                    if match:
                        graph_edges.append({
                            "source": f"direction:{_key(match.term)}",
                            "target": node["id"],
                            "kind": "problem_mechanism",
                        })
                    else:
                        for concept in request.problem_concepts:
                            graph_edges.append({
                                "source": f"problem:{_key(concept.term)}",
                                "target": node["id"],
                                "kind": "problem_mechanism",
                            })
                            break
                else:
                    for concept in request.problem_concepts:
                        graph_edges.append({
                            "source": f"problem:{_key(concept.term)}",
                            "target": node["id"],
                            "kind": "problem_mechanism",
                        })
                        break
                for repo_name in node["repos"]:
                    graph_edges.append({
                        "source": node["id"],
                        "target": f"repo:{_key(repo_name)}",
                        "kind": "mechanism_repo",
                    })
            for repo_item in visible_repos:
                repo_name = str(repo_item.get("repo") or "")
                if not repo_name:
                    continue
                graph_nodes.append({
                    "id": f"repo:{_key(repo_name)}",
                    "kind": "repository",
                    "label": repo_name,
                    "boundary_role": repo_item.get("boundary_role"),
                    "bucket": repo_item.get("_bucket"),
                    "default_visible": True,
                })

            payload = {
                "search_id": search_id,
                "at": at or "final",
                "stage": (snapshot or {}).get("stage"),
                "iteration": (snapshot or {}).get("iteration"),
                "problem_concepts": _terms(request.problem_concepts),
                "requested_mechanisms": _terms(request.mechanisms),
                "exploration_directions": _terms(request.exploration_directions),
                "overview": {
                    "recalled": recalled,
                    "presented": presented,
                    "unexplored": unexplored,
                    "rejected": rejected,
                    "negative": negative,
                    "discovered_terms": discovered_terms,
                },
                "mechanisms": mechanism_nodes,
                "delta": {
                    "new_mechanisms": list(delta.get("new_mechanisms") or []),
                    "new_presented_mechanisms": list(delta.get("new_presented_mechanisms") or []),
                    "new_directions": list(delta.get("new_directions") or []),
                    "new_terms": list(delta.get("new_terms") or []),
                },
                "graph": {
                    "nodes": graph_nodes,
                    "edges": graph_edges,
                },
                "hidden_recalled_count": len(hidden_extra),
            }
            if debug:
                payload["candidate_count"] = len(candidates)
                payload["graph_repo_count"] = len(visible_repos)
            return payload
        finally:
            store.close()

    def iteration_timeline(self, search_id: str, *, debug: bool = False) -> dict[str, Any]:
        store = self._store()
        try:
            snapshots = store.boundary_snapshots(search_id)
            iterations = store.list_iterations(search_id)
            by_iteration: dict[int, dict[str, Any]] = {}
            for row in iterations:
                by_iteration.setdefault(int(row["iteration"]), row)
            steps = []
            for snapshot in snapshots:
                stage = str(snapshot.get("stage") or "")
                iteration = int(snapshot.get("iteration") or 0)
                if stage == "search" or (stage not in {"iterate", "expand", "rank"} and iteration == 0 and not steps):
                    kind = "initial"
                elif stage == "rank":
                    kind = "rank"
                else:
                    kind = "iteration"
                delta = snapshot.get("boundary_delta") or {}
                boundary = snapshot.get("boundary") or {}
                summary = snapshot.get("query_summary") or {}
                executed = _query_executed(summary)
                hyp = snapshot.get("hypothesis") or {}
                iter_row = by_iteration.get(iteration) if kind == "iteration" else None
                stop_reason = (iter_row or {}).get("stop_reason") or None
                hard = [stop_reason] if stop_reason in HARD_STOP_REASONS else []
                signals = _advisory_from_delta(delta, boundary, executed)
                step = {
                    "id": f"{kind}-{iteration}-{snapshot.get('id')}",
                    "kind": kind,
                    "stage": stage,
                    "iteration": iteration,
                    "created_at": snapshot.get("created_at"),
                    "hypothesis": hyp or None,
                    "target_direction": (hyp or {}).get("target_direction") or None,
                    "target_mechanism": (hyp or {}).get("target_mechanism") or None,
                    "queries_executed": executed,
                    "new_mechanisms": list(delta.get("new_mechanisms") or []),
                    "new_presented_mechanisms": list(delta.get("new_presented_mechanisms") or []),
                    "new_directions": list(delta.get("new_directions") or []),
                    "new_terms": list(delta.get("new_terms") or []),
                    "boundary_gain": bool(
                        (delta.get("new_mechanisms") or [])
                        or (delta.get("new_directions") or [])
                        or (delta.get("new_terms") or [])
                    ),
                    "stop_reasons": hard,
                    "stop_signals": signals,
                }
                if debug:
                    step["query_summary"] = {
                        "executed_count": executed,
                        "skipped_count": (
                            int(summary.get("skipped_count") or 0)
                            if isinstance(summary, dict) else 0
                        ),
                    }
                steps.append(step)
            events = []
            for row in iterations:
                if row.get("event") in {"stop", "refuse"} or row.get("stop_reason") in HARD_STOP_REASONS:
                    events.append({
                        "event": row.get("event"),
                        "iteration": row.get("iteration"),
                        "stop_reason": row.get("stop_reason"),
                        "hard": row.get("stop_reason") in HARD_STOP_REASONS,
                    })
            payload = {
                "search_id": search_id,
                "steps": steps,
                "stop_events": events,
            }
            if debug:
                payload["query_history"] = store.query_history(search_id)
            return payload
        finally:
            store.close()

    def result_view(self, search_id: str, *, debug: bool = False) -> dict[str, Any]:
        store = self._store()
        try:
            ranking = store.get_ranking(search_id)
            if ranking is None:
                return {
                    "search_id": search_id,
                    "ranked": False,
                    "display_order": [],
                    "buckets": {"popular": [], "gems": [], "adjacent": []},
                    "items": [],
                    "boundary_summary": None,
                    "newly_presented_mechanisms": [],
                }
            buckets = ranking.get("buckets") or {}
            display_order = list(ranking.get("display_order") or [])
            items = []
            by_repo = {}
            for bucket_name, bucket_items in buckets.items():
                public_bucket = []
                for item in bucket_items:
                    public = _public_ranked_item(item, bucket=bucket_name, debug=debug)
                    public_bucket.append(public)
                    by_repo[_key(item.get("repo") or "")] = public
                buckets[bucket_name] = public_bucket
            for name in display_order:
                item = by_repo.get(_key(name))
                if item:
                    items.append(item)
            payload = {
                "search_id": search_id,
                "ranked": True,
                "display_order": display_order,
                "buckets": {
                    "popular": buckets.get("popular") or [],
                    "gems": buckets.get("gems") or [],
                    "adjacent": buckets.get("adjacent") or [],
                },
                "items": items,
                "boundary_summary": ranking.get("boundary_summary"),
                "newly_presented_mechanisms": list(ranking.get("newly_presented_mechanisms") or []),
                "next_action": ranking.get("next_action") or "done",
            }
            if debug:
                payload["selection_order"] = list(ranking.get("selection_order") or [])
                payload["coverage"] = ranking.get("coverage")
            return payload
        finally:
            store.close()

    def repo_detail(self, search_id: str, repo: str, *, debug: bool = False) -> dict[str, Any]:
        store = self._store()
        try:
            candidate = store.get_candidate(repo, search_id)
            if candidate is None:
                raise KeyError(f"repository not found in local snapshots: {repo}")
            ranking = store.get_ranking(search_id)
            ranked_item = None
            bucket = None
            if ranking:
                bucket = _bucket_of(repo, ranking.get("buckets") or {})
                for items in (ranking.get("buckets") or {}).values():
                    for item in items:
                        if _key(item.get("repo") or "") == _key(repo):
                            ranked_item = item
                            break
            public = public_candidate(candidate, detailed=True)
            if not debug:
                public.pop("selection_score_components", None)
                public.pop("discovery_paths", None)
            payload = {
                "search_id": search_id,
                "repo": public.get("full_name") or repo,
                "url": public.get("html_url"),
                "description": public.get("description"),
                "stars": public.get("stargazers_count"),
                "topics": public.get("topics") or [],
                "language": public.get("language"),
                "latest_release": public.get("latest_release"),
                "artifact_type": None,
                "mechanisms": _public_mechanisms(public.get("mechanisms") or (ranked_item or {}).get("mechanisms")),
                "boundary_role": (ranked_item or {}).get("boundary_role"),
                "bucket": bucket,
                "new_mechanisms": list((ranked_item or {}).get("new_mechanisms") or []),
                "why_different": (ranked_item or {}).get("why_different") or "",
                "assessment": None,
                "reasons": [],
                "risks": [],
                "evidence": public.get("evidence") or [],
            }
            if ranked_item:
                assessment = ranked_item.get("assessment") or {}
                payload["artifact_type"] = assessment.get("artifact_type")
                payload["assessment"] = {
                    "relevance": assessment.get("relevance"),
                    "uniqueness": assessment.get("uniqueness"),
                    "usability": assessment.get("usability"),
                    "difficulty": assessment.get("difficulty"),
                    "use_case": assessment.get("use_case"),
                    "category": assessment.get("category"),
                    "artifact_type": assessment.get("artifact_type"),
                    "mechanism": assessment.get("mechanism"),
                    "transferability": assessment.get("transferability"),
                    "boundary_value": assessment.get("boundary_value"),
                }
                payload["reasons"] = assessment.get("reasons") or []
                payload["risks"] = assessment.get("risks") or []
                payload["inspiration_score"] = ranked_item.get("inspiration_score")
                if not payload["evidence"]:
                    payload["evidence"] = ranked_item.get("evidence") or []
            if debug and ranked_item:
                payload["scores"] = ranked_item.get("scores")
            return payload
        finally:
            store.close()
