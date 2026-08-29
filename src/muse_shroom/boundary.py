from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any, Iterable

from .models import BoundaryDelta, Concept, SearchBoundary, SearchRequest


TOKEN_RE = re.compile(r"[A-Za-z0-9_+#]+|[\u3400-\u9fff]+")
GENERIC_DISCOVERED_TERMS = {
    "ai", "app", "application", "apps", "github", "library", "open source",
    "plugin", "project", "python", "sdk", "software", "tool", "tools",
}


def _normalized(value: str) -> str:
    return " ".join(TOKEN_RE.findall(value.casefold()))


def _contains_normalized(haystack: str, needle: str) -> bool:
    if not needle or not haystack:
        return False
    if re.search(r"[\u3400-\u9fff]", needle):
        return needle.replace(" ", "") in haystack.replace(" ", "")
    return f" {needle} " in f" {haystack} "


def _contains(surface: str, term: str) -> bool:
    return _contains_normalized(_normalized(surface), _normalized(term))


def _mechanism_concepts(request: SearchRequest) -> list[tuple[str, Concept, str]]:
    result: list[tuple[str, Concept, str]] = []
    seen: set[str] = set()
    for role, concepts in (
        ("mechanism", request.mechanisms),
        ("exploration", request.exploration_directions),
    ):
        for concept in concepts:
            key = concept.term.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append((concept.term, concept, role))
    return result


def _readme_match(lines: list[tuple[int, str, str]], term: str) -> tuple[str, int] | None:
    needle = _normalized(term)
    for index, line, normalized in lines:
        if _contains_normalized(normalized, needle):
            text = " ".join(line.strip().split())[:220]
            if text:
                return text, index
    return None


def annotate_candidate_mechanisms(candidate: dict[str, Any], request: SearchRequest) -> None:
    """Attach only mechanism labels supported by description, topics, or README text."""
    description = str(candidate.get("description") or "")
    topics = [str(value) for value in candidate.get("topics") or []]
    readme = str(candidate.get("readme") or "")
    readme_lines = [
        (index, line, _normalized(line))
        for index, line in enumerate(readme.splitlines(), 1)
    ]
    full_name = str(candidate.get("full_name") or "").lower()
    mechanisms: list[dict[str, Any]] = []
    evidence_by_id = {str(item.get("id")): item for item in candidate.get("evidence") or []}

    for name, concept, role in _mechanism_concepts(request):
        matches: list[dict[str, Any]] = []
        for term in concept.terms():
            readme_hit = _readme_match(readme_lines, term)
            if readme_hit:
                text, line = readme_hit
                matches.append({
                    "source": "readme", "matched_term": term, "text": text,
                    "line_start": line,
                })
            if _contains(description, term):
                matches.append({
                    "source": "description", "matched_term": term,
                    "text": " ".join(description.split())[:220],
                })
            topic = next((value for value in topics if _contains(value.replace("-", " "), term)), None)
            if topic is not None:
                matches.append({"source": "topics", "matched_term": term, "text": topic})
        unique_matches: list[dict[str, Any]] = []
        identities: set[tuple[str, str]] = set()
        for match in matches:
            identity = (str(match["source"]), str(match["matched_term"]).casefold())
            if identity in identities:
                continue
            identities.add(identity)
            unique_matches.append(match)
        if not unique_matches:
            continue

        digest = hashlib.sha1(name.casefold().encode("utf-8")).hexdigest()[:10]
        evidence_id = f"repo:{full_name}:mechanism:{digest}"
        primary = unique_matches[0]
        evidence_by_id[evidence_id] = {
            "id": evidence_id,
            "kind": "mechanism_match",
            "source": (
                f"https://github.com/{candidate.get('full_name')}#readme"
                if primary["source"] == "readme" else candidate.get("html_url")
            ),
            "facts": {
                "mechanism": name,
                "role": role,
                "source_field": primary["source"],
                "matched_term": primary["matched_term"],
                "text": primary["text"],
                **({"line_start": primary["line_start"]} if primary.get("line_start") else {}),
                **({"sha": candidate.get("readme_sha")} if primary["source"] == "readme" else {}),
                "untrusted_source": primary["source"] == "readme",
            },
        }
        mechanisms.append({
            "name": name, "role": role, "evidence_ids": [evidence_id],
            "matched_terms": list(dict.fromkeys(str(item["matched_term"]) for item in unique_matches)),
            "sources": list(dict.fromkeys(str(item["source"]) for item in unique_matches)),
            "evidence": {
                "source": primary["source"], "matched_term": primary["matched_term"],
                "text": primary["text"],
                **({"line_start": primary["line_start"]} if primary.get("line_start") else {}),
                **({"sha": candidate.get("readme_sha")} if primary["source"] == "readme" else {}),
                "untrusted_source": primary["source"] == "readme",
            },
        })

    candidate["mechanisms"] = mechanisms
    candidate["evidence"] = list(evidence_by_id.values())


