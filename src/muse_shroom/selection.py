from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from .models import Concept, SearchRequest, repo_key
from .queries import is_generic_term


QUERY_WEIGHTS = {
    "core": 1.0, "typed": 1.0, "gem": 0.9, "adjacent": 0.8,
    "refinement": 1.05, "anchor": 1.1, "key_file": 1.1,
}
RELATION_WEIGHTS = {
    "key_file": 95.0, "readme_link": 90.0, "reverse_readme": 85.0,
    "fork": 65.0, "same_owner": 45.0,
}
TOKEN_RE = re.compile(r"[A-Za-z0-9_+#.-]+|[\u3400-\u9fff]+")
GENERIC_PARTIAL_TOKENS = {"skill", "skills", "tool", "tools", "ai", "agent", "agents"}


def _text(candidate: dict[str, Any], include_readme: bool = False) -> dict[str, str]:
    values = {
        "name": str(candidate.get("full_name", "")).casefold(),
        "topics": " ".join(map(str, candidate.get("topics", []))).casefold(),
        "description": str(candidate.get("description", "")).casefold(),
    }
    if include_readme:
        values["readme"] = str(candidate.get("readme", "")).casefold()
    return values


def _term_coverage(term: str, surfaces: dict[str, str]) -> float:
    phrase = term.casefold().strip()
    if not phrase:
        return 0.0
    multipliers = {"name": 1.0, "topics": .95, "description": .8, "readme": .6}
    best = 0.0
    tokens = [token for token in TOKEN_RE.findall(phrase) if token.casefold() not in GENERIC_PARTIAL_TOKENS]
    if not tokens and phrase not in GENERIC_PARTIAL_TOKENS:
        tokens = TOKEN_RE.findall(phrase)
    for name, text in surfaces.items():
        if phrase in text:
            best = max(best, multipliers[name])
        elif tokens:
            coverage = sum(token in text for token in tokens) / len(tokens)
            best = max(best, coverage * multipliers[name] * .7)
    return best


def concept_coverage(candidate: dict[str, Any], concepts: Iterable[Concept],
                     *, include_readme: bool = False) -> float:
    concepts = list(concepts)
    denominator = sum(concept.weight for concept in concepts)
    if denominator <= 0:
        return 0.0
    surfaces = _text(candidate, include_readme)
    return min(100.0, sum(
        _term_coverage(concept.term, surfaces) * concept.weight for concept in concepts
    ) / denominator * 100)


def relationship_score(candidate: dict[str, Any]) -> float:
    return max((RELATION_WEIGHTS.get(path.get("relation"), 30.0)
                for path in candidate.get("discovery_paths", [])
                if path.get("kind") == "relationship"), default=0.0)


def _rrf_raw(candidate: dict[str, Any]) -> float:
    seen: set[tuple[str, str]] = set()
    score = 0.0
    for path in candidate.get("discovery_paths", []):
        if path.get("kind") != "query":
            continue
        identity = (str(path.get("query", "")), str(path.get("query_kind", "")))
        if identity in seen:
            continue
        seen.add(identity)
        weight = QUERY_WEIGHTS.get(identity[1], .7)
        position = max(1, int(path.get("position", 10)))
        score += weight / (60 + position)
    return score


def _normalized(values: dict[str, float]) -> dict[str, float]:
    maximum = max(values.values(), default=0.0)
    return {key: (value / maximum * 100 if maximum else 0.0) for key, value in values.items()}


def _percentiles(candidates: list[dict[str, Any]]) -> dict[str, float]:
    stars = sorted({int(item.get("stargazers_count", 0)) for item in candidates})
    if len(stars) == 1:
        return {repo_key(item): 100.0 for item in candidates}
    denominator = max(1, len(stars) - 1)
    rank = {value: index / denominator * 100 for index, value in enumerate(stars)}
    return {repo_key(item): rank[int(item.get("stargazers_count", 0))] for item in candidates}


def _activity(candidate: dict[str, Any]) -> float:
    raw = candidate.get("pushed_at")
    if not raw:
        return 0.0
    try:
        pushed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    days = max(0, (datetime.now(timezone.utc) - pushed).days)
    return max(0.0, 100.0 - days / 7)


def _underexposure(candidate: dict[str, Any]) -> float:
    return max(0.0, min(100.0, 100 - 20 * math.log10(int(candidate.get("stargazers_count", 0)) + 1)))


def _evidence_completeness(candidate: dict[str, Any]) -> float:
    kinds = {item.get("kind") for item in candidate.get("evidence", [])}
    snippet_types = {
        item.get("facts", {}).get("snippet_type") for item in candidate.get("evidence", [])
        if item.get("kind") == "readme_excerpt"
    }
    score = 0.0
    score += 35 if "overview" in snippet_types or "concept_match" in snippet_types else 0
    score += 25 if "installation" in snippet_types else 0
    score += 20 if "usage" in snippet_types else 0
    score += 10 if "type_risk" in snippet_types else 0
    score += 10 if "github_release" in kinds else 0
    return score


