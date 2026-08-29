from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict
from typing import Any, Iterable

from .analyze import age_days
from .boundary import annotate_candidate_mechanisms, build_boundary
from .boundary_score import (
    RANK_BOUNDARY_WEIGHTS, RELEVANCE_GATE, TYPE_QUALITY_GATE,
    assign_boundary_role, boundary_summary, candidate_mechanism_names,
    contribution_score, evidence_mechanism_labels, exploration_terms,
    gated_boundary_value, inspiration_score, mechanism_overlap,
    new_mechanisms_for, novelty_score, recalled_mechanism_counts,
    redundancy_penalty,
)
from .models import Assessment, ContractError, SearchRequest, repo_key
from .storage import Store


DIFFICULTY = {"easy": 100.0, "medium": 65.0, "hard": 25.0, "unknown": 45.0}


def _metadata_facts(candidate: dict[str, Any]) -> dict[str, Any]:
    for evidence in candidate.get("evidence", []):
        if evidence.get("kind") == "github_metadata":
            return dict(evidence.get("facts", {}))
    return {}


def _readme_facts(candidate: dict[str, Any]) -> dict[str, Any]:
    for evidence in candidate.get("evidence", []):
        if evidence.get("kind") == "readme":
            return dict(evidence.get("facts", {}))
    return {}


def _percentiles(candidates: list[dict[str, Any]]) -> dict[str, float]:
    star_values = sorted({int(item.get("stargazers_count", 0)) for item in candidates})
    if len(star_values) == 1:
        return {repo_key(item): 100.0 for item in candidates}
    denominator = max(1, len(star_values) - 1)
    ranks = {stars: index / denominator * 100 for index, stars in enumerate(star_values)}
    return {repo_key(item): ranks[int(item.get("stargazers_count", 0))] for item in candidates}


def _type_quality(artifact_type: str, candidate: dict[str, Any]) -> float:
    readme = _readme_facts(candidate)
    metadata = _metadata_facts(candidate)
    base = 20.0
    if readme:
        base += 15
    if readme.get("has_install"):
        base += 20
    if readme.get("has_usage"):
        base += 15
    if metadata.get("license") and metadata["license"] != "NOASSERTION":
        base += 10
    if candidate.get("latest_release"):
        base += 10
    if artifact_type == "mcp":
        base += 10 if readme.get("mentions_tool_contract") else -10
        base += 10 if readme.get("mentions_permissions") else -5
    elif artifact_type == "skill":
        lower = candidate.get("readme", "").lower()
        base += 10 if "trigger" in lower or "when to use" in lower else -5
        base += 10 if "skill.md" in lower else -5
    elif artifact_type == "mod":
        base += 10 if readme.get("mentions_compatibility") else -10
        base += 10 if readme.get("mentions_uninstall") else -5
    return max(0.0, min(100.0, base))


def _relationship(candidate: dict[str, Any]) -> float:
    strengths = {"key_file": 95, "readme_link": 90, "reverse_readme": 85, "fork": 65, "same_owner": 45}
    values = [strengths.get(path.get("relation"), 30) for path in candidate.get("discovery_paths", [])
              if path.get("kind") == "relationship"]
    return max(values, default=30 if candidate.get("discovery_paths") else 0)


def _similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    def tokens(item: dict[str, Any]) -> set[str]:
        stopwords = {"the", "and", "for", "with", "from", "tool", "tools", "app", "application", "project", "github"}
        description = re.findall(r"[a-z0-9_+#.-]{3,}", str(item.get("description", "")).lower())
        return (
            set(map(str.lower, item.get("topics", [])))
            | {item.get("assessment", {}).get("category", "").lower()}
            | (set(description) - stopwords)
        )
    left_tokens = tokens(left)
    right_tokens = tokens(right)
    left_tokens.discard("")
    right_tokens.discard("")
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 0.0


def _mechanism_penalty(item: dict[str, Any], peers: list[dict[str, Any]]) -> float:
    return max((mechanism_overlap(item, other) for other in peers), default=0.0) * 100