def mechanism_names(candidates: Iterable[dict[str, Any]]) -> list[str]:
    names = {
        str(mechanism.get("name"))
        for candidate in candidates
        for mechanism in candidate.get("mechanisms") or []
        if str(mechanism.get("name") or "").strip()
    }
    return sorted(names, key=str.casefold)


def discovered_terms(candidates: Iterable[dict[str, Any]], request: SearchRequest,
                     limit: int = 8) -> list[str]:
    known = {
        _normalized(term)
        for concept in (
            request.problem_concepts + request.mechanisms + request.exploration_directions
        )
        for term in concept.terms()
    }
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    for candidate in candidates:
        if not candidate.get("mechanisms"):
            continue
        for raw in candidate.get("topics") or []:
            value = " ".join(str(raw).replace("-", " ").split()).strip()
            key = _normalized(value)
            if (
                not key or key in known or key in GENERIC_DISCOVERED_TERMS
                or len(key) < 3 or len(key) > 80
            ):
                continue
            counts[key] += 1
            display.setdefault(key, value)
    ranked = sorted(counts, key=lambda key: (-counts[key], key))
    return [display[key] for key in ranked[:limit]]


def build_boundary(candidates: Iterable[dict[str, Any]], presented: Iterable[dict[str, Any]],
                   request: SearchRequest, *, rejected_directions: Iterable[str] = ()) -> SearchBoundary:
    candidate_list = list(candidates)
    presented_list = list(presented)
    recalled = mechanism_names(candidate_list)
    presented_names = mechanism_names(presented_list)
    recalled_keys = {value.casefold() for value in recalled}
    rejected = list(dict.fromkeys(str(value).strip() for value in rejected_directions if str(value).strip()))
    rejected_keys = {value.casefold() for value in rejected}
    explored = [
        concept.term for concept in request.exploration_directions
        if concept.term.casefold() in recalled_keys and concept.term.casefold() not in rejected_keys
    ]
    unexplored = [
        concept.term for concept in request.exploration_directions
        if concept.term.casefold() not in recalled_keys and concept.term.casefold() not in rejected_keys
    ]
    return SearchBoundary(
        recalled_mechanisms=recalled,
        presented_mechanisms=presented_names,
        explored_directions=explored,
        unexplored_directions=unexplored,
        rejected_directions=rejected,
        discovered_terms=discovered_terms(candidate_list, request),
    )


def boundary_delta(current: dict[str, Any], previous: dict[str, Any] | None) -> BoundaryDelta:
    previous = previous or {}

    def new_values(name: str) -> list[str]:
        old = {str(value).casefold() for value in previous.get(name) or []}
        return [str(value) for value in current.get(name) or [] if str(value).casefold() not in old]

    return BoundaryDelta(
        new_mechanisms=new_values("recalled_mechanisms"),
        new_presented_mechanisms=new_values("presented_mechanisms"),
        new_directions=new_values("explored_directions"),
        new_terms=new_values("discovered_terms"),
    )
