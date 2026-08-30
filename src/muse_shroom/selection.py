from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

from .boundary_score import (
    annotate_boundary_signals, candidate_mechanism_names, contribution_score,
    exploration_terms, gated_boundary_value, novelty_score, recalled_mechanism_counts,
    redundancy_penalty, shortlist_quotas,
)
from .models import Concept, SearchRequest, repo_key
from .queries import indexed_groups, is_generic_term


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

PROBE_LIMIT = 30
PROBE_PER_CORE = 3
PROBE_POPULAR = 4
PROBE_LOW_EXPOSURE = 5
PROBE_MAX_OWNER = 2
SHORTLIST_LIMIT = 12
SHORTLIST_QUOTAS = {"core": 3, "gems": 4, "adjacent": 2, "concept_bridge": 3}
SHORTLIST_MAX_OWNER = 2


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
    return _term_source(term, surfaces)[0]


def _term_source(term: str, surfaces: dict[str, str]) -> tuple[float, str | None]:
    phrase = term.casefold().strip()
    if not phrase:
        return 0.0, None
    multipliers = {"name": 1.0, "topics": .95, "description": .8, "readme": .6}
    best = 0.0
    source = None
    tokens = [token for token in TOKEN_RE.findall(phrase) if token.casefold() not in GENERIC_PARTIAL_TOKENS]
    if not tokens and phrase not in GENERIC_PARTIAL_TOKENS:
        tokens = TOKEN_RE.findall(phrase)
    for name, text in surfaces.items():
        if phrase in text:
            score = multipliers[name]
        elif tokens:
            score = (sum(token in text for token in tokens) / len(tokens)) * multipliers[name] * .7
        else:
            score = 0.0
        if score > best:
            best = score
            source = name
    return best, source


def _scored_concepts(concepts: Iterable[Concept]) -> list[Concept]:
    return [concept for concept in concepts if any(not is_generic_term(term) for term in concept.terms())]


def concept_coverage(candidate: dict[str, Any], concepts: Iterable[Concept],
                     *, include_readme: bool = False) -> float:
    concepts = _scored_concepts(concepts)
    denominator = sum(concept.weight for concept in concepts)
    if denominator <= 0:
        return 0.0
    surfaces = _text(candidate, include_readme)
    total = 0.0
    for concept in concepts:
        group_score = max((_term_coverage(term, surfaces) for term in concept.terms()), default=0.0)
        total += group_score * concept.weight
    return min(100.0, total / denominator * 100)


def relationship_score(candidate: dict[str, Any]) -> float:
    return max((RELATION_WEIGHTS.get(path.get("relation"), 30.0)
                for path in candidate.get("discovery_paths", [])
                if path.get("kind") == "relationship"), default=0.0)


def _rrf_raw(candidate: dict[str, Any]) -> float:
    seen: set[tuple[str, str]] = set()
    groups: dict[str, list[float]] = {}
    for path in candidate.get("discovery_paths", []):
        if path.get("kind") != "query":
            continue
        identity = (str(path.get("query", "")), str(path.get("query_kind", "")))
        if identity in seen:
            continue
        seen.add(identity)
        concept_id = str(path.get("concept_id") or "") or f"query:{identity[0]}"
        weight = QUERY_WEIGHTS.get(identity[1], .7)
        position = max(1, int(path.get("position", 10)))
        groups.setdefault(concept_id, []).append(weight / (60 + position))
    score = 0.0
    for contribs in groups.values():
        contribs.sort(reverse=True)
        score += contribs[0]
        if len(contribs) > 1:
            score += 0.25 * contribs[1]
        if len(contribs) > 2:
            score += 0.10 * contribs[2]
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