def _mmr_select(pool: list[dict[str, Any]], count: int, selected: list[dict[str, Any]],
                score_name: str, diversity: float = 0.22,
                *, boundary_ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    remaining = list(pool)
    ctx = boundary_ctx or {}
    presented = set(ctx.get("presented") or [])
    presented_counts: Counter[str] = ctx.get("presented_counts") or Counter()
    exploration = list(ctx.get("exploration") or [])
    pool_counts = ctx.get("pool_counts") or Counter()
    mode = str(ctx.get("mode") or "deep")
    weights = RANK_BOUNDARY_WEIGHTS
    if mode != "deep":
        weights = {key: value * 0.4 for key, value in RANK_BOUNDARY_WEIGHTS.items()}

    while remaining and len(chosen) < count:
        def value(item: dict[str, Any]) -> tuple[float, str]:
            peers = selected + chosen
            text_penalty = max((_similarity(item, other) for other in peers), default=0.0) * 100
            mech_penalty = _mechanism_penalty(item, peers)
            penalty = mech_penalty * 0.65 + text_penalty * 0.35
            contrib = gated_boundary_value(
                contribution_score(item, presented, exploration=exploration),
                item, relevance=float(item["assessment"]["relevance"]),
                evidence_completeness=float(item["scores"]["components"].get("evidence_completeness") or 0),
            )
            novelty = gated_boundary_value(
                novelty_score(
                    item, presented=presented, recalled_counts=pool_counts, exploration=exploration,
                ),
                item, relevance=float(item["assessment"]["relevance"]),
                evidence_completeness=float(item["scores"]["components"].get("evidence_completeness") or 0),
            )
            red = redundancy_penalty(item, presented, presented_counts=presented_counts)
            transfer = item["assessment"].get("transferability")
            transfer = 50.0 if transfer is None else float(transfer)
            boundary_value = item["assessment"].get("boundary_value")
            allowed = contrib > 15 or novelty > 15
            value_bonus = 0.0
            if allowed and boundary_value is not None:
                value_bonus = (float(boundary_value) - 50.0) * weights["boundary_value"]
            base = item["scores"][score_name]
            live = (
                base * (1 - diversity) - penalty * diversity
                + contrib * weights["contribution"]
                + novelty * weights["novelty"]
                + (transfer - 50.0) * weights["transferability"]
                + value_bonus
                - red * weights["redundancy"]
            )
            return live, str(item.get("repo") or "").lower()

        best = sorted(remaining, key=lambda item: (-value(item)[0], str(item.get("repo") or "").lower()))[0]
        chosen.append(best)
        remaining.remove(best)
        for name in candidate_mechanism_names(best):
            presented.add(name.casefold())
            presented_counts[name.casefold()] += 1
    if boundary_ctx is not None:
        boundary_ctx["presented"] = presented
        boundary_ctx["presented_counts"] = presented_counts
    return chosen


def _explain_ranked_items(items: list[dict[str, Any]], presented_before: Iterable[str],
                          by_name: dict[str, dict[str, Any]]) -> None:
    presented = {str(name).casefold() for name in presented_before if str(name).strip()}
    for item in items:
        kinds = by_name[item["repo"].lower()].get("matched_kinds", [])
        fresh = new_mechanisms_for(item, presented)
        role = assign_boundary_role(item, presented, matched_kinds=kinds)
        transfer = item["assessment"].get("transferability")
        reason = ""
        reasons = item.get("assessment", {}).get("reasons") or []
        if reasons and str(reasons[0].get("text") or "").strip():
            reason = str(reasons[0]["text"]).strip()
        lead = "introduces " + ", ".join(fresh) if fresh else "shares already presented mechanisms"
        item["boundary_role"] = role
        item["new_mechanisms"] = fresh
        item["why_different"] = (f"{lead}; {reason}" if reason else lead)[:240]
        item["transferability"] = 50.0 if transfer is None else float(transfer)
        item["inspiration_score"] = item["scores"]["components"].get("inspiration")
        for name in candidate_mechanism_names(item):
            presented.add(name.casefold())


def rank_search(store: Store, search_id: str, assessment_payload: Any) -> dict[str, Any]:
    session = store.load_search(search_id)
    candidates = session["candidates"]
    try:
        boundary_request = SearchRequest.from_dict(session["request"])
    except ContractError:
        # Rankings created by v0.2/v0.3 tests or persisted sessions may only
        # contain the original request string.
        boundary_request = SearchRequest.from_dict({
            "request": str(session["request"].get("request") or "legacy search"),
            "problem_concepts": [str(session["request"].get("request") or "legacy search")],
        })
    for candidate in candidates:
        annotate_candidate_mechanisms(candidate, boundary_request)
    by_name = {repo_key(item): item for item in candidates}
    raw_assessments = assessment_payload.get("assessments", []) if isinstance(assessment_payload, dict) else assessment_payload
    if not isinstance(raw_assessments, list):
        raise ContractError("assessments must be a list or an object containing assessments")
    assessments: dict[str, Assessment] = {}
    for raw in raw_assessments:
        name = str(raw.get("repo", "")).lower()
        if name not in by_name:
            raise ContractError(f"assessment references unknown candidate: {name}")
        evidence = {
            str(item.get("id")): str(item.get("kind"))
            for item in by_name[name].get("evidence", [])
        }
        assessment = Assessment.from_dict(raw, evidence)
        if assessment.mechanism:
            allowed = evidence_mechanism_labels(by_name[name])
            if assessment.mechanism.casefold() not in allowed:
                raise ContractError(
                    f"assessment mechanism for {name} must match evidence-backed mechanisms"
                )
        assessments[name] = assessment
        store.save_assessment(search_id, name, asdict(assessment))
    if not assessments:
        raise ContractError("at least one assessment is required")

    mode = str(session.get("mode") or "quick")
    previous_boundary = store.latest_boundary_snapshot(search_id)
    rejected = list((previous_boundary or {}).get("boundary", {}).get("rejected_directions", []))
    negatives = list((previous_boundary or {}).get("boundary", {}).get("negative_directions", []))
    presented_before = list((previous_boundary or {}).get("boundary", {}).get("presented_mechanisms") or [])
    if (previous_boundary or {}).get("stage") == "rank":
        presented_before = list(
            ((store.latest_boundary_snapshot(search_id, ("search", "expand", "iterate")) or {}).get("boundary") or {})
            .get("presented_mechanisms") or []
        )
    exploration = exploration_terms(boundary_request)
    pool_counts = recalled_mechanism_counts(candidates)
    percentiles = _percentiles(candidates)
    scored = []
    for name, assessment in assessments.items():
        candidate = by_name[name]
        stars = int(candidate.get("stargazers_count", 0))
        popularity = percentiles[name]
        activity = max(0.0, 100.0 - age_days(candidate.get("pushed_at")) / 7)
        type_quality = _type_quality(assessment.artifact_type, candidate)
        relation = _relationship(candidate)
        evidence_completeness = float(
            candidate.get("selection_score_components", {}).get("evidence_completeness", 0)
        )
        exposure = max(0.0, min(100.0, 100 - 20 * math.log10(stars + 1)))
        easy = DIFFICULTY[assessment.difficulty]
        personal = store.feedback_bias(name, candidate.get("topics", [])) * 15.0
        popular_score = (
            assessment.relevance * .30 + popularity * .30 + activity * .15 +
            type_quality * .15 + assessment.usability * .10 + personal
        )
        gem_score = (
            assessment.relevance * .25 + assessment.uniqueness * .18 + assessment.usability * .14 +
            easy * .08 + exposure * .10 + type_quality * .08 + relation * .07 +
            evidence_completeness * .10 + personal
        )
        adjacent_score = (
            assessment.relevance * .22 + assessment.uniqueness * .25 + assessment.usability * .14 +
            easy * .09 + type_quality * .09 + relation * .11 + evidence_completeness * .10 + personal
        )
        novelty = novelty_score(
            candidate, presented=presented_before, recalled_counts=pool_counts, exploration=exploration,
        )
        contribution = contribution_score(candidate, presented_before, exploration=exploration)
        transfer = 50.0 if assessment.transferability is None else float(assessment.transferability)
        inspiration = inspiration_score(
            assessment.relevance, novelty, transfer, evidence_completeness,
        )
        item = {
            "repo": candidate["full_name"], "url": candidate.get("html_url"),
            "description": candidate.get("description"), "stars": stars,
            "topics": candidate.get("topics", []), "assessment": asdict(assessment),
            "mechanisms": candidate.get("mechanisms", []),
            "discovery_paths": candidate.get("discovery_paths", []),
            "evidence": candidate.get("evidence", []),
            "scores": {
                "popular": round(max(0, min(100, popular_score)), 2),
                "gem": round(max(0, min(100, gem_score)), 2),
                "adjacent": round(max(0, min(100, adjacent_score)), 2),
                "components": {
                    "popularity_percentile": round(popularity, 2), "activity": round(activity, 2),
                    "type_quality": round(type_quality, 2), "relationship": round(relation, 2),
                    "underexposure": round(exposure, 2), "personalization": round(personal, 2),
                    "evidence_completeness": round(evidence_completeness, 2),
                    "mechanism_novelty": novelty,
                    "boundary_contribution": contribution,
                    "inspiration": inspiration,
                },
            },
            "star_growth": None,
        }
        history = store.star_history(name)
        if len(history) >= 2:
            item["star_growth"] = {
                "from": history[0]["stars"], "to": history[-1]["stars"],
                "from_time": history[0]["captured_at"], "to_time": history[-1]["captured_at"],
            }
        scored.append(item)

    eligible = [
        item for item in scored
        if item["assessment"]["relevance"] >= RELEVANCE_GATE
        and item["scores"]["components"]["type_quality"] >= TYPE_QUALITY_GATE
    ]
    boundary_ctx = {
        "presented": {name.casefold() for name in presented_before},
        "presented_counts": Counter(name.casefold() for name in presented_before),
        "exploration": exploration,
        "pool_counts": pool_counts,
        "mode": mode,
    }
    adjacent_pool = []
    for item in eligible:
        kinds = set(by_name[item["repo"].lower()].get("matched_kinds", []))
        transfer = item["assessment"].get("transferability")
        transfer = 50.0 if transfer is None else float(transfer)
        new = new_mechanisms_for(item, presented_before)
        if "adjacent" in kinds or (new and transfer >= 70):
            adjacent_pool.append(item)
    adjacent = _mmr_select(adjacent_pool, 2, [], "adjacent", boundary_ctx=boundary_ctx)
    used = {item["repo"].lower() for item in adjacent}
    popular_pool = [
        item for item in eligible
        if item["repo"].lower() not in used
        and item["scores"]["components"]["popularity_percentile"] >= 60
    ]
    popular = _mmr_select(popular_pool, 4, adjacent, "popular", boundary_ctx=boundary_ctx)
    used.update(item["repo"].lower() for item in popular)
    gem_pool = [item for item in eligible if item["repo"].lower() not in used and item["scores"]["components"]["underexposure"] >= 20]
    gems = _mmr_select(gem_pool, 4, adjacent + popular, "gem", boundary_ctx=boundary_ctx)
    pick_order = adjacent + popular + gems
    _explain_ranked_items(pick_order, presented_before, by_name)
    ranked_items = popular + gems + adjacent
    returned_names = {item["repo"].lower() for item in ranked_items}
    boundary = build_boundary(
        candidates, [by_name[name] for name in returned_names],
        boundary_request, rejected_directions=rejected,
        negative_directions=negatives,
    ).to_dict()
    delta = store.save_boundary_snapshot(search_id, "rank", boundary)
    assignments = sum(len(by_name[item["repo"].lower()].get("mechanisms") or []) for item in ranked_items)
    redundancy = round(
        max(0, assignments - len(boundary["presented_mechanisms"])) / max(1, assignments), 3,
    )
    summary = boundary_summary(
        ranked_items, presented_before, boundary["presented_mechanisms"], redundancy,
    )
    result = {
        "schema_version": 2, "search_id": search_id,
        "stale": bool(session["stale"]), "incomplete_phase": session["incomplete_phase"],
        "buckets": {"popular": popular, "gems": gems, "adjacent": adjacent},
        "boundary": boundary, "boundary_delta": delta,
        "boundary_summary": summary,
        "newly_presented_mechanisms": summary["new_mechanisms_introduced"],
        "coverage": {
            "recalled": len(candidates), "assessed": len(assessments), "eligible": len(eligible),
            "returned": len(ranked_items),
            "adjacent_share": round(len(adjacent) / max(1, len(ranked_items)), 3),
            "mechanism_count": len(boundary["recalled_mechanisms"]),
            "presented_mechanism_count": len(boundary["presented_mechanisms"]),
            "mechanism_redundancy": redundancy,
            "boundary_gain": len(delta["new_mechanisms"]),
            "direction_coverage": round(
                len(boundary["explored_directions"])
                / max(1, len(boundary["explored_directions"]) + len(boundary["unexplored_directions"])),
                3,
            ),
            "anchor_count": summary["anchor_count"],
            "edge_count": summary["edge_count"],
            "leap_count": summary["leap_count"],
            "wildcard_count": summary["wildcard_count"],
        },
    }
    store.save_ranking(search_id, result)
    return result
