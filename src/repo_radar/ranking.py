from __future__ import annotations

import math
from dataclasses import asdict
from typing import Any

from .analyze import age_days
from .models import Assessment, ContractError, repo_key
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
    ordered = sorted((int(item.get("stargazers_count", 0)), repo_key(item)) for item in candidates)
    denominator = max(1, len(ordered) - 1)
    return {name: index / denominator * 100 for index, (_, name) in enumerate(ordered)}


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
    strengths = {"readme_link": 90, "reverse_readme": 85, "fork": 65, "same_owner": 45}
    values = [strengths.get(path.get("relation"), 30) for path in candidate.get("discovery_paths", [])
              if path.get("kind") == "relationship"]
    return max(values, default=30 if candidate.get("discovery_paths") else 0)


def _similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_tokens = set(map(str.lower, left.get("topics", []))) | {left.get("assessment", {}).get("category", "").lower()}
    right_tokens = set(map(str.lower, right.get("topics", []))) | {right.get("assessment", {}).get("category", "").lower()}
    left_tokens.discard("")
    right_tokens.discard("")
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 0.0


def _mmr_select(pool: list[dict[str, Any]], count: int, selected: list[dict[str, Any]],
                score_name: str, diversity: float = 0.22) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    remaining = list(pool)
    while remaining and len(chosen) < count:
        def value(item: dict[str, Any]) -> float:
            peers = selected + chosen
            penalty = max((_similarity(item, other) for other in peers), default=0.0) * 100
            return item["scores"][score_name] * (1 - diversity) - penalty * diversity
        best = max(remaining, key=value)
        chosen.append(best)
        remaining.remove(best)
    return chosen


def rank_search(store: Store, search_id: str, assessment_payload: Any) -> dict[str, Any]:
    session = store.load_search(search_id)
    candidates = session["candidates"]
    by_name = {repo_key(item): item for item in candidates}
    raw_assessments = assessment_payload.get("assessments", []) if isinstance(assessment_payload, dict) else assessment_payload
    if not isinstance(raw_assessments, list):
        raise ContractError("assessments must be a list or an object containing assessments")
    assessments: dict[str, Assessment] = {}
    for raw in raw_assessments:
        name = str(raw.get("repo", "")).lower()
        if name not in by_name:
            raise ContractError(f"assessment references unknown candidate: {name}")
        evidence_ids = {str(item.get("id")) for item in by_name[name].get("evidence", [])}
        assessment = Assessment.from_dict(raw, evidence_ids)
        assessments[name] = assessment
        store.save_assessment(search_id, name, asdict(assessment))
    if not assessments:
        raise ContractError("at least one assessment is required")

    percentiles = _percentiles([by_name[name] for name in assessments])
    scored = []
    for name, assessment in assessments.items():
        candidate = by_name[name]
        stars = int(candidate.get("stargazers_count", 0))
        popularity = percentiles[name]
        activity = max(0.0, 100.0 - age_days(candidate.get("pushed_at")) / 7)
        type_quality = _type_quality(assessment.artifact_type, candidate)
        relation = _relationship(candidate)
        exposure = max(0.0, min(100.0, 100 - 20 * math.log10(stars + 1)))
        easy = DIFFICULTY[assessment.difficulty]
        personal = store.feedback_bias(name, candidate.get("topics", [])) * 15.0
        popular_score = (
            assessment.relevance * .30 + popularity * .30 + activity * .15 +
            type_quality * .15 + assessment.usability * .10 + personal
        )
        gem_score = (
            assessment.relevance * .27 + assessment.uniqueness * .20 + assessment.usability * .16 +
            easy * .10 + exposure * .10 + type_quality * .09 + relation * .08 + personal
        )
        adjacent_score = (
            assessment.relevance * .24 + assessment.uniqueness * .28 + assessment.usability * .16 +
            easy * .10 + type_quality * .10 + relation * .12 + personal
        )
        item = {
            "repo": candidate["full_name"], "url": candidate.get("html_url"),
            "description": candidate.get("description"), "stars": stars,
            "topics": candidate.get("topics", []), "assessment": asdict(assessment),
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

    eligible = [item for item in scored if item["assessment"]["relevance"] >= 45 and item["scores"]["components"]["type_quality"] >= 25]
    adjacent_pool = [item for item in eligible if "adjacent" in by_name[item["repo"].lower()].get("matched_kinds", [])]
    adjacent = _mmr_select(adjacent_pool, 2, [], "adjacent")
    used = {item["repo"].lower() for item in adjacent}
    popular_pool = [
        item for item in eligible
        if item["repo"].lower() not in used
        and item["scores"]["components"]["popularity_percentile"] >= 60
    ]
    popular = _mmr_select(popular_pool, 4, adjacent, "popular")
    used.update(item["repo"].lower() for item in popular)
    gem_pool = [item for item in eligible if item["repo"].lower() not in used and item["scores"]["components"]["underexposure"] >= 20]
    gems = _mmr_select(gem_pool, 4, adjacent + popular, "gem")
    result = {
        "schema_version": 1, "search_id": search_id,
        "stale": bool(session["stale"]), "incomplete_phase": session["incomplete_phase"],
        "buckets": {"popular": popular, "gems": gems, "adjacent": adjacent},
        "coverage": {
            "assessed": len(assessments), "eligible": len(eligible),
            "returned": len(popular) + len(gems) + len(adjacent),
            "adjacent_share": round(len(adjacent) / max(1, len(popular) + len(gems) + len(adjacent)), 3),
        },
    }
    store.save_ranking(search_id, result)
    return result
