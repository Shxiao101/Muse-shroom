from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any, Iterable

from .boundary import boundary_delta as compute_boundary_delta
from .models import (
    ContractError, NO_RECOMMENDATION_FIELDS, RANK_PAYLOAD_FIELDS, Selection,
    reject_unknown_fields, require_single_line, repo_key,
)
from .sidecar import (
    derive_hypothesis_status, merge_candidate_view, public_hypothesis,
)
from .storage import Store


def _collapsed(value: Any) -> str:
    """Collapse whitespace runs only.

    README line wrapping is a rendering artifact, not content, and is the most likely
    cause of a spurious rejection when an Agent copies a quote across a wrap. Nothing
    else is normalised: case, punctuation and word forms must still match exactly, or
    the quote check would become another vocabulary gate.
    """
    return " ".join(str(value or "").split())


def _metadata_facts(candidate: dict[str, Any]) -> dict[str, Any]:
    for evidence in candidate.get("evidence") or []:
        if evidence.get("kind") == "github_metadata":
            facts = evidence.get("facts")
            return dict(facts) if isinstance(facts, dict) else {}
    return {}


def _license(candidate: dict[str, Any]) -> str | None:
    value = candidate.get("license")
    if isinstance(value, dict):
        value = value.get("spdx_id")
    if value is None:
        value = _metadata_facts(candidate).get("license")
    return str(value) if value else None


def _star_growth(store: Store, repo: str) -> dict[str, Any] | None:
    history = store.star_history(repo)
    if len(history) < 2:
        return None
    return {
        "from": history[0]["stars"],
        "to": history[-1]["stars"],
        "from_time": history[0]["captured_at"],
        "to_time": history[-1]["captured_at"],
    }


def _recorded_texts(
    candidate: dict[str, Any], evidence: dict[str, Any],
) -> list[tuple[str, str]]:
    """Return exact recorded source text paired with its repository SHA."""
    facts = evidence.get("facts")
    facts = facts if isinstance(facts, dict) else {}
    kind = str(evidence.get("kind") or "")
    recorded: list[tuple[str, str]] = []
    if kind == "readme":
        text = str(candidate.get("readme") or "")
        sha = str(facts.get("sha") or candidate.get("readme_sha") or "")
        if text and sha:
            recorded.append((text, sha))
    elif kind == "readme_excerpt":
        text = str(facts.get("text") or "")
        sha = str(facts.get("sha") or "")
        if text and sha:
            recorded.append((text, sha))
    elif kind == "mechanism_match":
        for match in facts.get("mechanisms") or []:
            if not isinstance(match, dict):
                continue
            text = str(match.get("text") or "")
            sha = str(match.get("sha") or "")
            if not sha and match.get("source_field") == "readme":
                sha = str(candidate.get("readme_sha") or "")
            if text and sha:
                recorded.append((text, sha))
    return recorded


def _verify_selection(
    selection: Selection, candidate: dict[str, Any],
) -> tuple[list[str], dict[str, Any] | None]:
    evidence_by_id = {
        str(item.get("id") or ""): item
        for item in candidate.get("evidence") or []
        if str(item.get("id") or "")
    }
    unknown = [value for value in selection.evidence_ids if value not in evidence_by_id]
    if unknown:
        return [f"evidence_not_owned:{value}" for value in unknown], None
    source_term = _collapsed(selection.source_term)
    quote = _collapsed(selection.quote)
    for evidence_id in selection.evidence_ids:
        for text, sha in _recorded_texts(candidate, evidence_by_id[evidence_id]):
            collapsed = _collapsed(text)
            if source_term in collapsed and quote in collapsed:
                return [], {"evidence_id": evidence_id, "sha": sha}
    return ["quote_not_verbatim_at_recorded_sha"], None


def candidate_source(candidate: dict[str, Any]) -> str:
    """host_supplied only when the host's own submission is the sole discovery path."""
    paths = [path for path in candidate.get("discovery_paths") or [] if isinstance(path, dict)]
    supplied = any(path.get("relation") == "host_supplied" for path in paths)
    recalled = any(
        path.get("kind") == "query"
        or (path.get("kind") == "relationship" and path.get("relation") != "host_supplied")
        for path in paths
    )
    return "host_supplied" if supplied and not recalled else "muse_recall"


def _raw_item(
    store: Store, candidate: dict[str, Any], selection: Selection,
    verification: dict[str, Any], new_mechanisms: list[str],
) -> dict[str, Any]:
    metadata = _metadata_facts(candidate)
    stars = int(candidate.get("stargazers_count", metadata.get("stars", 0)) or 0)
    forks = int(candidate.get("forks_count", metadata.get("forks", 0)) or 0)
    open_issues = int(
        candidate.get("open_issues_count", metadata.get("open_issues", 0)) or 0
    )
    return {
        **asdict(selection),
        "repo": candidate.get("full_name") or selection.repo,
        "url": candidate.get("html_url"),
        "description": candidate.get("description"),
        "stars": stars,
        "star_growth": _star_growth(store, selection.repo),
        "forks": forks,
        "open_issues": open_issues,
        "pushed_at": candidate.get("pushed_at", metadata.get("pushed_at")),
        "archived": bool(candidate.get("archived", metadata.get("archived", False))),
        "license": _license(candidate),
        "language": candidate.get("language"),
        "topics": list(candidate.get("topics") or metadata.get("topics") or []),
        "evidence": list(candidate.get("evidence") or []),
        "discovery_paths": list(candidate.get("discovery_paths") or []),
        "new_mechanisms": new_mechanisms,
        "verification": verification,
        "source": candidate_source(candidate),
    }