def score_candidates(candidates: Iterable[dict[str, Any]], request: SearchRequest,
                     *, enriched: bool) -> list[dict[str, Any]]:
    items = list(candidates)
    rrf = _normalized({repo_key(item): _rrf_raw(item) for item in items})
    popularity = _percentiles(items)
    for item in items:
        key = repo_key(item)
        core = concept_coverage(
            item, [concept for concept in request.core_concepts if not is_generic_term(concept.term)],
            include_readme=enriched,
        )
        adjacent = concept_coverage(
            item, [concept for concept in request.adjacent_concepts if not is_generic_term(concept.term)],
            include_readme=enriched,
        )
        relation = relationship_score(item)
        recall = rrf.get(key, 0.0) * .55 + core * .30 + relation * .15
        evidence = _evidence_completeness(item) if enriched else 0.0
        activity = _activity(item)
        underexposure = _underexposure(item)
        item["selection_score_components"] = {
            "recall": round(recall, 2), "rrf": round(rrf.get(key, 0.0), 2),
            "core_concept": round(core, 2), "adjacent_concept": round(adjacent, 2),
            "relationship": round(relation, 2), "popularity_percentile": round(popularity.get(key, 0.0), 2),
            "activity": round(activity, 2), "underexposure": round(underexposure, 2),
            "evidence_completeness": round(evidence, 2),
        }
        kinds = set(item.get("matched_kinds", []))
        lanes: list[str] = []
        if kinds & {"core", "typed", "refinement", "anchor", "key_file"} or relation >= 65:
            lanes.append("core")
        if ("gem" in kinds or underexposure >= 20) and (recall >= 15 or relation >= 65):
            lanes.append("gems")
        if "adjacent" in kinds or adjacent >= 35:
            lanes.append("adjacent")
        if recall >= 10 or relation >= 65:
            lanes.append("popular")
        item["selection_lanes"] = lanes
        item["_lane_scores"] = {
            "core": recall * .75 + evidence * .25,
            "gems": recall * .40 + underexposure * .20 + evidence * .20 + relation * .10 + activity * .10,
            "adjacent": adjacent * .45 + rrf.get(key, 0.0) * .20 + relation * .15 + evidence * .10 + underexposure * .10,
            "popular": recall * .45 + popularity.get(key, 0.0) * .30 + activity * .15 + evidence * .10,
        }
    return items


def balanced_select(candidates: Iterable[dict[str, Any]], request: SearchRequest,
                    quotas: dict[str, int], *, enriched: bool,
                    max_per_owner: int | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    items = score_candidates(candidates, request, enriched=enriched)
    selected: list[dict[str, Any]] = []
    selected_names: set[str] = set()
    owner_counts: dict[str, int] = {}
    counts = {lane: 0 for lane in quotas}

    def add(item: dict[str, Any]) -> bool:
        key = repo_key(item)
        owner = key.partition("/")[0]
        if key in selected_names:
            return False
        if max_per_owner is not None and owner_counts.get(owner, 0) >= max_per_owner:
            return False
        selected.append(item)
        selected_names.add(key)
        owner_counts[owner] = owner_counts.get(owner, 0) + 1
        return True

    for lane, quota in quotas.items():
        pool = sorted(
            (item for item in items if lane in item.get("selection_lanes", [])),
            key=lambda item: (-item["_lane_scores"][lane], repo_key(item)),
        )
        for item in pool:
            if not add(item):
                continue
            counts[lane] += 1
            if counts[lane] >= quota:
                break
    target = sum(quotas.values())
    fallback = sorted(
        (item for item in items if repo_key(item) not in selected_names),
        key=lambda item: (-max(item["_lane_scores"].values(), default=0.0), repo_key(item)),
    )
    for item in fallback:
        if len(selected) >= target:
            break
        add(item)
    counts["fallback"] = max(0, len(selected) - sum(counts.values()))
    for item in items:
        item.pop("_lane_scores", None)
    return selected[:target], counts


def candidate_allowed(candidate: dict[str, Any], request: SearchRequest, *, include_readme: bool) -> bool:
    constraints = request.constraints
    if not constraints.get("include_archived", False) and candidate.get("archived", False):
        return False
    stars = int(candidate.get("stargazers_count", 0))
    if constraints.get("min_stars") is not None and stars < int(constraints["min_stars"]):
        return False
    if constraints.get("max_stars") is not None and stars > int(constraints["max_stars"]):
        return False
    language = constraints.get("language")
    if language and str(candidate.get("language", "")).casefold() != str(language).casefold():
        return False
    pushed_after = constraints.get("pushed_after")
    if pushed_after and str(candidate.get("pushed_at", ""))[:10] < str(pushed_after):
        return False
    terms = [term.casefold() for term in request.exclusions]
    if terms:
        surfaces = _text(candidate, include_readme=include_readme)
        haystack = " ".join(surfaces.values())
        phrase_tokens = re.compile(r"[A-Za-z0-9_+#]+|[\u3400-\u9fff]+")
        normalized_haystack = " ".join(phrase_tokens.findall(haystack))

        def excluded(term: str) -> bool:
            normalized_term = " ".join(phrase_tokens.findall(term))
            return term in haystack or bool(normalized_term) and normalized_term in normalized_haystack
        if any(excluded(term) for term in terms):
            return False
    return True
