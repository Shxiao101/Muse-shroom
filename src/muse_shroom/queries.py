from __future__ import annotations

import re
from typing import Any, Iterable

from .models import Concept, Refinement, SearchHypothesis, SearchRequest


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


def _search_terms(concept: Concept, *, allow_generic: bool = False) -> list[str]:
    values = []
    for term in concept.terms():
        if allow_generic or not is_generic_term(term):
            values.append(term)
    return values


def indexed_groups(concepts: Iterable[Concept], prefix: str,
                   *, allow_generic: bool = False) -> list[tuple[str, Concept, list[str]]]:
    groups = []
    for index, concept in enumerate(concepts):
        terms = _search_terms(concept, allow_generic=allow_generic)
        if terms:
            groups.append((f"{prefix}:{index}", concept, terms))
    return groups


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


def _take(result: list[dict[str, Any]], seen: set[str],
          bucket: list[tuple[str, str, str, str, str]], limit: int,
          *, n: int | None = None) -> None:
    added = 0
    for query, kind, sort, concept_id, term in bucket:
        if len(result) >= limit:
            return
        if n is not None and added >= n:
            return
        normalized = " ".join(query.split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append({
            "query": normalized, "kind": kind, "sort": sort,
            "concept_id": concept_id, "term": term,
        })
        added += 1


def _build_legacy_queries(request: SearchRequest, limit: int = 12) -> list[dict[str, Any]]:
    """Build validated repository-search queries; agents never construct GitHub syntax."""
    core_groups = indexed_groups(request.core_concepts, "core")
    adjacent_groups = indexed_groups(request.adjacent_concepts, "adjacent")
    type_terms: list[str] = []
    for artifact_type in request.artifact_types:
        type_terms.extend(TYPE_TERMS.get(artifact_type, [artifact_type]))
    suffix = _qualifiers(request)
    lefts = [(terms[0], concept_id) for concept_id, _concept, terms in (core_groups[:2] or adjacent_groups[:2])]
    primary_term = core_groups[0][2][0] if core_groups else (adjacent_groups[0][2][0] if adjacent_groups else "")
    primary_id = core_groups[0][0] if core_groups else (adjacent_groups[0][0] if adjacent_groups else "")
    primary = _quote(primary_term)

    core_queries: list[tuple[str, str, str, str, str]] = []
    for concept_id, _concept, terms in core_groups[:3]:
        core_queries.append((
            f"{_quote(terms[0])} in:name,description,topics,readme {suffix}",
            "core", "stars", concept_id, terms[0],
        ))

    typed_queries: list[tuple[str, str, str, str, str]] = []
    rights = type_terms[:2] or ["tool"]
    for right in rights[:1]:
        for left, concept_id in lefts:
            if _typed_redundant(left, right):
                continue
            typed_queries.append((
                f"{_quote(left)} {_quote(right)} in:name,description,topics,readme {suffix}",
                "typed", "stars", concept_id, left,
            ))
    for right in rights[1:]:
        for left, concept_id in lefts:
            if _typed_redundant(left, right):
                continue
            typed_queries.append((
                f"{_quote(left)} {_quote(right)} in:name,description,topics,readme {suffix}",
                "typed", "stars", concept_id, left,
            ))

    alias_queries: list[tuple[str, str, str, str, str]] = []
    for concept_id, _concept, terms in core_groups:
        for term in terms[1:2]:
            alias_queries.append((
                f"{_quote(term)} in:name,description,topics,readme {suffix}",
                "core", "stars", concept_id, term,
            ))
    for concept_id, _concept, terms in adjacent_groups:
        for term in terms[1:2]:
            alias_queries.append((
                f"{_quote(term)} in:name,description,topics,readme {suffix}",
                "adjacent", "stars", concept_id, term,
            ))
    alias_typed: list[tuple[str, str, str, str, str]] = []
    for concept_id, _concept, terms in core_groups:
        for term in terms[1:2]:
            for right in type_terms[:1] or ["tool"]:
                if _typed_redundant(term, right):
                    continue
                alias_typed.append((
                    f"{_quote(term)} {_quote(right)} in:name,description,topics,readme {suffix}",
                    "typed", "stars", concept_id, term,
                ))

    gem_queries: list[tuple[str, str, str, str, str]] = []
    if primary:
        gem_queries.extend([
            (f"{primary} in:name,description,topics,readme stars:1..500 {suffix}",
             "gem", "updated", primary_id, primary_term),
            (f"{primary} in:name,description,topics,readme stars:0..50 {suffix}",
             "gem", "updated", primary_id, primary_term),
        ])

    adjacent_queries: list[tuple[str, str, str, str, str]] = []
    for concept_id, _concept, terms in adjacent_groups[:3]:
        adjacent_queries.append((
            f"{_quote(terms[0])} in:name,description,topics,readme {suffix}",
            "adjacent", "stars", concept_id, terms[0],
        ))
    for concept_id, _concept, terms in core_groups[:2]:
        for adj_id, _adj, adj_terms in adjacent_groups[:3]:
            adjacent_queries.append((
                f"{_quote(terms[0])} {_quote(adj_terms[0])} in:name,description,topics,readme {suffix}",
                "adjacent", "stars", adj_id, adj_terms[0],
            ))
    adjacent_quota = min(3, 2 + round(request.exploration_level)) if adjacent_groups else 0

    surface_queries: list[tuple[str, str, str, str, str]] = []
    if primary:
        for scope, kind in (
            ("name,description", "core"), ("topics", "core"), ("readme", "core"),
            ("name,description,topics", "core"),
        ):
            surface_queries.append((
                f"{primary} in:{scope} {suffix}", kind, "stars", primary_id, primary_term,
            ))
        for companion in ("tool", "app", "plugin"):
            if _typed_redundant(primary_term, companion):
                continue
            surface_queries.append((
                f"{primary} {_quote(companion)} in:name,description,topics,readme {suffix}",
                "typed", "stars", primary_id, primary_term,
            ))

    n_core = min(3, len(core_queries))
    n_gem = min(2, len(gem_queries))
    n_adj = min(adjacent_quota, len(adjacent_queries))
    room = max(0, limit - n_core - n_gem - n_adj)
    if alias_queries and room:
        n_alias = min(len(alias_queries), room if room <= 2 else max(1, room - 2))
    else:
        n_alias = 0

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    _take(result, seen, core_queries, limit, n=n_core)
    _take(result, seen, gem_queries, limit, n=n_gem)
    _take(result, seen, adjacent_queries, limit, n=n_adj)
    _take(result, seen, alias_queries, limit, n=n_alias)
    _take(result, seen, typed_queries, limit, n=3)
    _take(result, seen, alias_queries, limit)
    _take(result, seen, alias_typed, limit)
    _take(result, seen, adjacent_queries, limit)
    _take(result, seen, surface_queries, limit)
    return result


def build_queries(request: SearchRequest, limit: int = 12) -> list[dict[str, Any]]:
    """Build a bounded plan with explicit v0.4 problem/mechanism/exploration sources."""
    if request.legacy_schema:
        return _build_legacy_queries(request, limit)

    suffix = _qualifiers(request)
    problem_groups = indexed_groups(request.problem_concepts, "core")
    mechanism_groups = [
        (f"core:{len(request.problem_concepts) + index}", concept, _search_terms(concept))
        for index, concept in enumerate(request.mechanisms)
        if _search_terms(concept)
    ]
    exploration_groups = indexed_groups(request.exploration_directions, "adjacent")
    type_terms: list[str] = []
    for artifact_type in request.artifact_types:
        type_terms.extend(TYPE_TERMS.get(artifact_type, [artifact_type]))

    def bucket(groups: list[tuple[str, Concept, list[str]]], kind: str,
               *, aliases: bool) -> list[tuple[str, str, str, str, str]]:
        values: list[tuple[str, str, str, str, str]] = []
        for concept_id, _concept, terms in groups:
            selected_terms = terms if aliases else terms[:1]
            for term in selected_terms:
                values.append((
                    f"{_quote(term)} in:name,description,topics,readme {suffix}",
                    kind, "stars", concept_id, term,
                ))
        return values

    problem = bucket(problem_groups, "problem", aliases=False)
    mechanisms = bucket(mechanism_groups, "mechanism", aliases=True)
    exploration = bucket(exploration_groups, "exploration", aliases=True)

    primary_term = problem_groups[0][2][0] if problem_groups else ""
    primary_id = problem_groups[0][0] if problem_groups else ""
    gem = []
    if primary_term:
        primary = _quote(primary_term)
        gem = [
            (f"{primary} in:name,description,topics,readme stars:1..500 {suffix}",
             "gem", "updated", primary_id, primary_term),
            (f"{primary} in:name,description,topics,readme stars:0..50 {suffix}",
             "gem", "updated", primary_id, primary_term),
        ]

    typed: list[tuple[str, str, str, str, str]] = []
    left_groups = problem_groups[:2] + mechanism_groups[:2]
    for concept_id, _concept, terms in left_groups:
        for right in (type_terms[:2] or ["tool"]):
            if _typed_redundant(terms[0], right):
                continue
            typed.append((
                f"{_quote(terms[0])} {_quote(right)} in:name,description,topics,readme {suffix}",
                "typed", "stars", concept_id, terms[0],
            ))

    seen: set[str] = set()
    result: list[dict[str, Any]] = []

    def take(values: list[tuple[str, str, str, str, str]], n: int | None = None) -> None:
        before = len(result)
        _take(result, seen, values, limit, n=n)
        lane_by_kind = {
            "problem": "core", "mechanism": "core", "exploration": "adjacent",
            "typed": "typed", "gem": "gem",
        }
        for item in result[before:]:
            item["lane_kind"] = lane_by_kind[item["kind"]]

    # Reserve distinct sources first; remaining slots admit all mechanism aliases.
    take(problem, min(3, len(problem)))
    take(mechanisms, min(4, len(mechanisms)))
    take(exploration, min(3, len(exploration)))
    take(gem, min(2, len(gem)))
    take(typed)
    take(mechanisms)
    take(exploration)
    take(problem)
    return result


def query_fingerprint(query: str) -> str:
    tokens = [token.strip().strip('"').casefold() for token in query.split()]
    return " ".join(sorted(token for token in tokens if token))


def term_blocked_by_negative(term: str, negatives: Iterable[str]) -> bool:
    from .boundary import _contains_normalized, _normalized

    needle = _normalized(term)
    if not needle:
        return True
    for raw in negatives:
        negative = _normalized(str(raw))
        if not negative:
            continue
        if needle == negative:
            return True
        if _contains_normalized(negative, needle) or _contains_normalized(needle, negative):
            return True
    return False


def hypothesis_queries(hypothesis: SearchHypothesis, request: SearchRequest,
                       *, negatives: Iterable[str] = (),
                       known_fingerprints: Iterable[str] = (),
                       limit: int = 6) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build this-round keyword queries, skipping negatives and historical duplicates."""
    suffix = _qualifiers(request)
    blocked = list(dict.fromkeys(str(value).strip() for value in negatives if str(value).strip()))
    known = set(known_fingerprints)
    planned: list[dict[str, Any]] = []

    def add(term: str, kind: str, concept_id: str) -> None:
        clean = term.strip()
        if not clean or term_blocked_by_negative(clean, blocked):
            return
        quoted = _quote(clean)
        if not quoted:
            return
        query = f"{quoted} in:name,description,topics,readme {suffix}"
        normalized = " ".join(query.split())
        item = {
            "query": normalized, "kind": kind, "sort": "stars",
            "concept_id": concept_id, "term": clean,
            "lane_kind": "adjacent" if kind in {"adjacent", "exploration"} else "core",
            "fingerprint": query_fingerprint(normalized),
        }
        if any(existing["query"] == item["query"] for existing in planned):
            return
        planned.append(item)

    if hypothesis.target_mechanism:
        add(hypothesis.target_mechanism, "refinement", "hypothesis:mechanism")
    if hypothesis.target_direction:
        add(hypothesis.target_direction, "adjacent", "hypothesis:direction")
    for index, term in enumerate(hypothesis.concepts):
        add(term, "refinement", f"refinement:{index}")
    for index, term in enumerate(hypothesis.aliases):
        add(term, "refinement", f"hypothesis:alias:{index}")
    for index, term in enumerate(hypothesis.adjacent_concepts):
        add(term, "adjacent", f"adjacent:{index}")
    for index, term in enumerate(hypothesis.promote_discovered_terms):
        add(term, "adjacent", f"hypothesis:discovered:{index}")
    for index, addition in enumerate(hypothesis.add_exploration_directions):
        add(addition.term, "adjacent", f"hypothesis:exploration:{index}")
    for left in (hypothesis.concepts[:3] or ([hypothesis.target_mechanism] if hypothesis.target_mechanism else [])):
        for right in hypothesis.anchors[:3]:
            if not left or term_blocked_by_negative(left, blocked) or term_blocked_by_negative(right, blocked):
                continue
            query = f"{_quote(left)} {_quote(right)} in:readme {suffix}"
            normalized = " ".join(query.split())
            item = {
                "query": normalized, "kind": "anchor", "sort": "stars",
                "concept_id": "hypothesis:anchor", "term": left,
                "lane_kind": "core", "fingerprint": query_fingerprint(normalized),
            }
            if not any(existing["query"] == item["query"] for existing in planned):
                planned.append(item)

    executed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in planned:
        if item["fingerprint"] in known:
            skipped.append({**item, "skipped": True, "skip_reason": "duplicate"})
            continue
        if len(executed) >= limit:
            skipped.append({**item, "skipped": True, "skip_reason": "round_budget"})
            continue
        known.add(item["fingerprint"])
        executed.append(item)
    return executed, skipped


def confirmation_queries(candidate: str, request: SearchRequest, *,
                         anchors: Iterable[str] = (), seed_repos: Iterable[str] = (),
                         known_fingerprints: Iterable[str] = (),
                         limit: int = 3) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build bounded candidate-to-problem queries for mechanism confirmation."""
    term = candidate.strip()
    if not term or limit <= 0:
        return [], []
    suffix = _qualifiers(request)
    contexts: list[tuple[str, str]] = []
    seen_contexts: set[str] = set()
    term_key = " ".join(term.casefold().split())

    def add_context(raw: str, kind: str) -> None:
        value = str(raw).strip()
        key = " ".join(value.casefold().split())
        if not value or not key or key == term_key or key in seen_contexts:
            return
        seen_contexts.add(key)
        contexts.append((value, kind))

    problem = next(
        (concept.term for concept in request.problem_concepts if concept.term.strip()), "",
    )
    add_context(problem, "confirmation_problem")
    anchor = next((
        str(value) for value in anchors
        if str(value).strip() and " ".join(str(value).casefold().split()) != term_key
    ), "")
    add_context(anchor, "confirmation_anchor")
    seed = next((str(value) for value in seed_repos if "/" in str(value)), "")
    add_context(seed, "confirmation_seed")

    planned: list[dict[str, Any]] = []
    for context, kind in contexts[:3]:
        query = " ".join(
            f'{_quote(term)} {_quote(context)} in:name,description,topics,readme {suffix}'.split()
        )
        planned.append({
            "query": query,
            "kind": kind,
            "sort": "stars",
            "concept_id": f"confirmation:{term_key}",
            "term": term,
            "lane_kind": "adjacent",
            "fingerprint": query_fingerprint(query),
        })

    known = set(known_fingerprints)
    executed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in planned:
        if item["fingerprint"] in known:
            skipped.append({**item, "skipped": True, "skip_reason": "duplicate"})
            continue
        if len(executed) >= limit:
            skipped.append({**item, "skipped": True, "skip_reason": "confirmation_budget"})
            continue
        known.add(item["fingerprint"])
        executed.append(item)
    return executed, skipped


def refinement_queries(refinement: Refinement, request: SearchRequest,
                       limit: int = 10) -> list[dict[str, str]]:
    concepts = refinement.concepts
    adjacent = refinement.adjacent_concepts
    anchors = refinement.anchors
    suffix = _qualifiers(request)
    result = []
    for index, term in enumerate(concepts):
        result.append({
            "query": f"{_quote(term)} in:name,description,topics,readme {suffix}",
            "kind": "refinement", "concept_id": f"refinement:{index}", "term": term,
        })
    for left in concepts[:3]:
        for right in anchors[:3]:
            result.append({
                "query": f"{_quote(left)} {_quote(right)} in:readme {suffix}",
                "kind": "anchor", "concept_id": f"refinement:{concepts.index(left)}", "term": left,
            })
    for index, term in enumerate(adjacent):
        result.append({
            "query": f"{_quote(term)} in:name,description,topics,readme {suffix}",
            "kind": "adjacent", "concept_id": f"adjacent:{index}", "term": term,
        })
    unique = {item["query"]: item for item in result}
    return list(unique.values())[:limit]


def reverse_reference_query(full_name: str, request: SearchRequest | None = None) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.\-/]", "", full_name)
    suffix = _qualifiers(request) if request else "is:public archived:false"
    return f'"{safe}" in:readme {suffix}'


def code_filename_query(filename: str, concept: str | None = None) -> str:
    query = f"is:public filename:{filename}"
    return query + (f" {_quote(concept)}" if concept else "")