_EMPTY_SELECTION_HINT = (
    "empty selection requires no_recommendation.reason "
    "(a single-line string up to 500 characters). "
    'Submit {"selection": [], "no_recommendation": {"reason": "..."}} '
    "to record that no repository is worth recommending."
)


def _no_recommendation_reason(payload: dict[str, Any], *, strict: bool) -> str:
    raw = payload.get("no_recommendation")
    if raw is None:
        raise ContractError(_EMPTY_SELECTION_HINT)
    if not isinstance(raw, dict):
        raise ContractError("no_recommendation must be an object with reason")
    if strict:
        reject_unknown_fields(raw, NO_RECOMMENDATION_FIELDS, where="no_recommendation")
    if "reason" not in raw:
        raise ContractError("no_recommendation.reason is required when selection is empty")
    return require_single_line(raw["reason"], where="no_recommendation.reason", limit=500)


def _selection_payload(payload: Any, *, strict: bool) -> tuple[list[Selection], str | None]:
    extra = None
    if isinstance(payload, dict):
        if strict:
            reject_unknown_fields(payload, RANK_PAYLOAD_FIELDS, where="muse_rank payload")
        raw = payload.get("selection")
        extra = payload.get("no_recommendation")
    else:
        raw = payload
    if not isinstance(raw, list):
        raise ContractError("selection must be an ordered list")
    if extra is not None and raw:
        raise ContractError("no_recommendation is only valid with an empty selection")
    if not raw:
        if not isinstance(payload, dict):
            raise ContractError(_EMPTY_SELECTION_HINT)
        return [], _no_recommendation_reason(payload, strict=strict)
    parsed = [Selection.from_dict(item, strict=strict) for item in raw]
    repos = [item.repo for item in parsed]
    if len(repos) != len(set(repos)):
        raise ContractError("selection must not contain the same repository twice")
    return parsed, None


