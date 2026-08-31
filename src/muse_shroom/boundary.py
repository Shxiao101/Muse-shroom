from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable

from .models import BoundaryDelta, Concept, SearchBoundary, SearchRequest


TOKEN_RE = re.compile(r"[A-Za-z0-9_+#]+|[\u3400-\u9fff]+")
GENERIC_DISCOVERED_TERMS = {
    "agent", "ai", "app", "application", "apps", "awesome", "awesome list",
    "github", "library", "llm", "open source", "plugin", "project", "python",
    "sdk", "software", "tool", "tools",
}
TECHNOLOGY_DISCOVERED_TERMS = {
    "android", "api", "c", "c++", "cli", "css", "docker", "electron",
    "go", "html", "javascript", "kotlin", "linux", "macos", "nodejs",
    "php", "react", "ruby", "rust", "swift", "typescript", "windows",
}
MECHANISM_HINTS = {
    "accountability", "automation", "biofeedback", "blocker", "blocking",
    "commitment", "dashboard", "economics", "feedback", "friction", "habit",
    "interface", "intervention", "minimalism", "monitoring", "notification",
    "pacing", "pomodoro", "reminder", "sensemaking", "tracking", "visualization",
    "wellbeing", "timer", "workflow",
}
# Optional precision aids only. This is not a complete mechanism vocabulary and
# must never be extended from holdout benchmark answers.
DISCOVERY_PHRASE_HINTS = {
    "accountability", "audio reactive", "behavior design", "behavioral economics",
    "behavioral friction", "biofeedback", "change impact", "commitment device",
    "creative coding", "decision hygiene", "decision record", "digital minimalism",
    "digital wellbeing", "environmental cue", "feedback loop", "focus mode",
    "habit tracking", "hardware controller", "implementation minimalism",
    "knowledge graph", "local first", "memory timeline", "music visualization",
    "notification intervention", "physical environment", "progress visualization",
    "progressive summarization", "replacement behavior", "reward schedule",
    "risk visualization", "screen time", "sensemaking", "social accountability",
    "spaced repetition", "tangible interface", "usage tracking", "visual feedback",
    "website blocker",
}
GENERIC_MECHANISM_SUFFIXES = {
    "archive", "auditing", "feedback", "friction", "graph", "history",
    "journal", "mapping", "monitoring", "replay", "review", "timeline",
    "tracking", "visualization",
}
CURATED_README_SNIPPETS = {
    "overview", "features", "use_cases", "motivation", "philosophy",
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


def _structured_phrases(text: Any) -> list[str]:
    normalized = _normalized(str(text or ""))
    tokens = normalized.split()
    generic = {
        " ".join(tokens[start:end])
        for end in range(1, len(tokens) + 1)
        if tokens[end - 1] in GENERIC_MECHANISM_SUFFIXES
        for start in range(max(0, end - 3), end - 1)
    }
    hinted = {
        phrase for phrase in DISCOVERY_PHRASE_HINTS
        if _contains_normalized(normalized, phrase)
    }
    return sorted(hinted | generic)


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
    evidence_by_id = {
        str(item.get("id")): item for item in candidate.get("evidence") or []
        if item.get("kind") != "mechanism_match"
    }
    mechanism_facts: list[dict[str, Any]] = []

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

        primary = unique_matches[0]
        mechanisms.append({
            "name": name, "role": role,
            "matched_terms": list(dict.fromkeys(str(item["matched_term"]) for item in unique_matches)),
            "sources": list(dict.fromkeys(str(item["source"]) for item in unique_matches)),
        })
        mechanism_facts.append({
            "mechanism": name, "role": role,
            "source_field": primary["source"],
            "matched_term": primary["matched_term"],
            "text": primary["text"],
            "source": (
                f"https://github.com/{candidate.get('full_name')}#readme"
                if primary["source"] == "readme" else candidate.get("html_url")
            ),
            **({"line_start": primary["line_start"]} if primary.get("line_start") else {}),
            **({"sha": candidate.get("readme_sha")} if primary["source"] == "readme" else {}),
            "untrusted_source": primary["source"] == "readme",
        })

    if mechanisms:
        evidence_id = f"repo:{full_name}:mechanisms"
        evidence_by_id[evidence_id] = {
            "id": evidence_id,
            "kind": "mechanism_match",
            "source": candidate.get("html_url"),
            "facts": {
                "mechanisms": mechanism_facts,
                "untrusted_source": any(
                    bool(fact.get("untrusted_source")) for fact in mechanism_facts
                ),
            },
        }
        for mechanism in mechanisms:
            mechanism["evidence_ids"] = [evidence_id]

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


def mechanism_names_by_role(candidates: Iterable[dict[str, Any]], role: str) -> list[str]:
    names = {
        str(mechanism.get("name"))
        for candidate in candidates
        for mechanism in candidate.get("mechanisms") or []
        if mechanism.get("role") == role and str(mechanism.get("name") or "").strip()
    }
    return sorted(names, key=str.casefold)


def discovered_term_evidence(candidates: Iterable[dict[str, Any]], request: SearchRequest,
                             limit: int = 8) -> list[dict[str, Any]]:
    """Return deterministic, source-backed terms that may expand the boundary."""
    candidate_list = list(candidates)
    known = {
        _normalized(term)
        for concept in (
            request.problem_concepts + request.mechanisms + request.exploration_directions
        )
        for term in concept.terms()
    }
    exclusions = {
        _normalized(value) for value in request.exclusions if _normalized(value)
    }
    support_repos: dict[str, set[str]] = {}
    display: dict[str, str] = {}
    sources: dict[str, list[dict[str, Any]]] = {}

    def add_source(raw: str, candidate: dict[str, Any], *, source_field: str,
                   evidence_id: str, evidence_text: str, confidence: float,
                   relationship_backed: bool = False) -> None:
        value = " ".join(str(raw).replace("-", " ").split()).strip()
        key = _normalized(value)
        if (
            not key or key in known or key in GENERIC_DISCOVERED_TERMS
            or key in TECHNOLOGY_DISCOVERED_TERMS or len(key) < 3 or len(key) > 80
            or any(_contains_normalized(key, exclusion) for exclusion in exclusions)
        ):
            return
        repo = str(candidate.get("full_name") or "").strip()
        if not repo:
            return
        display.setdefault(key, value)
        support_repos.setdefault(key, set()).add(repo.casefold())
        source = {
            "repo": repo,
            "source_field": source_field,
            "evidence_id": evidence_id,
            "evidence_text": " ".join(str(evidence_text).split())[:240],
            "confidence": confidence,
            "relationship_backed": relationship_backed,
        }
        identity = (repo.casefold(), source_field, evidence_id, source["evidence_text"])
        existing = {
            (
                str(item["repo"]).casefold(), item["source_field"],
                item["evidence_id"], item["evidence_text"],
            )
            for item in sources.setdefault(key, [])
        }
        if identity not in existing:
            sources[key].append(source)

    for candidate in candidate_list:
        repo = str(candidate.get("full_name") or "").strip()
        metadata = next(
            (
                item for item in candidate.get("evidence") or []
                if item.get("kind") == "github_metadata"
            ),
            None,
        )
        if metadata is None:
            continue
        relationship_backed = any(
            path.get("kind") == "relationship"
            for path in candidate.get("discovery_paths") or []
        )
        for raw in candidate.get("topics") or []:
            add_source(
                str(raw), candidate, source_field="topics",
                evidence_id=str(metadata.get("id") or ""), evidence_text=str(raw),
                confidence=.95, relationship_backed=relationship_backed,
            )
        description = str(candidate.get("description") or "")
        for phrase in _structured_phrases(description):
            add_source(
                phrase, candidate, source_field="description",
                evidence_id=str(metadata.get("id") or ""), evidence_text=description,
                confidence=.82, relationship_backed=relationship_backed,
            )
        for evidence in candidate.get("evidence") or []:
            if evidence.get("kind") != "readme_excerpt":
                continue
            facts = evidence.get("facts") or {}
            if facts.get("snippet_type") not in CURATED_README_SNIPPETS:
                continue
            text = str(facts.get("text") or "")
            for phrase in _structured_phrases(text):
                add_source(
                    phrase, candidate, source_field=f"readme_{facts.get('snippet_type')}",
                    evidence_id=str(evidence.get("id") or ""), evidence_text=text,
                    confidence=.86, relationship_backed=relationship_backed,
                )
        evidence_by_id = {
            str(item.get("id") or ""): item for item in candidate.get("evidence") or []
        }
        for path in candidate.get("discovery_paths") or []:
            if path.get("kind") != "relationship":
                continue
            detail = str(path.get("detail") or "")
            relation_id = f"relation:{str(path.get('from') or '').lower()}:{path.get('relation')}:{repo.lower()}"
            relation = evidence_by_id.get(relation_id) or {}
            for phrase in _structured_phrases(detail):
                add_source(
                    phrase, candidate, source_field="relationship_detail",
                    evidence_id=str(relation.get("id") or relation_id), evidence_text=detail,
                    confidence=.78, relationship_backed=True,
                )
    ranked_all = sorted(
        support_repos,
        key=lambda key: (
            -max(float(item["confidence"]) for item in sources[key]),
            -len(support_repos[key]), key,
        ),
    )
    non_topic = [
        key for key in ranked_all
        if any(source["source_field"] != "topics" for source in sources[key])
    ]
    ranked = list(dict.fromkeys([*ranked_all[:4], *non_topic, *ranked_all]))
    result: list[dict[str, Any]] = []
    for key in ranked[:limit]:
        tokens = set(key.split())
        kind = "candidate_mechanism" if tokens & MECHANISM_HINTS else "project_category"
        if any(source["relationship_backed"] for source in sources[key]):
            kind = "cross_domain_direction"
        result.append({
            "term": display[key],
            "kind": kind,
            "confidence": max(float(item["confidence"]) for item in sources[key]),
            "support_count": len(support_repos[key]),
            "sources": [
                {name: value for name, value in source.items() if name != "relationship_backed"}
                for source in sources[key][:3]
            ],
        })
    return result


def discovered_terms(candidates: Iterable[dict[str, Any]], request: SearchRequest,
                     limit: int = 8) -> list[str]:
    return [
        str(item["term"])
        for item in discovered_term_evidence(candidates, request, limit=limit)
    ]


def mechanism_distribution(candidates: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    for candidate in candidates:
        for mechanism in candidate.get("mechanisms") or []:
            name = str(mechanism.get("name") or "").strip()
            if not name:
                continue
            key = name.casefold()
            counts[key] += 1
            display.setdefault(key, name)
    return {
        display[key]: counts[key]
        for key in sorted(counts, key=lambda item: (-counts[item], item))
    }


def build_boundary(candidates: Iterable[dict[str, Any]], presented: Iterable[dict[str, Any]],
                   request: SearchRequest, *, rejected_directions: Iterable[str] = (),
                   negative_directions: Iterable[str] = ()) -> SearchBoundary:
    candidate_list = list(candidates)
    presented_list = list(presented)
    recalled = mechanism_names(candidate_list)
    presented_names = mechanism_names(presented_list)
    recalled_keys = {value.casefold() for value in recalled}
    rejected = list(dict.fromkeys(str(value).strip() for value in rejected_directions if str(value).strip()))
    negatives = list(dict.fromkeys(str(value).strip() for value in negative_directions if str(value).strip()))
    rejected_keys = {value.casefold() for value in rejected}
    negative_keys = {value.casefold() for value in negatives}
    blocked_keys = rejected_keys | negative_keys
    explored = [
        concept.term for concept in request.exploration_directions
        if concept.term.casefold() in recalled_keys and concept.term.casefold() not in blocked_keys
    ]
    unexplored = [
        concept.term for concept in request.exploration_directions
        if concept.term.casefold() not in recalled_keys and concept.term.casefold() not in blocked_keys
    ]
    term_evidence = discovered_term_evidence(candidate_list, request)
    return SearchBoundary(
        recalled_mechanisms=recalled,
        presented_mechanisms=presented_names,
        mechanism_origins={
            "requested_mechanisms": mechanism_names_by_role(candidate_list, "mechanism"),
            "confirmed_exploration_directions": mechanism_names_by_role(
                candidate_list, "exploration"
            ),
        },
        explored_directions=explored,
        unexplored_directions=unexplored,
        rejected_directions=rejected,
        discovered_terms=[str(item["term"]) for item in term_evidence],
        discovered_term_evidence=term_evidence,
        negative_directions=negatives,
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
