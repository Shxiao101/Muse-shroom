from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from .boundary import _normalized, mechanism_distribution
from .confirmation import confirmation_metrics
from .selection import concept_coverage
from .models import (
    ContractError,
    DEFAULT_CONSECUTIVE_NO_GAIN,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_QUERIES_PER_ITERATION,
    DEFAULT_README_ENRICH_PER_ITERATION,
    DEFAULT_SESSION_QUERY_BUDGET,
    Concept,
    SearchHypothesis,
    SearchRequest,
)


def default_session_state() -> dict[str, Any]:
    return {
        "iteration": 0,
        "negative_directions": [],
        "exploration_additions": [],
        "confirmed_directions": [],
        "confirmation_records": [],
        "relation_calls_used": 0,
        "consecutive_no_gain": 0,
        "stop_reason": None,
    }


def remaining_budget(*, iteration: int, queries_used: int, relation_calls_used: int,
                     max_iterations: int = DEFAULT_MAX_ITERATIONS,
                     queries_per_iteration: int = DEFAULT_QUERIES_PER_ITERATION,
                     session_query_budget: int = DEFAULT_SESSION_QUERY_BUDGET,
                     readme_enrich_per_iteration: int = DEFAULT_README_ENRICH_PER_ITERATION,
                     relation_budget: int = 40) -> dict[str, int]:
    queries_left = max(0, session_query_budget - queries_used)
    return {
        "iterations": max(0, max_iterations - iteration),
        "max_iterations": max_iterations,
        "queries": queries_left,
        "queries_this_round": min(queries_per_iteration, queries_left),
        "readme_enrich_this_round": readme_enrich_per_iteration,
        "relation_calls": max(0, relation_budget - relation_calls_used),
    }


def meaningful_gain(delta: dict[str, Any] | None,
                    previous_origins: dict[str, Any] | None = None,
                    current_origins: dict[str, Any] | None = None) -> bool:
    delta = delta or {}
    if any(delta.get(name) for name in ("new_mechanisms", "new_directions")):
        return True
    if delta.get("new_terms"):
        return True

    def origin_names(payload: dict[str, Any] | None) -> set[str]:
        names: set[str] = set()
        for values in (payload or {}).values():
            names.update(str(value).casefold() for value in (values or []) if str(value).strip())
        return names

    return bool(origin_names(current_origins) - origin_names(previous_origins))


def hard_stop_reason(*, iteration: int, queries_used: int, max_iterations: int,
                     session_query_budget: int, decision: str | None = None) -> str | None:
    if decision == "stop":
        return "agent_stop"
    if iteration >= max_iterations:
        return "max_iterations"
    if queries_used >= session_query_budget:
        return "query_budget_exhausted"
    return None


def iteration_stop_reasons(*, hard_reason: str | None, delta: dict[str, Any],
                           boundary: dict[str, Any], skipped_all: bool,
                           executed: bool, previous_origins: dict[str, Any] | None,
                           current_origins: dict[str, Any] | None,
                           consecutive_no_gain: int = 0,
                           consecutive_limit: int = DEFAULT_CONSECUTIVE_NO_GAIN
                           ) -> tuple[list[str], list[str]]:
    hard: list[str] = []
    signals: list[str] = []
    if hard_reason:
        hard.append(hard_reason)
    if skipped_all and not executed and hard_reason not in {
        "agent_stop", "max_iterations", "query_budget_exhausted", "consecutive_no_gain",
    }:
        if "duplicate_queries" not in hard:
            hard.append("duplicate_queries")
    if executed and not (delta.get("new_mechanisms") or []):
        signals.append("no_new_mechanism")
    if executed and not meaningful_gain(delta, previous_origins, current_origins):
        signals.append("no_boundary_gain")
    if not (boundary.get("unexplored_directions") or []):
        signals.append("directions_covered")
    if consecutive_no_gain >= consecutive_limit and "consecutive_no_gain" not in hard:
        hard.append("consecutive_no_gain")
    return list(dict.fromkeys(hard)), list(dict.fromkeys(signals))