def _mark_sidecar_selection(
    records: list[dict[str, Any]], items: Iterable[dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
) -> None:
    for record in records:
        hypothesis_id = str(record.get("id") or "")
        record["validated"] = False
        record["selected"] = False
        record["presented"] = False
        record["assessment_repo"] = None
        for item in items:
            name = str(item.get("repo") or "").casefold()
            candidate = candidates.get(name) or {}
            cited = set(item.get("evidence_ids") or [])
            linked = False
            for evidence in candidate.get("evidence") or []:
                if str(evidence.get("id") or "") not in cited:
                    continue
                facts = evidence.get("facts") or {}
                linked = str(facts.get("hypothesis_id") or "") == hypothesis_id or any(
                    str(value.get("hypothesis_id") or "") == hypothesis_id
                    for value in facts.get("mechanisms") or [] if isinstance(value, dict)
                )
                if linked:
                    break
            if linked:
                record["validated"] = True
                record["selected"] = True
                record["presented"] = True
                record["assessment_repo"] = item.get("repo")
                break
        record["status"] = derive_hypothesis_status(record)


def _unique_labels(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        label = str(value).strip()
        key = label.casefold()
        if label and key not in seen:
            seen.add(key)
            result.append(label)
    return result


def rank_search(
    store: Store, search_id: str, selection_payload: Any, *, strict: bool = False,
) -> dict[str, Any]:
    """Validate and record an Agent-owned ordered repository selection."""
    session = store.load_search(search_id)
    session_state = store.get_session_state(search_id)
    sidecar_state = session_state.get("semantic_sidecar") or {}
    sidecar_records = list(sidecar_state.get("hypotheses") or [])

    by_name = {repo_key(item): item for item in session.get("candidates") or []}
    for candidate in sidecar_state.get("candidates") or []:
        key = repo_key(candidate)
        if not key:
            continue
        by_name[key] = merge_candidate_view(by_name[key], candidate) if key in by_name else candidate

    selections, no_recommendation_reason = _selection_payload(
        selection_payload, strict=strict,
    )
    previous_snapshot = store.latest_boundary_snapshot(
        search_id, ("search", "expand", "iterate")
    ) or {}
    boundary = deepcopy(previous_snapshot.get("boundary") or {})
    presented_before = _unique_labels(boundary.get("presented_mechanisms") or [])
    presented_keys = {value.casefold() for value in presented_before}

    items: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    introduced: list[str] = []
    for index, selection in enumerate(selections):
        candidate = by_name.get(selection.repo)
        if candidate is None:
            rejected.append({
                "index": index, "repo": selection.repo, "reasons": ["unknown_candidate"],
            })
            continue
        reasons, verification = _verify_selection(selection, candidate)
        if reasons:
            rejected.append({
                "index": index, "repo": selection.repo, "reasons": reasons,
                # Enough to tell "wrong evidence cited" from "right evidence, wrong
                # quote". Recorded text is never returned.
                "evidence_ids_checked": list(selection.evidence_ids),
            })
            continue
        label_key = selection.mechanism_label.casefold()
        new_mechanisms = []
        # A repository the host supplied is shown, but it is not a Muse-shroom
        # discovery, so it never counts as a newly introduced mechanism.
        if label_key not in presented_keys and candidate_source(candidate) != "host_supplied":
            new_mechanisms = [selection.mechanism_label]
            introduced.append(selection.mechanism_label)
            presented_keys.add(label_key)
        items.append(_raw_item(
            store, candidate, selection, verification or {}, new_mechanisms,
        ))

    display_order = [str(item["repo"]) for item in items]
    # Build the post-selection view in memory first. A recoverable rank with
    # rejected items must not persist a partial decision; the Agent can resubmit
    # corrected input against the same pre-rank state.
    proposed_records = deepcopy(sidecar_records)
    _mark_sidecar_selection(proposed_records, items, by_name)
    proposed_sidecar_state = deepcopy(sidecar_state)
    proposed_sidecar_state["hypotheses"] = proposed_records
    metrics = proposed_sidecar_state.setdefault("metrics", {})
    metrics["validated_presented"] = sum(
        1 for record in proposed_records if record.get("presented")
    )
    metrics["agent_selected"] = len(items)

    selected_labels = [item["mechanism_label"] for item in items]
    boundary["presented_mechanisms"] = _unique_labels([*presented_before, *selected_labels])
    recalled_labels = [item["mechanism_label"] for item in items if item["source"] != "host_supplied"]
    host_labels = [item["mechanism_label"] for item in items if item["source"] == "host_supplied"]
    boundary["recalled_mechanisms"] = _unique_labels([
        *(boundary.get("recalled_mechanisms") or []), *recalled_labels,
    ])
    origins = dict(boundary.get("mechanism_origins") or {})
    origins["agent_selection"] = _unique_labels(selected_labels)
    if host_labels:
        origins["host_supplied"] = _unique_labels(host_labels)
    boundary["mechanism_origins"] = origins
    previous_boundary = previous_snapshot.get("boundary") if previous_snapshot else None
    delta = compute_boundary_delta(boundary, previous_boundary).to_dict()
    role_counts = {
        role: sum(item["boundary_role"] == role for item in items)
        for role in ("anchor", "edge", "leap", "wildcard")
    }
    summary = {
        **{f"{role}_count": count for role, count in role_counts.items()},
        "mechanisms_shown": _unique_labels([*presented_before, *selected_labels]),
        "new_mechanisms_introduced": introduced,
        "host_supplied_count": len(host_labels),
    }
    # Any rejection means the rank is not final. Saving it and reporting "done" would
    # strand the Agent: the Skill treats rank-with-done as terminal, so it could never
    # resubmit corrected quotes and the rejected repositories would be lost. The escape
    # from a quote the Agent cannot fix is to resubmit only the passing items, which
    # produces zero rejections and terminates; a re-rank is free because rank_search
    # performs no network calls.
    recoverable = bool(rejected)
    result = {
        "schema_version": 3,
        "search_id": search_id,
        "stale": bool(session["stale"]),
        "incomplete_phase": session["incomplete_phase"],
        "next_action": "rank" if recoverable else "done",
        "items": items,
        "display_order": display_order,
        "rejected_items": rejected,
        "boundary": boundary,
        "boundary_delta": delta,
        "boundary_summary": summary,
        "newly_presented_mechanisms": introduced,
        "semantic_hypotheses": [public_hypothesis(item) for item in proposed_records],
        "sidecar_metrics": {
            **dict(metrics),
            "base_ledger": list(sidecar_state.get("base_ledger") or []),
        },
        "coverage": {
            "recalled": len(by_name),
            "selected": len(selections),
            "returned": len(items),
            "rejected": len(rejected),
            "evidence_verified": len(items),
            "presented_mechanism_count": len(boundary["presented_mechanisms"]),
            **{f"{role}_count": count for role, count in role_counts.items()},
        },
    }
    if no_recommendation_reason:
        result["no_recommendation"] = {"reason": no_recommendation_reason}
    if not recoverable:
        session_state["semantic_sidecar"] = proposed_sidecar_state
        store.save_session_state(search_id, session_state)
        store.save_boundary_snapshot(
            search_id, "rank", boundary,
            visible_repos={"assessment_repos": display_order, "pool_repos": list(by_name)},
        )
        store.save_ranking(search_id, result)
    return result
