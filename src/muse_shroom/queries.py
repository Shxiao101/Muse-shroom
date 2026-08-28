from __future__ import annotations

import re
from typing import Iterable

from .models import Concept, Refinement, SearchRequest


TYPE_TERMS = {
    "application": ["app", "tool"],
    "app": ["app", "tool"],
    "mcp": ["mcp", "model context protocol"],
    "skill": ["skill", "agent skill"],
    "mod": ["mod", "modding"],
    "plugin": ["plugin", "extension"],
    "library": ["library", "sdk"],
}


def _quote(term: str) -> str:
    clean = re.sub(r"[\r\n\t]+", " ", term).replace('"', "").replace("\\", "").strip()
    if not clean:
        return ""
    return f'"{clean}"'


def _terms(concepts: Iterable[Concept]) -> list[str]:
    return [c.term for c in sorted(concepts, key=lambda item: item.weight, reverse=True) if c.term]


def _qualifiers(request: SearchRequest) -> str:
    qualifiers = ["is:public"]
    if not request.constraints.get("include_archived", False):
        qualifiers.append("archived:false")
    if request.constraints.get("language"):
        qualifiers.append(f"language:{_quote(str(request.constraints['language']))}")
    if request.constraints.get("pushed_after"):
        qualifiers.append(f"pushed:>={request.constraints['pushed_after']}")
    if request.constraints.get("min_stars") is not None:
        qualifiers.append(f"stars:>={int(request.constraints['min_stars'])}")
    if request.constraints.get("max_stars") is not None:
        qualifiers.append(f"stars:<={int(request.constraints['max_stars'])}")
    return " ".join(qualifiers)


def build_queries(request: SearchRequest, limit: int = 12) -> list[dict[str, str]]:
    """Build validated repository-search queries; agents never construct GitHub syntax."""
    core = _terms(request.core_concepts)
    adjacent = _terms(request.adjacent_concepts)
    type_terms: list[str] = []
    for artifact_type in request.artifact_types:
        type_terms.extend(TYPE_TERMS.get(artifact_type, [artifact_type]))
    suffix = _qualifiers(request)

    raw: list[tuple[str, str, str]] = []
    for concept in core[:3]:
        raw.append((f"{_quote(concept)} in:name,description,topics,readme {suffix}", "core", "stars"))
    typed = []
    for left in core[:2]:
        for right in type_terms[:2] or ["tool"]:
            typed.append((f"{_quote(left)} {_quote(right)} in:name,description,topics,readme {suffix}", "typed", "stars"))
    raw.extend(typed[:3])

    primary = _quote(core[0])
    raw.extend([
        (f"{primary} in:name,description,topics,readme stars:1..500 {suffix}", "gem", "updated"),
        (f"{primary} in:name,description,topics,readme stars:0..50 {suffix}", "gem", "updated"),
    ])

    adjacent_queries = [
        (f"{_quote(term)} in:name,description,topics,readme {suffix}", "adjacent", "stars")
        for term in adjacent[:3]
    ]
    for left in core[:2]:
        for right in adjacent[:3]:
            adjacent_queries.append((
                f"{_quote(left)} {_quote(right)} in:name,description,topics,readme {suffix}", "adjacent", "stars"
            ))
    adjacent_quota = min(3, 2 + round(request.exploration_level)) if adjacent else 0
    raw.extend(adjacent_queries[:adjacent_quota])

    # Ensure even a terse request explores distinct indexed surfaces. These are
    # repository-search variants, not free-form syntax supplied by the agent.
    for scope, kind in (
        ("name,description", "core"), ("topics", "core"), ("readme", "core"),
        ("name,description,topics", "core"),
    ):
        raw.append((f"{primary} in:{scope} {suffix}", kind, "stars"))
    for companion in ("tool", "app", "plugin"):
        raw.append((f"{primary} {_quote(companion)} in:name,description,topics,readme {suffix}", "typed", "stars"))

    seen: set[str] = set()
    result = []
    for query, kind, sort in raw:
        normalized = " ".join(query.split())
        if normalized and normalized not in seen:
            result.append({"query": normalized, "kind": kind, "sort": sort})
            seen.add(normalized)
        if len(result) >= limit:
            break
    return result


def refinement_queries(refinement: Refinement, request: SearchRequest,
                       limit: int = 10) -> list[dict[str, str]]:
    concepts = refinement.concepts
    adjacent = refinement.adjacent_concepts
    anchors = refinement.anchors
    suffix = _qualifiers(request)
    result = []
    for term in concepts:
        result.append({"query": f"{_quote(term)} in:name,description,topics,readme {suffix}", "kind": "refinement"})
    for left in concepts[:3]:
        for right in anchors[:3]:
            result.append({"query": f"{_quote(left)} {_quote(right)} in:readme {suffix}", "kind": "anchor"})
    for term in adjacent:
        result.append({"query": f"{_quote(term)} in:name,description,topics,readme {suffix}", "kind": "adjacent"})
    unique = {item["query"]: item for item in result}
    return list(unique.values())[:limit]


def reverse_reference_query(full_name: str, request: SearchRequest | None = None) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.\-/]", "", full_name)
    suffix = _qualifiers(request) if request else "is:public archived:false"
    return f'"{safe}" in:readme {suffix}'


def code_filename_query(filename: str, concept: str | None = None) -> str:
    query = f"is:public filename:{filename}"
    return query + (f" {_quote(concept)}" if concept else "")