def evidence_anchors(selected: Iterable[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in selected:
        repo = str(candidate.get("full_name") or "")
        for match in candidate.get("concept_matches") or []:
            term = str(match.get("matched_alias") or match.get("label") or "").strip()
            key = term.casefold()
            if not term or key in seen:
                continue
            seen.add(key)
            anchors.append({
                "term": term, "repo": repo,
                "source": str(match.get("source") or "concept"),
            })
            if len(anchors) >= limit:
                return anchors
        for mechanism in candidate.get("mechanisms") or []:
            for term in mechanism.get("matched_terms") or [mechanism.get("name")]:
                value = str(term or "").strip()
                key = value.casefold()
                if not value or key in seen:
                    continue
                seen.add(key)
                anchors.append({"term": value, "repo": repo, "source": "mechanism"})
                if len(anchors) >= limit:
                    return anchors
    return anchors


def ambiguity_signals(candidates: Iterable[dict[str, Any]], selected: Iterable[dict[str, Any]],
                      request: SearchRequest, boundary: dict[str, Any],
                      coverage: dict[str, Any]) -> list[dict[str, Any]]:
    items = list(candidates)
    shortlist = list(selected)
    signals: list[dict[str, Any]] = []
    problem_hits = sum(
        1 for item in items
        if concept_coverage(item, request.problem_concepts, include_readme="readme" in item) >= 40
    )
    mechanism_hits = sum(1 for item in items if item.get("mechanisms"))
    if problem_hits >= 3 and mechanism_hits <= max(1, problem_hits // 4):
        signals.append({
            "kind": "problem_without_mechanism",
            "problem_hits": problem_hits,
            "mechanism_hits": mechanism_hits,
        })

    known = {
        _normalized(term)
        for concept in (
            request.problem_concepts + request.mechanisms + request.exploration_directions
        )
        for term in concept.terms()
    }
    topic_counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    for item in items:
        for raw in item.get("topics") or []:
            value = " ".join(str(raw).replace("-", " ").split()).strip()
            key = _normalized(value)
            if not key or key in known:
                continue
            topic_counts[key] += 1
            display.setdefault(key, value)
    off_topic = [(key, count) for key, count in topic_counts.most_common(5) if count >= 2]
    for key, count in off_topic[:3]:
        signals.append({
            "kind": "repeated_offtopic_topic",
            "term": display[key],
            "count": count,
        })

    distribution = mechanism_distribution(items)
    if distribution and items:
        top, count = next(iter(distribution.items()))
        share = count / max(1, len(items))
        if share >= 0.5 and len(distribution) <= 2:
            signals.append({
                "kind": "mechanism_monopoly",
                "mechanism": top,
                "count": count,
                "share": round(share, 3),
            })

    if boundary.get("unexplored_directions"):
        signals.append({
            "kind": "unexplored_directions",
            "directions": list(boundary["unexplored_directions"])[:5],
        })

    if shortlist and int(coverage.get("queries_executed") or 0) >= 6:
        cores = [
            float((item.get("selection_score_components") or {}).get("core_concept") or 0)
            for item in shortlist
        ]
        mean = sum(cores) / max(1, len(cores))
        if mean < 25:
            signals.append({
                "kind": "low_shortlist_relevance",
                "mean_core_concept": round(mean, 1),
                "queries_executed": coverage.get("queries_executed"),
            })
    return signals[:6]


def build_observation(*, iteration: int, boundary: dict[str, Any],
                      boundary_delta: dict[str, Any], coverage: dict[str, Any],
                      query_summary: dict[str, Any], candidates: Iterable[dict[str, Any]],
                      selected: Iterable[dict[str, Any]], request: SearchRequest,
                      remaining: dict[str, int], stop_reasons: list[str],
                      hard_stop: bool, exploration_additions: list[dict[str, Any]] | None = None,
                      stop_signals: list[str] | None = None,
                      consecutive_no_gain: int = 0) -> dict[str, Any]:
    candidate_list = list(candidates)
    selected_list = list(selected)
    distribution = mechanism_distribution(candidate_list)
    assignments = sum(distribution.values())
    presented = len(boundary.get("presented_mechanisms") or [])
    return {
        "iteration": iteration,
        "boundary": {
            "recalled_mechanisms": list(boundary.get("recalled_mechanisms") or []),
            "presented_mechanisms": list(boundary.get("presented_mechanisms") or []),
            "explored_directions": list(boundary.get("explored_directions") or []),
            "unexplored_directions": list(boundary.get("unexplored_directions") or []),
            "discovered_terms": list(boundary.get("discovered_terms") or []),
            "discovered_term_evidence": list(
                boundary.get("discovered_term_evidence") or []
            ),
            "confirmation_queue": list(boundary.get("confirmation_queue") or []),
            "mechanism_confirmations": list(boundary.get("mechanism_confirmations") or []),
            "negative_directions": list(boundary.get("negative_directions") or []),
            "rejected_directions": list(boundary.get("rejected_directions") or []),
            "mechanism_origins": dict(boundary.get("mechanism_origins") or {}),
        },
        "boundary_delta": dict(boundary_delta or {}),
        "coverage": {
            "queries_executed": coverage.get("queries_executed"),
            "mechanism_count": coverage.get("mechanism_count"),
            "presented_mechanism_count": coverage.get("presented_mechanism_count"),
            "direction_coverage": coverage.get("direction_coverage"),
            "core_concepts_covered": coverage.get("core_concepts_covered"),
            "uncovered_core_concepts": coverage.get("uncovered_core_concepts"),
        },
        "query_summary": query_summary,
        "mechanism_distribution": distribution,
        "mechanism_redundancy": round(
            max(0, assignments - presented) / max(1, assignments), 3
        ),
        "unexplored_directions": list(boundary.get("unexplored_directions") or []),
        "discovered_terms": list(boundary.get("discovered_terms") or []),
        "discovered_term_evidence": list(
            boundary.get("discovered_term_evidence") or []
        ),
        "confirmation_queue": list(boundary.get("confirmation_queue") or []),
        "mechanism_confirmations": list(boundary.get("mechanism_confirmations") or []),
        "exploration_additions": list(exploration_additions or []),
        "ambiguity_signals": ambiguity_signals(
            candidate_list, selected_list, request, boundary, coverage
        ),
        "anchors": evidence_anchors(selected_list),
        "remaining_budget": remaining,
        "stop": {
            "should_stop": hard_stop or bool(stop_reasons),
            "hard": hard_stop or bool(stop_reasons),
            "reasons": list(stop_reasons),
            "signals": list(stop_signals or []),
            "consecutive_no_gain": consecutive_no_gain,
        },
    }


def merge_unique(existing: Iterable[str], incoming: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in (*existing, *incoming):
        text = str(value).strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def validate_hypothesis_evidence(hypothesis: SearchHypothesis, request: SearchRequest,
                                 boundary: dict[str, Any],
                                 candidates: Iterable[dict[str, Any]]) -> None:
    """Reject new high-priority directions that cannot be traced to evidence."""
    if hypothesis.decision != "continue":
        return
    discovered = {
        str(item.get("term") or "").casefold(): item
        for item in boundary.get("discovered_term_evidence") or []
        if str(item.get("term") or "").strip()
    }
    discovered_names = {
        str(term).casefold() for term in boundary.get("discovered_terms") or []
        if str(term).strip()
    }
    promotable_names = {
        str(item.get("term") or "").casefold()
        for item in boundary.get("discovered_term_evidence") or []
        if str(item.get("term") or "").strip() and item.get("promotable") is not False
    }
    evidence_ids = {
        str(item.get("id"))
        for candidate in candidates
        for item in candidate.get("evidence") or []
        if str(item.get("id") or "").strip()
    }
    requested_directions = {
        concept.term.casefold() for concept in request.exploration_directions
    }
    addition_terms: set[str] = set()
    for addition in hypothesis.add_exploration_directions:
        key = addition.term.casefold()
        addition_terms.add(key)
        if key in requested_directions:
            continue
        if addition.evidence == "user_request":
            continue
        if addition.evidence == "discovered_term" and key in promotable_names:
            continue
        if addition.evidence in evidence_ids:
            continue
        raise ContractError(
            f"new exploration direction {addition.term!r} requires evidence: "
            "use discovered_term for an observed term, an evidence ID, or user_request"
        )
    for term in hypothesis.promote_discovered_terms:
        key = term.casefold()
        if key not in discovered or key not in discovered_names or key not in promotable_names:
            raise ContractError(
                f"promote_discovered_terms entry {term!r} is not a promotable, "
                "evidence-backed term from the latest observation"
            )
    if hypothesis.target_direction:
        key = hypothesis.target_direction.casefold()
        allowed = (
            requested_directions | promotable_names | addition_terms
            | {term.casefold() for term in hypothesis.promote_discovered_terms}
        )
        if key not in allowed:
            raise ContractError(
                f"target_direction {hypothesis.target_direction!r} must come from an "
                "existing direction or evidence-backed discovered term"
            )


def apply_hypothesis_to_request(request: SearchRequest, hypothesis: SearchHypothesis,
                                *, iteration: int,
                                existing_additions: list[dict[str, Any]] | None = None
                                ) -> tuple[SearchRequest, list[dict[str, Any]]]:
    additions = list(existing_additions or [])
    known = {concept.term.casefold() for concept in request.exploration_directions}
    known.update(str(item.get("term") or "").casefold() for item in additions)
    request.exploration_directions = list(request.exploration_directions)

    def add_direction(term: str, reason: str, evidence: str,
                      source_iteration: int | None = None) -> None:
        if not term or term.casefold() in known:
            return
        additions.append({
            "term": term, "reason": reason, "evidence": evidence,
            "source_iteration": source_iteration if source_iteration is not None else iteration,
        })
        known.add(term.casefold())
        request.exploration_directions.append(Concept.from_value(term))

    for term in hypothesis.promote_discovered_terms:
        add_direction(term, hypothesis.reason, "discovered_term")
    for addition in hypothesis.add_exploration_directions:
        add_direction(
            addition.term,
            addition.reason or hypothesis.reason,
            addition.evidence,
            addition.source_iteration,
        )
    if hypothesis.exclude:
        request.exclusions = merge_unique(request.exclusions, hypothesis.exclude)
    return request, additions


def session_loop_diagnostics(store: Any, search_id: str) -> dict[str, Any]:
    snapshots = store.boundary_snapshots(search_id)
    history = store.query_history(search_id) if hasattr(store, "query_history") else []
    iterations = store.list_iterations(search_id) if hasattr(store, "list_iterations") else []
    iterate_snaps = [item for item in snapshots if item.get("stage") in {"iterate", "expand"}]
    normal_history = [
        item for item in history
        if not str(item.get("kind") or "").startswith("confirmation_")
    ]
    executed = [item for item in normal_history if not item.get("skipped")]
    duplicates = [
        item for item in normal_history
        if item.get("skip_reason") == "duplicate" or (
            item.get("skipped") and not item.get("skip_reason")
        )
    ]
    skipped_by_reason: dict[str, int] = {}
    for item in normal_history:
        reason = str(item.get("skip_reason") or "")
        if item.get("skipped") and reason:
            skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + 1
    per_iteration: list[dict[str, Any]] = []
    for snapshot in iterate_snaps:
        delta = snapshot.get("boundary_delta") or {}
        per_iteration.append({
            "iteration": snapshot.get("iteration"),
            "stage": snapshot.get("stage"),
            "new_mechanisms": list(delta.get("new_mechanisms") or []),
            "boundary_gain": len(delta.get("new_mechanisms") or []),
            "new_directions": list(delta.get("new_directions") or []),
        })
    latest = snapshots[-1]["boundary"] if snapshots else {}
    novelty: dict[int, int] = {}
    try:
        session = store.load_search(search_id)
        for candidate in session.get("candidates") or []:
            seen = int(candidate.get("first_seen_iteration") or 0)
            novelty[seen] = novelty.get(seen, 0) + 1
    except (KeyError, TypeError):
        novelty = {}
    max_seen = max(novelty) if novelty else 0
    executed_events = [
        item for item in iterations
        if item.get("event") in {None, "iterate", "expand", "search"}
    ]
    planned_events = [
        item for item in iterations
        if item.get("event") in {None, "iterate", "expand"}
    ]
    executed_iteration_events = [
        item for item in planned_events
        if int((item.get("query_summary") or {}).get("executed_count") or 0) > 0
    ]
    retrieval_changes: list[bool] = []
    previous_pool: set[str] = set()
    for snapshot in snapshots:
        pool = {
            str(repo).casefold()
            for repo in ((snapshot.get("visible_repos") or {}).get("pool_repos") or [])
            if str(repo).strip()
        }
        if snapshot.get("stage") in {"iterate", "expand"}:
            retrieval_changes.append(bool(pool - previous_pool))
        if pool:
            previous_pool = pool
    state = store.get_session_state(search_id) if hasattr(store, "get_session_state") else {}
    return {
        "iterations_used": max(
            (item.get("iteration") or 0) for item in executed_events
        ) if executed_events else 0,
        "planned_iteration_count": len(planned_events),
        "executed_iteration_count": len(executed_iteration_events),
        "retrieval_changing_iteration_count": sum(retrieval_changes),
        "queries_per_iteration": _counts_by_iteration(executed),
        "new_mechanisms_per_iteration": [
            len(item["new_mechanisms"]) for item in per_iteration
        ],
        "boundary_gain_per_iteration": [item["boundary_gain"] for item in per_iteration],
        "duplicate_query_rate": round(len(duplicates) / max(1, len(normal_history)), 3),
        "skipped_by_reason": skipped_by_reason,
        "candidate_novelty_per_iteration": [novelty.get(index, 0) for index in range(max_seen + 1)],
        "stop_reason": next(
            (item.get("stop_reason") for item in reversed(iterations) if item.get("stop_reason")),
            None,
        ),
        "unexplored_directions_at_stop": list(latest.get("unexplored_directions") or []),
        "mode": "agentic-loop" if iterate_snaps else "single-pass",
        "boundary_trace": boundary_trace(store, search_id),
        **confirmation_metrics(state.get("confirmation_records") or []),
    }


def boundary_trace(store: Any, search_id: str) -> list[dict[str, Any]]:
    """Build a compact, evaluation/debug trace without changing user output."""
    snapshots = store.boundary_snapshots(search_id)
    trace: list[dict[str, Any]] = []
    evidence_by_term: dict[str, dict[str, Any]] = {}
    session = store.load_search(search_id)
    evidence_by_id = {
        str(evidence.get("id")): evidence
        for candidate in session.get("candidates") or []
        for evidence in candidate.get("evidence") or []
        if str(evidence.get("id") or "").strip()
    }
    seen_confirmations: set[tuple[str, str]] = set()
    for snapshot in snapshots:
        summary = snapshot.get("query_summary") or {}
        delta = snapshot.get("boundary_delta") or {}
        boundary = snapshot.get("boundary") or {}
        hypothesis = snapshot.get("hypothesis") or {}
        promoted = {
            str(term).casefold()
            for term in hypothesis.get("promote_discovered_terms") or []
        }
        evidence_sources = [
            evidence_by_term[key] for key in promoted if key in evidence_by_term
        ]
        confirmations = []
        for record in boundary.get("mechanism_confirmations") or []:
            identity = (
                str(record.get("candidate") or "").casefold(),
                str(record.get("confirmation_status") or ""),
            )
            if identity in seen_confirmations:
                continue
            seen_confirmations.add(identity)
            confirmations.append(record)
            if record.get("confirmation_status") == "confirmed":
                evidence_sources.append({
                    "term": record.get("candidate"),
                    "kind": "confirmation",
                    "sources": list(record.get("confirmation_evidence") or []),
                    "discovery_sources": list(record.get("discovery_evidence") or []),
                })
        for addition in hypothesis.get("add_exploration_directions") or []:
            if not isinstance(addition, dict):
                continue
            term = str(addition.get("term") or "")
            key = term.casefold()
            evidence_id = str(addition.get("evidence") or "")
            if evidence_id == "discovered_term" and key in evidence_by_term:
                evidence_sources.append(evidence_by_term[key])
            elif evidence_id == "user_request":
                evidence_sources.append({
                    "term": term, "kind": "user_request", "sources": [],
                })
            elif evidence_id in evidence_by_id:
                evidence = evidence_by_id[evidence_id]
                evidence_sources.append({
                    "term": term,
                    "kind": str(evidence.get("kind") or "candidate_evidence"),
                    "sources": [{
                        "evidence_id": evidence_id,
                        "source": evidence.get("source"),
                    }],
                })
        for evidence in boundary.get("discovered_term_evidence") or []:
            key = str(evidence.get("term") or "").casefold()
            if key:
                evidence_by_term.setdefault(key, evidence)
        trace.append({
            "iteration": int(snapshot.get("iteration") or 0),
            "stage": snapshot.get("stage"),
            "hypothesis": hypothesis or None,
            "evidence_sources": evidence_sources,
            "queries": [
                item.get("query") for item in summary.get("executed") or []
                if item.get("query")
            ],
            "mechanisms_found": list(boundary.get("recalled_mechanisms") or []),
            "directions_uncovered": list(delta.get("new_directions") or []),
            "new_mechanisms": list(delta.get("new_mechanisms") or []),
            "new_mechanism_surfaces": list(
                delta.get("new_mechanism_surfaces") or delta.get("new_mechanisms") or []
            ),
            "mechanism_normalizations": list(delta.get("mechanism_normalizations") or []),
            "boundary_gain": len(delta.get("new_mechanisms") or []),
            "confirmations": confirmations,
        })
    return trace


def _counts_by_iteration(rows: list[dict[str, Any]]) -> list[int]:
    counts: dict[int, int] = {}
    for row in rows:
        iteration = int(row.get("iteration") or 0)
        counts[iteration] = counts.get(iteration, 0) + 1
    if not counts:
        return []
    return [counts.get(index, 0) for index in range(max(counts) + 1)]
