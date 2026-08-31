from __future__ import annotations

from typing import Any, Iterable

from .boundary import _canonical_token_key, _normalized, _token_overlap


SPECIFICITY_BONUS = {
    "workflow_pattern": 35,
    "intervention": 20,
    "behavioral_signal": 5,
    "mechanism": 10,
    "project_category": 0,
}


def pending_confirmation_candidates(boundary: dict[str, Any],
                                    existing_records: Iterable[dict[str, Any]] = (),
                                    *, limit: int = 3) -> list[dict[str, Any]]:
    """Select a bounded, non-synonymous queue without consulting Golden data."""
    completed = {
        _normalized(str(item.get("candidate") or ""))
        for item in existing_records
        if str(item.get("confirmation_status") or "") in {
            "confirmed", "rejected", "unresolved",
        }
    }
    confirmed_keys = [
        _canonical_token_key(str(item.get("candidate") or ""))
        for item in existing_records
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
        -(
            int(item.get("evidence_relevance_score") or 0)
            + 2 * int(item.get("support_count") or 0)
            + SPECIFICITY_BONUS.get(str(item.get("mechanism_specificity") or ""), 0)
        ),
        str(item.get("candidate") or "").casefold(),
    ))
    selected: list[dict[str, Any]] = []
    selected_keys: list[str] = []
    for item in queue:
        key = _canonical_token_key(str(item.get("candidate") or ""))
        if not key:
            continue
        if any(
            key == previous
            or _token_overlap(key, previous) >= (2 / 3)
            or (
                f" {key} " in f" {previous} " or f" {previous} " in f" {key} "
            )
            for previous in selected_keys
        ):
            continue
        selected.append(item)
        selected_keys.append(key)
        if len(selected) >= limit:
            break
    return selected


def evaluate_confirmation(queue_item: dict[str, Any], refreshed: dict[str, Any] | None,
                          queries: Iterable[dict[str, Any]], *,
                          failed: bool = False) -> dict[str, Any]:
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
    elif failed or not executed_queries:
        status = "unresolved"
        reason = "confirmation_not_executed" if not executed_queries else "confirmation_search_failed"
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
    }


def confirmation_metrics(records: Iterable[dict[str, Any]]) -> dict[str, int]:
    items = list(records)
    return {
        "confirmation_planned_count": len(items),
        "confirmation_executed_count": sum(
            bool(item.get("confirmation_queries")) for item in items
        ),
        "confirmation_confirmed_count": sum(
            item.get("confirmation_status") == "confirmed" for item in items
        ),
        "confirmation_rejected_count": sum(
            item.get("confirmation_status") == "rejected" for item in items
        ),
        "confirmation_unresolved_count": sum(
            item.get("confirmation_status") == "unresolved" for item in items
        ),
        "confirmation_query_count": sum(
            len(item.get("confirmation_queries") or []) for item in items
        ),
    }
