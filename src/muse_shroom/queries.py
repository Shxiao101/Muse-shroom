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
GENERIC_TYPE_TOKENS = {"skill", "skills", "tool", "tools", "ai", "agent", "agents"}
CJK_RE = re.compile(r"[\u3400-\u9fff]")
LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9_+#.-]+")


def _quote(term: str) -> str:
    clean = re.sub(r"[\r\n\t]+", " ", term).replace('"', "").replace("\\", "").strip()
    if not clean:
        return ""
    return f'"{clean}"'


def _latin_tokens(term: str) -> list[str]:
    return LATIN_TOKEN_RE.findall(term)


def is_generic_term(term: str) -> bool:
    phrase = term.casefold().strip()
    if not phrase or CJK_RE.search(phrase):
        return False
    tokens = [token.casefold() for token in _latin_tokens(phrase)]
    return bool(tokens) and all(token in GENERIC_TYPE_TOKENS for token in tokens)


def _terms(concepts: Iterable[Concept], *, allow_generic: bool = False) -> list[str]:
    values = [c.term for c in sorted(concepts, key=lambda item: item.weight, reverse=True) if c.term]
    if allow_generic:
        return values
    return [term for term in values if not is_generic_term(term)]


def _typed_redundant(left: str, right: str) -> bool:
    if not left or not right:
        return True
    if right.casefold() in left.casefold():
        return True
    right_tokens = {token.casefold() for token in _latin_tokens(right)}
    left_tokens = {token.casefold() for token in _latin_tokens(left)}
    return bool(right_tokens) and right_tokens <= left_tokens


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
    lefts = core[:2] or adjacent[:2]
    primary_term = core[0] if core else (adjacent[0] if adjacent else "")
    primary = _quote(primary_term)

    raw: list[tuple[str, str, str]] = []
    for concept in core[:3]:
        raw.append((f"{_quote(concept)} in:name,description,topics,readme {suffix}", "core", "stars"))
    typed = []
    for left in lefts:
        for right in type_terms[:2] or ["tool"]:
            if _typed_redundant(left, right):
                continue
            typed.append((f"{_quote(left)} {_quote(right)} in:name,description,topics,readme {suffix}", "typed", "stars"))
    raw.extend(typed[:3])

    if primary:
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
    if primary:
        for scope, kind in (
            ("name,description", "core"), ("topics", "core"), ("readme", "core"),
            ("name,description,topics", "core"),
        ):
            raw.append((f"{primary} in:{scope} {suffix}", kind, "stars"))
        for companion in ("tool", "app", "plugin"):
            if _typed_redundant(primary_term, companion):
                continue
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
