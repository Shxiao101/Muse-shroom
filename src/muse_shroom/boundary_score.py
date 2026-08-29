from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from .boundary import mechanism_distribution
from .models import SearchRequest


RELEVANCE_GATE = 45.0
TYPE_QUALITY_GATE = 25.0
BOUNDARY_BONUS_RELEVANCE = 50.0
BOUNDARY_BONUS_EVIDENCE = 35.0

QUICK_BOUNDARY_WEIGHTS = {
    "novelty": 0.04,
    "contribution": 0.04,
    "redundancy": 0.08,
}
DEEP_BOUNDARY_WEIGHTS = {
    "novelty": 0.12,
    "contribution": 0.10,
    "redundancy": 0.18,
}

# Additive ranking bonuses after the existing popular/gem/adjacent formulas.
RANK_BOUNDARY_WEIGHTS = {
    "novelty": 0.10,
    "contribution": 0.08,
    "transferability": 0.06,
    "redundancy": 0.16,
}


def candidate_mechanism_names(candidate: dict[str, Any]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for mechanism in candidate.get("mechanisms") or []:
        name = str(mechanism.get("name") or "").strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def candidate_mechanism_roles(candidate: dict[str, Any]) -> dict[str, str]:
    return {
        str(mechanism.get("name") or "").strip(): str(mechanism.get("role") or "")
        for mechanism in candidate.get("mechanisms") or []
        if str(mechanism.get("name") or "").strip()
    }


def recalled_mechanism_counts(candidates: Iterable[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for candidate in candidates:
        for name in candidate_mechanism_names(candidate):
            counts[name.casefold()] += 1
    return counts


def _stack(values: list[float]) -> float:
    if not values:
        return 0.0
    values = sorted(values, reverse=True)
    total = values[0]
    if len(values) > 1:
        total += 0.25 * values[1]
    if len(values) > 2:
        total += 0.10 * values[2]
    return min(100.0, total)


def novelty_score(candidate: dict[str, Any], *, presented: Iterable[str],
                  recalled_counts: Counter[str] | None = None,
                  exploration: Iterable[str] = ()) -> float:
    presented_keys = {str(name).casefold() for name in presented if str(name).strip()}
    exploration_keys = {str(name).casefold() for name in exploration if str(name).strip()}
    counts = recalled_counts or Counter()
    scores: list[float] = []
    roles = candidate_mechanism_roles(candidate)
    for name in candidate_mechanism_names(candidate):
        key = name.casefold()
        pool_count = counts.get(key, 1)
        if key not in presented_keys:
            if roles.get(name) == "exploration" or key in exploration_keys:
                base = 92.0
            elif pool_count <= 1:
                base = 88.0
            elif pool_count <= 2:
                base = 72.0
            else:
                base = max(22.0, 64.0 - 6.0 * (pool_count - 1))
        else:
            base = max(0.0, 32.0 - 8.0 * max(0, pool_count - 1))
        scores.append(base)
    return round(_stack(scores), 2)


def contribution_score(candidate: dict[str, Any], presented: Iterable[str],
                       *, exploration: Iterable[str] = ()) -> float:
    presented_keys = {str(name).casefold() for name in presented if str(name).strip()}
    exploration_keys = {str(name).casefold() for name in exploration if str(name).strip()}
    roles = candidate_mechanism_roles(candidate)
    fresh: list[float] = []
    for name in candidate_mechanism_names(candidate):
        key = name.casefold()
        if key in presented_keys:
            continue
        value = 80.0
        if roles.get(name) == "exploration" or key in exploration_keys:
            value = 92.0
        fresh.append(value)
    return round(_stack(fresh), 2)


def redundancy_penalty(candidate: dict[str, Any], presented: Iterable[str],
                       *, presented_counts: Counter[str] | None = None) -> float:
    presented_keys = {str(name).casefold() for name in presented if str(name).strip()}
    counts = presented_counts or Counter(presented_keys)
    penalties: list[float] = []
    for name in candidate_mechanism_names(candidate):
        already = counts.get(name.casefold(), 1 if name.casefold() in presented_keys else 0)
        if already <= 0:
            penalties.append(0.0)
        elif already == 1:
            penalties.append(18.0)
        else:
            penalties.append(min(80.0, 18.0 + 22.0 * (already - 1)))
    return round(max(penalties, default=0.0), 2)


def mechanism_overlap(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_names = {name.casefold() for name in candidate_mechanism_names(left)}
    right_names = {name.casefold() for name in candidate_mechanism_names(right)}
    if not left_names or not right_names:
        return 0.0
    return len(left_names & right_names) / len(left_names | right_names)


def inspiration_score(relevance: float, novelty: float, transferability: float,
                      evidence: float) -> float:
    return round(max(0.0, min(100.0, (
        max(relevance, 0.0) / 100.0
        * max(novelty, 0.0) / 100.0
        * max(transferability, 0.0) / 100.0
        * max(evidence, 0.0) / 100.0
        * 100.0
    ))), 2)


def has_mechanism_evidence(candidate: dict[str, Any]) -> bool:
    return bool(candidate_mechanism_names(candidate))


def has_readme_evidence(candidate: dict[str, Any]) -> bool:
    for item in candidate.get("evidence") or []:
        kind = str(item.get("kind") or "")
        snippet = str((item.get("facts") or {}).get("snippet_type") or "")
        if kind == "readme_excerpt" or kind == "readme" or snippet in {"usage", "installation", "overview", "concept_match"}:
            return True
    return bool(str(candidate.get("readme") or "").strip())


def boundary_bonus_allowed(candidate: dict[str, Any], *, relevance: float | None = None,
                           evidence_completeness: float | None = None) -> bool:
    if relevance is not None and relevance < BOUNDARY_BONUS_RELEVANCE:
        return False
    completeness = evidence_completeness
    if completeness is None:
        completeness = float((candidate.get("selection_score_components") or {}).get("evidence_completeness") or 0)
    if not has_mechanism_evidence(candidate):
        return False
    if completeness < BOUNDARY_BONUS_EVIDENCE and not has_readme_evidence(candidate):
        return False
    return True


def gated_boundary_value(raw: float, candidate: dict[str, Any], *,
                         relevance: float | None = None,
                         evidence_completeness: float | None = None) -> float:
    if boundary_bonus_allowed(
        candidate, relevance=relevance, evidence_completeness=evidence_completeness,
    ):
        return raw
    return min(raw, 15.0)


def exploration_terms(request: SearchRequest) -> list[str]:
    return [concept.term for concept in request.exploration_directions]


def annotate_boundary_signals(items: list[dict[str, Any]], request: SearchRequest,
                              *, presented: Iterable[str] = (), mode: str = "deep") -> None:
    counts = recalled_mechanism_counts(items)
    exploration = exploration_terms(request)
    weights = DEEP_BOUNDARY_WEIGHTS if mode == "deep" else QUICK_BOUNDARY_WEIGHTS
    presented_list = list(presented)
    for item in items:
        novelty = novelty_score(
            item, presented=presented_list, recalled_counts=counts, exploration=exploration,
        )
        contribution = contribution_score(item, presented_list, exploration=exploration)
        redundancy = redundancy_penalty(item, presented_list)
        allowed = boundary_bonus_allowed(item)
        components = item.setdefault("selection_score_components", {})
        components["mechanism_novelty"] = novelty
        components["boundary_contribution"] = contribution
        components["redundancy_penalty"] = redundancy
        components["gated_novelty"] = gated_boundary_value(novelty, item)
        components["gated_contribution"] = gated_boundary_value(contribution, item)
        lanes = list(item.get("selection_lanes") or [])
        if allowed and (contribution >= 40 or novelty >= 60):
            if "boundary" not in lanes:
                lanes.append("boundary")
        item["selection_lanes"] = lanes
        scores = item.setdefault("_lane_scores", {})
        core = float(components.get("core_concept") or 0)
        evidence = float(components.get("evidence_completeness") or 0)
        scores["boundary"] = (
            gated_boundary_value(contribution, item) * 0.45
            + gated_boundary_value(novelty, item) * 0.30
            + core * 0.15
            + evidence * 0.10
        )
        item["_boundary_weights"] = weights


FALLBACK_QUOTAS = {"core": 3, "gems": 4, "adjacent": 2, "concept_bridge": 3}


def shortlist_quotas(items: Iterable[dict[str, Any]], *, mode: str = "deep") -> dict[str, int]:
    pool = list(items)
    if mode != "deep" or not any(candidate_mechanism_names(item) for item in pool):
        return dict(FALLBACK_QUOTAS)
    dist = mechanism_distribution(pool)
    unique = len(dist)
    assignments = sum(dist.values())
    redundancy = max(0, assignments - unique) / max(1, assignments)
    cores = [
        float((item.get("selection_score_components") or {}).get("core_concept") or 0)
        for item in pool
    ]
    mean_core = sum(cores) / max(1, len(cores))
    if mean_core < 25:
        return {"core": 4, "gems": 3, "adjacent": 2, "concept_bridge": 2, "boundary": 1}
    if redundancy >= 0.45 and unique >= 2:
        return {"boundary": 3, "core": 2, "adjacent": 2, "gems": 3, "concept_bridge": 2}
    return {"core": 3, "boundary": 2, "gems": 3, "adjacent": 2, "concept_bridge": 2}


def new_mechanisms_for(candidate: dict[str, Any], presented: Iterable[str]) -> list[str]:
    presented_keys = {str(name).casefold() for name in presented if str(name).strip()}
    return [name for name in candidate_mechanism_names(candidate) if name.casefold() not in presented_keys]


def assign_boundary_role(item: dict[str, Any], presented_before: Iterable[str],
                         *, matched_kinds: Iterable[str] = ()) -> str:
    new = new_mechanisms_for(item, presented_before)
    transfer = item.get("assessment", {}).get("transferability")
    if transfer is None:
        transfer = 50.0
    popularity = float((item.get("scores") or {}).get("components", {}).get("popularity_percentile") or 0)
    kinds = set(matched_kinds)
    adjacent = "adjacent" in kinds
    if new and float(transfer) >= 70 and (adjacent or popularity < 55):
        return "wildcard"
    if new and (adjacent or float(transfer) >= 55):
        return "leap"
    if popularity >= 60 and not new:
        return "anchor"
    if new:
        return "edge"
    if popularity >= 60:
        return "anchor"
    return "edge"


def boundary_summary(items: Iterable[dict[str, Any]], presented_before: Iterable[str],
                     presented_after: Iterable[str], redundancy: float) -> dict[str, Any]:
    roles = Counter(str(item.get("boundary_role") or "") for item in items if item.get("boundary_role"))
    before = {str(name).casefold() for name in presented_before}
    introduced = [
        name for name in presented_after
        if str(name).casefold() not in before
    ]
    return {
        "mechanisms_shown": list(presented_after),
        "new_mechanisms_introduced": introduced,
        "mechanism_redundancy": redundancy,
        "anchor_count": int(roles.get("anchor", 0)),
        "edge_count": int(roles.get("edge", 0)),
        "leap_count": int(roles.get("leap", 0)),
        "wildcard_count": int(roles.get("wildcard", 0)),
    }
