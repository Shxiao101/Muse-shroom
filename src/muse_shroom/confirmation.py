from __future__ import annotations

from typing import Any, Iterable

from .boundary import (
    PROMOTABLE_SPECIFICITIES, _canonical_token_key, _normalized, _token_overlap,
)


COMPLETED_STATUSES = {
    "confirmed", "rejected", "unresolved", "skipped_budget", "skipped_duplicate",
}


def _overlaps(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    return (
        left == right
        or _token_overlap(left, right) >= (2 / 3)
        or left_tokens <= right_tokens
        or right_tokens <= left_tokens
    )


def _evidence_repos(item: dict[str, Any]) -> set[str]:
    return {
        str(source.get("repo") or "").casefold()
        for source in item.get("discovery_evidence") or []
        if str(source.get("repo") or "").strip()
    }


def _same_repo_variant(left: dict[str, Any], right: dict[str, Any],
                       left_key: str, right_key: str) -> bool:
    """Merge adjectival surface variants only when evidence and core phrase match."""
    left_tokens = left_key.split()
    right_tokens = right_key.split()
    return (
        len(left_tokens) >= 3
        and len(right_tokens) >= 3
        and left_tokens[-2:] == right_tokens[-2:]
        and bool(_evidence_repos(left) & _evidence_repos(right))
    )


def _skipped_record(item: dict[str, Any], status: str, reason: str) -> dict[str, Any]:
    return {
        key: item.get(key) for key in (
            "candidate", "discovery_evidence", "novelty_score", "confirmability_score",
            "confirmation_priority_score", "confirmation_priority_reason",
            "mechanism_specificity", "specificity_tier",
        )
    } | {
        "confirmation_queries": [],
        "confirmation_evidence": [],
        "confirmation_status": status,
        "confirmation_reason": reason,
    }


def plan_confirmation_candidates(boundary: dict[str, Any],
                                 existing_records: Iterable[dict[str, Any]] = (),
                                 *, limit: int = 2,
                                 attempt_budget: int | None = None
                                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Order, deduplicate, and budget candidates without consulting Golden data."""
    existing = list(existing_records)
    completed = {
        _normalized(str(item.get("candidate") or ""))
        for item in existing
        if str(item.get("confirmation_status") or "") in COMPLETED_STATUSES
    }
    confirmed_keys = [
        _canonical_token_key(str(item.get("candidate") or ""))
        for item in existing
        if item.get("confirmation_status") == "confirmed"
    ]
    queue = [
        dict(item) for item in boundary.get("confirmation_queue") or []
        if _normalized(str(item.get("candidate") or "")) not in completed
        and not any(
            set(_canonical_token_key(str(item.get("candidate") or "")).split())
            <= set(previous.split())
            or set(previous.split())
            <= set(_canonical_token_key(str(item.get("candidate") or "")).split())
            for previous in confirmed_keys if previous
        )
    ]
    queue.sort(key=lambda item: (
        0 if (
            item.get("specificity_tier") == "mechanism"
            or item.get("mechanism_specificity") in PROMOTABLE_SPECIFICITIES
        ) else 1,
        -int(item.get("confirmation_priority_score") or 0),
        -int(item.get("confirmability_score") or 0),
        -int(item.get("novelty_score") or 0),
        str(item.get("candidate") or "").casefold(),
    ))
    unique: list[dict[str, Any]] = []
    seen = [
        (_canonical_token_key(str(item.get("candidate") or "")), item)
        for item in existing
        if item.get("confirmation_status") == "confirmed"
        and _canonical_token_key(str(item.get("candidate") or ""))
    ]
    skipped: list[dict[str, Any]] = []
    for item in queue:
        key = _canonical_token_key(str(item.get("candidate") or ""))
        if not key:
            continue
        if any(
            _overlaps(key, previous_key)
            or _same_repo_variant(item, previous_item, key, previous_key)
            for previous_key, previous_item in seen
        ):
            skipped.append(_skipped_record(
                item, "skipped_duplicate", "canonical_or_surface_overlap",
            ))
            continue
        unique.append(item)
        seen.append((key, item))
    available = max(0, limit)
    if attempt_budget is not None:
        available = min(available, max(0, attempt_budget))
    selected = unique[:available]
    skipped.extend(
        _skipped_record(item, "skipped_budget", "confirmation_candidate_budget")
        for item in unique[available:]
    )
    return selected, skipped


def pending_confirmation_candidates(boundary: dict[str, Any],
                                    existing_records: Iterable[dict[str, Any]] = (),
                                    *, limit: int = 3) -> list[dict[str, Any]]:
    selected, _ = plan_confirmation_candidates(
        boundary, existing_records, limit=limit, attempt_budget=limit,
    )
    return selected


def confirmation_query_stage_limit(candidate: dict[str, Any]) -> int:
    """Reserve the seed/relationship stage for high-confirmability candidates."""
    return 3 if (
        int(candidate.get("confirmation_priority_score") or 0) >= 75
        and int(candidate.get("confirmability_score") or 0) >= 70
    ) else 2


def evaluate_confirmation(queue_item: dict[str, Any], refreshed: dict[str, Any] | None,
                          queries: Iterable[dict[str, Any]], *,
                          failed: bool = False, final: bool = True) -> dict[str, Any]:
    """Require new independent, core-use-case evidence before confirmation."""
    candidate = str(queue_item.get("candidate") or "").strip()
    discovery_evidence = list(queue_item.get("discovery_evidence") or [])
    discovery_repos = {
        str(item.get("repo") or "").casefold()
        for item in discovery_evidence if str(item.get("repo") or "").strip()
    }
    executed_queries = [
        str(item.get("query") or "") for item in queries
        if str(item.get("query") or "").strip()
    ]
    refreshed_sources = list((refreshed or {}).get("sources") or [])
    confirmation_evidence = [
        dict(item) for item in refreshed_sources
        if item.get("retrieval_stage") == "confirmation"
        and str(item.get("repo") or "").casefold() not in discovery_repos
    ]
    strong = [
        item for item in confirmation_evidence
        if item.get("core_use_case")
        and item.get("request_anchored")
        and int(item.get("evidence_relevance_score") or 0) >= 50
    ]
    core_sources = [
        item for item in [*discovery_evidence, *confirmation_evidence]
        if item.get("core_use_case")
    ]
    core_repos = {
        str(item.get("repo") or "").casefold()
        for item in core_sources if str(item.get("repo") or "").strip()
    }
    multi_repo = (
        len(core_repos) >= 2
        and int((refreshed or {}).get("evidence_relevance_score") or 0) >= 50
        and (
            any(item.get("request_anchored") for item in confirmation_evidence)
            if confirmation_evidence
            else any(item.get("mechanism_anchored") for item in discovery_evidence)
        )
    )
    transfer_backed = (
        bool(confirmation_evidence)
        and len(core_repos) >= 2
        and any(item.get("core_use_case") for item in confirmation_evidence)
        and any(item.get("mechanism_anchored") for item in discovery_evidence)
    )
    if strong:
        status = "confirmed"
        reason = "new_independent_core_use_case"
    elif multi_repo:
        status = "confirmed"
        reason = "multi_repo_independent_support"
    elif transfer_backed:
        status = "confirmed"
        reason = "cross_domain_mechanism_transfer"
    elif failed:
        status = "unresolved"
        reason = "confirmation_search_failed"
    elif not executed_queries:
        status = "unresolved"
        reason = "confirmation_not_executed"
    elif not final:
        status = "unresolved"
        reason = "confirmation_evidence_insufficient"
    else:
        status = "rejected"
        reason = (
            "same_repo_repetition"
            if refreshed_sources and not confirmation_evidence
            else "no_independent_core_use_case_evidence"
        )
    return {
        "candidate": candidate,
        "discovery_evidence": discovery_evidence,
        "confirmation_queries": executed_queries,
        "confirmation_evidence": confirmation_evidence,
        "confirmation_status": status,
        "confirmation_reason": reason,
        **{
            key: queue_item.get(key) for key in (
                "novelty_score", "confirmability_score",
                "confirmation_priority_score", "confirmation_priority_reason",
                "mechanism_specificity", "specificity_tier",
            )
        },
    }


def confirmation_metrics(records: Iterable[dict[str, Any]]) -> dict[str, int | float]:
    items = list(records)
    attempted = [
        item for item in items
        if item.get("confirmation_status") not in {"skipped_budget", "skipped_duplicate"}
        and bool(item.get("confirmation_queries"))
    ]
    skipped = [
        item for item in items
        if item.get("confirmation_status") in {"skipped_budget", "skipped_duplicate"}
    ]
    confirmed = sum(item.get("confirmation_status") == "confirmed" for item in items)
    query_count = sum(len(item.get("confirmation_queries") or []) for item in items)
    return {
        "confirmation_candidates_total": len(items),
        "confirmation_candidates_attempted": len(attempted),
        "confirmation_candidates_skipped": len(skipped),
        "confirmation_skipped_count": len(skipped),
        "confirmation_budget_exhausted_count": sum(
            item.get("confirmation_status") == "skipped_budget" for item in items
        ),
        "confirmation_planned_count": len(items),
        "confirmation_executed_count": len(attempted),
        "confirmation_confirmed_count": confirmed,
        "confirmation_rejected_count": sum(
            item.get("confirmation_status") == "rejected" for item in items
        ),
        "confirmation_unresolved_count": sum(
            item.get("confirmation_status") == "unresolved" for item in items
        ),
        "confirmation_query_count": query_count,
        "confirmed_per_attempted_candidate": round(confirmed / max(1, len(attempted)), 3),
        "queries_per_confirmed_mechanism": round(query_count / max(1, confirmed), 3),
    }