def _query_paths(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    return [path for path in candidate.get("discovery_paths", []) if path.get("kind") == "query"]


def _concept_query_score(candidate: dict[str, Any], concept_id: str) -> float:
    best = 0.0
    for path in _query_paths(candidate):
        if str(path.get("concept_id") or "") != concept_id:
            continue
        position = max(1, int(path.get("position", 10)))
        weight = QUERY_WEIGHTS.get(str(path.get("query_kind", "")), 0.7)
        best = max(best, 100.0 * weight * 61.0 / (60 + position))
    return min(100.0, best)


def nongeneric_query_source(candidate: dict[str, Any]) -> bool:
    for path in _query_paths(candidate):
        term = str(path.get("term") or "").strip()
        if term and not is_generic_term(term):
            return True
        concept_id = str(path.get("concept_id") or "")
        if concept_id.startswith(("core:", "adjacent:", "refinement:")):
            if not term or not is_generic_term(term):
                return True
        if str(path.get("query_kind") or "") in {"core", "gem", "adjacent", "refinement", "anchor"}:
            return True
    return False


def describe_concept_matches(candidate: dict[str, Any], request: SearchRequest,
                             *, include_readme: bool) -> list[dict[str, Any]]:
    surfaces = _text(candidate, include_readme)
    matches: list[dict[str, Any]] = []
    for prefix, concepts in (("core", request.core_concepts), ("adjacent", request.adjacent_concepts)):
        for index, concept in enumerate(concepts):
            if not any(not is_generic_term(term) for term in concept.terms()):
                continue
            concept_id = f"{prefix}:{index}"
            best_score = 0.0
            matched_alias = concept.term
            source = None
            for term in concept.terms():
                score, term_source = _term_source(term, surfaces)
                if score > best_score:
                    best_score = score
                    matched_alias = term
                    source = term_source
            query_score = _concept_query_score(candidate, concept_id)
            if source is None and query_score > 0:
                source = "query"
                best_score = max(best_score, query_score / 100.0)
                for path in _query_paths(candidate):
                    if str(path.get("concept_id") or "") == concept_id and path.get("term"):
                        matched_alias = str(path.get("term"))
                        break
            if best_score <= 0 and query_score <= 0:
                continue
            matches.append({
                "concept_id": concept_id,
                "label": concept.term,
                "matched_alias": matched_alias,
                "source": source or "query",
                "score": round(best_score * 100),
            })
    return matches


def lexical_concept_evidence(candidate: dict[str, Any]) -> bool:
    return any(
        str(item.get("source") or "") in {"name", "topics", "description", "readme"}
        and float(item.get("score") or 0) > 0
        for item in candidate.get("concept_matches") or []
    )


def _alias_hit(candidate: dict[str, Any]) -> bool:
    for item in candidate.get("concept_matches") or []:
        matched = str(item.get("matched_alias") or "").casefold()
        label = str(item.get("label") or "").casefold()
        if matched and label and matched != label:
            return True
    return False


def _reason_for_lane(lane: str, item: dict[str, Any]) -> str:
    matches = item.get("concept_matches") or []
    if lane == "concept_bridge":
        alias = next(
            (match for match in matches
             if str(match.get("matched_alias") or "").casefold() != str(match.get("label") or "").casefold()),
            None,
        )
        if alias:
            return f"Recalled via alias '{alias['matched_alias']}' of '{alias['label']}'"
        return "Recalled by a specific concept query"
    if lane == "core":
        label = matches[0]["label"] if matches else "core concept"
        return f"Matched core concept '{label}'"
    if lane == "gems":
        return "Low exposure with a non-generic query source and README or metadata evidence"
    if lane == "adjacent":
        label = next((match["label"] for match in matches if str(match.get("concept_id") or "").startswith("adjacent:")),
                     "adjacent concept")
        return f"Matched adjacent concept '{label}'"
    if lane == "boundary":
        names = candidate_mechanism_names(item)
        if names:
            return f"Adds mechanism coverage for '{names[0]}'"
        return "Adds uncovered mechanism coverage"
    return "Filled remaining shortlist from global recall"


def concept_probe_score(candidate: dict[str, Any], concept: Concept, concept_id: str,
                        *, include_readme: bool) -> float:
    surfaces = _text(candidate, include_readme)
    lexical = max((_term_coverage(term, surfaces) for term in concept.terms()), default=0.0) * 100
    query_score = _concept_query_score(candidate, concept_id)
    return lexical * 0.35 + query_score * 0.65


def score_candidates(candidates: Iterable[dict[str, Any]], request: SearchRequest,
                     *, enriched: bool, mode: str = "deep") -> list[dict[str, Any]]:
    items = list(candidates)
    rrf = _normalized({repo_key(item): _rrf_raw(item) for item in items})
    popularity = _percentiles(items)
    for item in items:
        key = repo_key(item)
        item["concept_matches"] = describe_concept_matches(item, request, include_readme=enriched)
        core = concept_coverage(item, request.core_concepts, include_readme=enriched)
        adjacent = concept_coverage(item, request.adjacent_concepts, include_readme=enriched)
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
        has_query_source = nongeneric_query_source(item)
        has_lexical = lexical_concept_evidence(item)
        lanes: list[str] = []
        if kinds & {"core", "typed", "refinement", "anchor", "key_file"} or relation >= 65 or core > 0:
            lanes.append("core")
        if has_query_source and (has_lexical or not enriched) and underexposure >= 20:
            lanes.append("gems")
        if "adjacent" in kinds or adjacent >= 35:
            lanes.append("adjacent")
        if recall >= 10 or relation >= 65:
            lanes.append("popular")
        if has_query_source and (has_lexical or (enriched and str(item.get("readme") or "").strip())):
            if _alias_hit(item) or (underexposure >= 15 and core > 0):
                lanes.append("concept_bridge")
        item["selection_lanes"] = lanes
        item["_lane_scores"] = {
            "core": recall * .75 + evidence * .25,
            "gems": recall * .40 + underexposure * .20 + evidence * .20 + relation * .10 + activity * .10,
            "adjacent": adjacent * .45 + rrf.get(key, 0.0) * .20 + relation * .15 + evidence * .10 + underexposure * .10,
            "popular": recall * .45 + popularity.get(key, 0.0) * .30 + activity * .15 + evidence * .10,
            "concept_bridge": (
                (40.0 if _alias_hit(item) else 0.0)
                + core * .25 + underexposure * .15 + evidence * .15 + rrf.get(key, 0.0) * .10
            ),
        }
    annotate_boundary_signals(items, request, mode=mode)
    return items


def _owner_limited_add(selected: list[dict[str, Any]], selected_names: set[str],
                       owner_counts: dict[str, int], item: dict[str, Any],
                       max_per_owner: int | None, lane: str | None = None) -> bool:
    key = repo_key(item)
    owner = key.partition("/")[0]
    if key in selected_names:
        return False
    if max_per_owner is not None and owner_counts.get(owner, 0) >= max_per_owner:
        return False
    selected.append(item)
    selected_names.add(key)
    owner_counts[owner] = owner_counts.get(owner, 0) + 1
    if lane and not item.get("selection_reason"):
        item["selection_reason"] = {"lane": lane, "reason": _reason_for_lane(lane, item)}
    return True


def _unseen_mechanisms(item: dict[str, Any], presented: set[str]) -> list[str]:
    return [name for name in candidate_mechanism_names(item) if name.casefold() not in presented]


def balanced_select(candidates: Iterable[dict[str, Any]], request: SearchRequest,
                    quotas: dict[str, int], *, enriched: bool,
                    max_per_owner: int | None = None, mode: str = "deep",
                    mechanism_aware: bool = True, rescore: bool = True
                    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
    items = (
        score_candidates(candidates, request, enriched=enriched, mode=mode)
        if rescore else list(candidates)
    )
    selected: list[dict[str, Any]] = []
    selected_names: set[str] = set()
    owner_counts: dict[str, int] = {}
    counts = {lane: 0 for lane in quotas}
    presented: set[str] = set()
    presented_counts: Counter[str] = Counter()
    pool_counts = recalled_mechanism_counts(items)
    exploration = exploration_terms(request)
    weights = (items[0].get("_boundary_weights") if items else None) or (
        {"novelty": 0.12, "contribution": 0.10, "redundancy": 0.18} if mode == "deep"
        else {"novelty": 0.04, "contribution": 0.04, "redundancy": 0.08}
    )

    def add(item: dict[str, Any], lane: str | None = None) -> bool:
        if not _owner_limited_add(selected, selected_names, owner_counts, item, max_per_owner, lane):
            return False
        for name in candidate_mechanism_names(item):
            presented.add(name.casefold())
            presented_counts[name.casefold()] += 1
        return True

    def live_score(item: dict[str, Any], lane: str) -> float:
        base = float((item.get("_lane_scores") or {}).get(lane) or 0.0)
        if not mechanism_aware:
            return base
        contrib = gated_boundary_value(
            contribution_score(item, presented, exploration=exploration), item,
        )
        novelty = gated_boundary_value(
            novelty_score(
                item, presented=presented, recalled_counts=pool_counts, exploration=exploration,
            ),
            item,
        )
        red = redundancy_penalty(item, presented, presented_counts=presented_counts)
        return base + contrib * weights["contribution"] + novelty * weights["novelty"] - red * weights["redundancy"]

    def take(lane: str, quota: int, *, allow_repeat: bool) -> None:
        while counts.get(lane, 0) < quota:
            pool = [
                item for item in items
                if repo_key(item) not in selected_names
                and lane in item.get("selection_lanes", [])
            ]
            if not allow_repeat:
                unseen = [item for item in pool if _unseen_mechanisms(item, presented) or not candidate_mechanism_names(item)]
                if unseen:
                    pool = unseen
            if not pool:
                return
            best = sorted(pool, key=lambda item: (-live_score(item, lane), repo_key(item)))[0]
            if not add(best, lane):
                selected_names.add(repo_key(best))
                continue
            counts[lane] = counts.get(lane, 0) + 1

    for lane, quota in quotas.items():
        take(lane, quota, allow_repeat=False)
        if counts.get(lane, 0) < quota:
            take(lane, quota, allow_repeat=True)
    target = sum(quotas.values())
    fallback = sorted(
        (item for item in items if repo_key(item) not in selected_names),
        key=lambda item: (
            -live_score(item, next((lane for lane in quotas if lane in item.get("selection_lanes", [])), "core")),
            repo_key(item),
        ),
    )
    for item in fallback:
        if len(selected) >= target:
            break
        add(item, "core" if "core" in item.get("selection_lanes", []) else next(
            (lane for lane in quotas if lane in item.get("selection_lanes", [])), "core"
        ))
    counts["fallback"] = max(0, len(selected) - sum(counts.get(lane, 0) for lane in quotas))
    for item in items:
        item.pop("_lane_scores", None)
        item.pop("_boundary_weights", None)
    return selected[:target], counts


def probe_select(candidates: Iterable[dict[str, Any]], request: SearchRequest,
                 limit: int = PROBE_LIMIT) -> tuple[list[dict[str, Any]], dict[str, int]]:
    items = score_candidates(candidates, request, enriched=False)
    selected: list[dict[str, Any]] = []
    selected_names: set[str] = set()
    owner_counts: dict[str, int] = {}
    counts = {"core": 0, "adjacent": 0, "popular": 0, "low_exposure": 0, "recall": 0}

    def add(item: dict[str, Any], bucket: str) -> bool:
        if len(selected) >= limit:
            return False
        if not _owner_limited_add(selected, selected_names, owner_counts, item, PROBE_MAX_OWNER):
            return False
        counts[bucket] = counts.get(bucket, 0) + 1
        return True

    for concept_id, concept, _terms in indexed_groups(request.core_concepts, "core"):
        ranked = sorted(
            items,
            key=lambda item: (
                -concept_probe_score(item, concept, concept_id, include_readme=False),
                repo_key(item),
            ),
        )
        taken = 0
        for item in ranked:
            if concept_probe_score(item, concept, concept_id, include_readme=False) <= 0:
                break
            if add(item, "core"):
                taken += 1
            if taken >= PROBE_PER_CORE:
                break

    for concept_id, concept, _terms in indexed_groups(request.adjacent_concepts, "adjacent"):
        ranked = sorted(
            items,
            key=lambda item: (
                -concept_probe_score(item, concept, concept_id, include_readme=False),
                repo_key(item),
            ),
        )
        for item in ranked:
            if concept_probe_score(item, concept, concept_id, include_readme=False) <= 0:
                break
            if add(item, "adjacent"):
                break

    popular_pool = sorted(
        (item for item in items if repo_key(item) not in selected_names),
        key=lambda item: (-item["_lane_scores"]["popular"], repo_key(item)),
    )
    taken = 0
    for item in popular_pool:
        if taken >= PROBE_POPULAR:
            break
        if add(item, "popular"):
            taken += 1

    low_pool = sorted(
        (
            item for item in items
            if repo_key(item) not in selected_names and nongeneric_query_source(item)
        ),
        key=lambda item: (
            -item["selection_score_components"]["underexposure"],
            -item["selection_score_components"]["recall"],
            repo_key(item),
        ),
    )
    taken = 0
    for item in low_pool:
        if taken >= PROBE_LOW_EXPOSURE:
            break
        if add(item, "low_exposure"):
            taken += 1

    remainder = sorted(
        (item for item in items if repo_key(item) not in selected_names),
        key=lambda item: (-item["selection_score_components"]["recall"], repo_key(item)),
    )
    for item in remainder:
        if len(selected) >= limit:
            break
        add(item, "recall")

    for item in items:
        item.pop("_lane_scores", None)
    return selected[:limit], counts


def shortlist_select(candidates: Iterable[dict[str, Any]], request: SearchRequest,
                     *, mode: str = "deep") -> tuple[list[dict[str, Any]], dict[str, int]]:
    items = list(candidates)
    scored = score_candidates(items, request, enriched=True, mode=mode)
    quotas = shortlist_quotas(scored, mode=mode)
    selected, counts = balanced_select(
        scored, request, quotas, enriched=True, max_per_owner=SHORTLIST_MAX_OWNER, mode=mode,
        rescore=False,
    )
    return selected[:SHORTLIST_LIMIT], counts


COVERAGE_LEXICAL_SOURCES = {"name", "topics", "description", "readme"}
COVERAGE_MIN_SCORE = 40
COVERAGE_RELATION_MIN = 65


def _match_covers_concept(match: dict[str, Any], item: dict[str, Any]) -> bool:
    source = str(match.get("source") or "")
    score = float(match.get("score") or 0)
    if source in COVERAGE_LEXICAL_SOURCES and score >= COVERAGE_MIN_SCORE:
        return True
    return source == "query" and relationship_score(item) >= COVERAGE_RELATION_MIN


def covered_core_ids(selected: Iterable[dict[str, Any]], request: SearchRequest) -> set[str]:
    valid = {concept_id for concept_id, _concept, _terms in indexed_groups(request.core_concepts, "core")}
    covered: set[str] = set()
    for item in selected:
        for match in item.get("concept_matches") or []:
            concept_id = str(match.get("concept_id") or "")
            if concept_id in valid and _match_covers_concept(match, item):
                covered.add(concept_id)
    return covered


def uncovered_core_terms(selected: Iterable[dict[str, Any]], request: SearchRequest) -> list[str]:
    covered = covered_core_ids(selected, request)
    terms = []
    for index, concept in enumerate(request.core_concepts):
        concept_id = f"core:{index}"
        if concept_id in covered:
            continue
        searchable = [term for term in concept.terms() if not is_generic_term(term)]
        if searchable:
            terms.append(concept.term)
    return terms


def _contains_blocked_term(surfaces: Iterable[str], terms: Iterable[str], *,
                           contiguous: bool = True) -> bool:
    haystack = " ".join(str(value).casefold() for value in surfaces)
    phrase_tokens = re.compile(r"[A-Za-z0-9_+#]+|[\u3400-\u9fff]+")
    haystack_tokens = phrase_tokens.findall(haystack)
    normalized_haystack = " ".join(haystack_tokens)
    token_set = set(haystack_tokens)
    for value in terms:
        term = str(value).casefold().strip()
        if not term:
            continue
        normalized_term = " ".join(phrase_tokens.findall(term))
        if term in haystack or normalized_term and normalized_term in normalized_haystack:
            return True
        if not contiguous:
            term_tokens = phrase_tokens.findall(term)
            if term_tokens and all(token in token_set for token in term_tokens):
                return True
    return False


def mechanism_rejected(mechanism: dict[str, Any], rejected_terms: Iterable[str]) -> bool:
    surfaces = [mechanism.get("name") or "", *(mechanism.get("matched_terms") or [])]
    return _contains_blocked_term(surfaces, rejected_terms)


def candidate_allowed(candidate: dict[str, Any], request: SearchRequest, *, include_readme: bool,
                      negative_terms: Iterable[str] = (),
                      rejected_terms: Iterable[str] = ()) -> bool:
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
    surfaces = _text(candidate, include_readme=include_readme)
    if _contains_blocked_term(surfaces.values(), request.exclusions):
        return False
    identity_surfaces = _text(candidate, include_readme=False)
    if _contains_blocked_term(
        identity_surfaces.values(), negative_terms, contiguous=False,
    ):
        return False
    rejected = list(rejected_terms)
    if rejected:
        mechanisms = list(candidate.get("mechanisms") or [])
        if mechanisms:
            rejected_mechanisms = [
                mechanism for mechanism in mechanisms
                if mechanism_rejected(mechanism, rejected)
            ]
            if rejected_mechanisms and len(rejected_mechanisms) == len(mechanisms):
                return False
        elif _contains_blocked_term(
            identity_surfaces.values(), rejected, contiguous=False,
        ):
            return False
    return True
