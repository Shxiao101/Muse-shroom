from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable

from .models import BoundaryDelta, Concept, SearchBoundary, SearchRequest


TOKEN_RE = re.compile(r"[A-Za-z0-9_+#]+|[\u3400-\u9fff]+")
SEGMENT_RE = re.compile(
    r"(?:[.!?;,|:\r\n]+|\s[-–—]\s|[\U0001F000-\U0001FAFF]|(?=\s/[a-z][\w-]+\s))"
)
MECHANISM_FORM_RE = re.compile(r"(?:tion|sion|ment|ysis|isation|ization)$")
ADJECTIVE_FORM_RE = re.compile(r"(?:al|ary|ed|ic|ive|ory|ual)$")
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
REQUEST_RELEVANCE_STOPWORDS = {
    "a", "ai", "an", "and", "agent", "app", "application", "assistant",
    "coding", "creation", "for", "from", "help", "improvement", "in",
    "management", "of", "on", "or", "programmer", "project", "quality",
    "software", "the", "to", "tool", "using", "with", "workflow",
}
INCIDENTAL_CONTEXT_CUES = {
    "changelog", "contributing", "contribution guide", "dependency",
    "disclosure", "issue template", "pull request template", "release notes",
}
LIST_REPOSITORY_CUES = {
    "awesome list", "awesome-list", "curated list", "list of resources",
}
BROAD_CATEGORY_HEADS = {
    "ai", "data", "digital", "general", "project", "software", "technology",
}
GENERIC_MECHANISM_MODIFIERS = {
    "advanced", "leading", "modern", "new", "next",
}
UMBRELLA_CATEGORY_TAILS = {
    "automation", "management", "visualization", "wellbeing", "workflow",
}
BEHAVIOR_SIGNAL_TAILS = {
    "detection", "monitoring", "recognition", "sensing", "tracking",
}
INTERVENTION_TAILS = {
    "blocker", "blocking", "feedback", "friction", "intervention", "reminder",
}
WORKFLOW_PATTERN_TAILS = {
    "auditing", "checklist", "device", "log", "loop", "record", "replay",
    "schedule", "template", "training", "workflow",
}
PACKAGING_HEADS = {
    "addon", "addons", "app", "application", "applications", "apps", "boilerplate",
    "boilerplates", "bot", "bots", "client", "clients", "extension", "extensions",
    "plugin", "plugins", "starter", "starters", "theme", "themes", "wrapper",
    "wrappers",
}
PACKAGING_PHRASES = {
    "add on", "android app", "browser extension", "cli tool", "desktop app",
    "ios app", "mobile app", "web app",
}
ARTIFACT_TAILS = {
    "document", "documentation", "foundation", "graph", "implement",
    "instrument", "journal", "version",
}
NON_TRANSFERABLE_CATEGORY_TAILS = {
    "configuration", "qualification", "transportation",
}
TRANSFER_MECHANISM_TAILS = {
    "collaboration", "compression", "coordination", "decision", "execution",
    "filtering", "generation", "orchestration", "planning", "prioritization",
    "recognition", "reflection", "retrieval", "scheduling", "summarization",
    "synchronization",
}
MECHANISM_TOKEN_EQUIVALENTS = {
    "web": "browser",
}
MECHANISM_HINTS = {
    "accountability", "automation", "biofeedback", "blocker", "blocking",
    "commitment", "dashboard", "economics", "feedback", "friction", "habit",
    "interface", "intervention", "minimalism", "monitoring", "notification",
    "pacing", "pomodoro", "reminder", "sensemaking", "tracking", "visualization",
    "wellbeing", "timer", "workflow",
}
GENERIC_MECHANISM_SUFFIXES = {
    "archive", "auditing", "feedback", "friction", "graph", "history",
    "journal", "mapping", "monitoring", "replay", "review", "timeline",
    "tracking", "visualization",
}
CURATED_README_SNIPPETS = {
    "concept_match", "overview", "features", "use_cases", "motivation",
}
DISCOVERY_SOURCE_PRIORITY = {
    "readme_concept_match": 0,
    "readme_features": 0,
    "readme_use_cases": 0,
    "readme_overview": 1,
    "description": 2,
    "relationship_detail": 2,
    "readme_motivation": 3,
    "topics": 4,
}
PHRASE_BOUNDARY_TOKENS = {
    "a", "an", "and", "as", "based", "by", "for", "from", "in", "of",
    "on", "or", "the", "to", "using", "via", "with", "high", "long", "low",
    "open", "real", "self", "short",
}
VERB_BOUNDARY_TOKENS = {
    "adds", "changes", "creates", "enables", "includes", "offers", "provides",
    "supports", "uses",
}
INVALID_MECHANISM_PREFIXES = {
    "any", "around", "before", "both", "description", "each", "every",
    "feature", "free", "full", "implement", "ready", "sort", "ultimate", "your",
}
COMPOUND_MECHANISM_TAILS = {
    "item", "items", "log", "logs", "record", "records",
}
NON_MECHANISM_ENDINGS = {
    "application", "collection", "completion", "condition", "contribution",
    "conversation", "creation", "description", "development", "discussion",
    "education", "emotion", "environment", "generation", "information",
    "inspiration", "integration", "learning", "management", "missing",
    "option", "processing", "question", "recognition", "session", "solution",
    "specification", "testing", "vision", "writing",
}
CONFIRMATION_SPECIFICITY_SCORES = {
    "mechanism": 95,
    "intervention": 95,
    "workflow_pattern": 90,
    "behavioral_signal": 85,
    "project_category": 35,
    "packaging": 15,
}
CONFIRMATION_SOURCE_SCORES = {
    "readme_concept_match": 95,
    "readme_features": 90,
    "readme_use_cases": 90,
    "readme_motivation": 75,
    "readme_overview": 70,
    "description": 65,
    "topics": 40,
    "relationship_detail": 25,
}


def _normalized(value: str) -> str:
    return " ".join(TOKEN_RE.findall(value.casefold().replace("_", " ")))


def _contains_normalized(haystack: str, needle: str) -> bool:
    if not needle or not haystack:
        return False
    if re.search(r"[\u3400-\u9fff]", needle):
        return needle.replace(" ", "") in haystack.replace(" ", "")
    return f" {needle} " in f" {haystack} "


def _contains(surface: str, term: str) -> bool:
    return _contains_normalized(_normalized(surface), _normalized(term))


def _token_overlap(left: str, right: str) -> float:
    left_tokens = set(_normalized(left).split())
    right_tokens = set(_normalized(right).split())
    return len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))


def _canonical_token_key(value: str) -> str:
    return " ".join(
        MECHANISM_TOKEN_EQUIVALENTS.get(token, token)
        for token in _normalized(value).split()
    )


def _confirmation_priority(item: dict[str, Any], known_terms: Iterable[str]) -> dict[str, Any]:
    """Score confirmability and retain novelty as diagnostic telemetry."""
    candidate = str(item.get("term") or item.get("candidate") or "")
    sources = list(item.get("sources") or item.get("discovery_evidence") or [])
    core_sources = [source for source in sources if source.get("core_use_case")]
    repos = {
        str(source.get("repo") or "").casefold()
        for source in core_sources if str(source.get("repo") or "").strip()
    }
    relevance = max(0, min(100, int(item.get("evidence_relevance_score") or 0)))
    specificity = str(item.get("mechanism_specificity") or "project_category")
    specificity_score = CONFIRMATION_SPECIFICITY_SCORES.get(specificity, 25)
    source_quality = max(
        (
            CONFIRMATION_SOURCE_SCORES.get(str(source.get("source_field") or ""), 30)
            for source in sources
        ),
        default=0,
    )
    request_anchored = any(source.get("request_anchored") for source in core_sources)
    mechanism_anchored = any(source.get("mechanism_anchored") for source in core_sources)
    transfer_plausible = mechanism_anchored and specificity_score >= 85
    confirmability = round(min(100, (
        0.30 * relevance
        + 0.20 * specificity_score
        + 0.15 * source_quality
        + (15 if core_sources else 0)
        + (10 if request_anchored else 0)
        + (5 if mechanism_anchored else 0)
        + (5 if len(repos) >= 2 else 0)
        + (5 if transfer_plausible else 0)
    )))
    overlap = max(
        (_token_overlap(candidate, str(term)) for term in known_terms if str(term).strip()),
        default=0.0,
    )
    novelty = round(max(0, 100 * (1 - overlap)))
    priority = confirmability
    reasons = []
    if request_anchored:
        reasons.append("request_anchored")
    if mechanism_anchored:
        reasons.append("mechanism_anchored")
    if transfer_plausible:
        reasons.append("transfer_plausible")
    if core_sources:
        reasons.append("core_use_case")
    if len(repos) >= 2:
        reasons.append("independent_repo_support")
    if specificity_score >= 85:
        reasons.append("specific_mechanism")
    else:
        reasons.append("category_like")
    if source_quality >= 70:
        reasons.append("strong_source")
    else:
        reasons.append("weak_source")
    return {
        "novelty_score": novelty,
        "confirmability_score": confirmability,
        "confirmation_priority_score": priority,
        "confirmation_priority_reason": ",".join(reasons),
    }


def _request_relevance(text: str, concepts: Iterable[Concept]) -> tuple[bool, bool]:
    """Return exact-phrase and informative-token request relevance signals."""
    normalized = _normalized(text)
    def token_key(token: str) -> str:
        return token[:-1] if len(token) > 4 and token.endswith("s") else token

    text_tokens = {token_key(token) for token in normalized.split()}
    exact = False
    token_match = False
    for concept in concepts:
        for raw in concept.terms():
            term = _normalized(raw)
            if not term:
                continue
            if _contains_normalized(normalized, term):
                exact = True
            informative = {
                token_key(token) for token in term.split()
                if token not in REQUEST_RELEVANCE_STOPWORDS
            }
            overlap = informative & text_tokens
            if overlap and (
                len(informative) <= 1
                or len(overlap) >= 2
                or len(overlap) / len(informative) >= 0.67
            ):
                token_match = True
    return exact, token_match


def _candidate_primary_text(candidate: dict[str, Any]) -> str:
    values = [
        str(candidate.get("description") or ""),
        " ".join(str(value).replace("-", " ") for value in candidate.get("topics") or []),
    ]
    for evidence in candidate.get("evidence") or []:
        if evidence.get("kind") != "readme_excerpt":
            continue
        facts = evidence.get("facts") or {}
        if facts.get("snippet_type") in {"overview", "features", "use_cases", "motivation"}:
            values.append(str(facts.get("text") or ""))
    return " ".join(values)


def _incidental_source(candidate: dict[str, Any], source_field: str, text: str) -> tuple[bool, str]:
    normalized = _normalized(text)
    primary = _normalized(_candidate_primary_text(candidate))
    if any(_contains_normalized(normalized, _normalized(cue)) for cue in INCIDENTAL_CONTEXT_CUES):
        return True, "incidental_readme"
    if re.search(
        r"\bv?\d+\.\d+(?:\.\d+)?\b|\bpr\s*#\d+\b",
        str(text).casefold(),
    ):
        return True, "changelog_or_release"
    if any(_contains_normalized(primary, _normalized(cue)) for cue in LIST_REPOSITORY_CUES):
        return True, "list_item"
    repo_name = str(candidate.get("full_name") or "").split("/")[-1].casefold()
    topic_keys = {
        _normalized(str(value).replace("-", " "))
        for value in candidate.get("topics") or []
    }
    if repo_name.startswith("awesome-") or {"awesome", "awesome list"} & topic_keys:
        return True, "list_item"
    repo_tokens = set(_normalized(repo_name).split())
    if repo_tokens & {"awesome", "catalog", "papers", "resources"}:
        return True, "list_item"
    if re.search(
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+"
        r"(?:19|20)\d{2}\b",
        str(text).casefold(),
    ):
        return True, "changelog_or_release"
    if source_field == "relationship_detail":
        return True, "relationship_only"
    return False, "core_context"


def _specificity_class(term: str) -> str:
    tokens = _normalized(term).split()
    if not tokens:
        return "project_category"
    if len(tokens) >= 2 and tokens[0] in GENERIC_MECHANISM_MODIFIERS:
        return "project_category"
    if tokens[-1] in PACKAGING_HEADS or _normalized(term) in PACKAGING_PHRASES:
        return "packaging"
    if tokens[-1] in UMBRELLA_CATEGORY_TAILS and (
        len(tokens) == 1 or tokens[0] in BROAD_CATEGORY_HEADS
    ):
        return "domain_category"
    if tokens[-1] in ARTIFACT_TAILS:
        return "artifact"
    if tokens[-1] in NON_TRANSFERABLE_CATEGORY_TAILS:
        return "project_category"
    if tokens[-1] in BEHAVIOR_SIGNAL_TAILS:
        return "behavioral_signal"
    if tokens[-1] in INTERVENTION_TAILS:
        return "intervention"
    if tokens[-1] in WORKFLOW_PATTERN_TAILS:
        return "workflow_pattern"
    if tokens[0].endswith("ed") and tokens[-1] in UMBRELLA_CATEGORY_TAILS:
        return "project_category"
    if all(token in TECHNOLOGY_DISCOVERED_TERMS for token in tokens):
        return "technology"
    if (
        set(tokens) & MECHANISM_HINTS
        or tokens[-1] in TRANSFER_MECHANISM_TAILS
    ):
        return "mechanism"
    if (
        len(tokens) >= 2
        and tokens[-1].endswith("ing")
        and tokens[-1] not in NON_MECHANISM_ENDINGS
        and ADJECTIVE_FORM_RE.search(tokens[-2])
    ):
        return "mechanism"
    return "project_category"


def _specificity_with_context(term: str, sources: Iterable[dict[str, Any]]) -> str:
    specificity = _specificity_class(term)
    if specificity != "artifact":
        return specificity
    context = _normalized(" ".join(
        str(source.get("full_evidence_text") or source.get("evidence_text") or "")
        for source in sources
    ))
    if any(
        _contains_normalized(context, cue)
        for cue in ("methodology", "workflow pattern", "intervention method")
    ):
        return "workflow_pattern"
    return specificity


def mechanism_specificity(term: str, sources: Iterable[dict[str, Any]] = ()) -> str:
    """Return a diagnostic text-shape classification; it never gates Agent labels."""
    return _specificity_with_context(term, sources)


def _term_distance(text: str, term: str, anchors: Iterable[str]) -> int:
    tokens = _normalized(text).split()
    needle = _normalized(term).split()
    if not tokens or not needle:
        return 999

    def starts(sequence: list[str]) -> list[int]:
        return [
            index for index in range(len(tokens) - len(sequence) + 1)
            if tokens[index:index + len(sequence)] == sequence
        ]

    term_starts = starts(needle)
    anchor_starts = [
        (index, len(sequence))
        for anchor in anchors
        if (sequence := _normalized(anchor).split())
        for index in starts(sequence)
    ]
    if not term_starts or not anchor_starts:
        return 999
    return min(
        abs(term_index - anchor_index)
        for term_index in term_starts
        for anchor_index, _ in anchor_starts
    )


def _hint_present(segment: str, phrase: str) -> bool:
    normalized = _normalized(segment)
    if _contains_normalized(normalized, phrase):
        return True
    compact = _normalized(re.sub(r"(?<=\w)-(?=\w)", "", segment))
    return compact != normalized and _contains_normalized(compact, phrase)


def _mechanism_window(tokens: list[str], end: int) -> str | None:
    if end < 2:
        return None
    if (
        end < len(tokens)
        and tokens[end] in COMPOUND_MECHANISM_TAILS
        and MECHANISM_FORM_RE.search(tokens[end - 1])
    ):
        return " ".join(tokens[end - 1:end + 1])
    pair = tokens[end - 2:end]
    if (
        pair[0] in PHRASE_BOUNDARY_TOKENS | VERB_BOUNDARY_TOKENS | INVALID_MECHANISM_PREFIXES
        or pair[1] in PHRASE_BOUNDARY_TOKENS
    ):
        return None
    if all(token in GENERIC_DISCOVERED_TERMS | TECHNOLOGY_DISCOVERED_TERMS for token in pair):
        return None
    return " ".join(pair)


def _structured_phrases(text: Any) -> list[str]:
    """Extract short mechanism phrases without crossing prose boundaries."""
    phrases: set[str] = set()
    for segment in SEGMENT_RE.split(str(text or "")):
        normalized = _normalized(segment)
        tokens = normalized.split()
        if not tokens:
            continue
        for end in range(1, len(tokens) + 1):
            last = tokens[end - 1]
            adjective_gerund = (
                end >= 2 and last.endswith("ing")
                and bool(ADJECTIVE_FORM_RE.search(tokens[end - 2]))
            )
            if (
                last not in GENERIC_MECHANISM_SUFFIXES
                and not MECHANISM_FORM_RE.search(last)
                and not adjective_gerund
            ):
                continue
            phrase = _mechanism_window(tokens, end)
            if phrase:
                phrases.add(phrase)
    return sorted(phrases)


def _looks_mechanistic(term: str) -> bool:
    tokens = _normalized(term).split()
    return bool(tokens) and tokens[-1] not in NON_MECHANISM_ENDINGS and (
        bool(set(tokens) & MECHANISM_HINTS)
        or tokens[-1] in GENERIC_MECHANISM_SUFFIXES
        or (
            len(tokens) >= 2
            and tokens[-1] in COMPOUND_MECHANISM_TAILS
            and bool(MECHANISM_FORM_RE.search(tokens[-2]))
        )
        or (
            (
                bool(MECHANISM_FORM_RE.search(tokens[-1]))
                or (
                    len(tokens) >= 2 and tokens[-1].endswith("ing")
                    and bool(ADJECTIVE_FORM_RE.search(tokens[-2]))
                )
            )
        )
    )


def _evidence_excerpt(text: str, term: str, limit: int = 240) -> str:
    compact = " ".join(str(text).split())
    tokens = TOKEN_RE.findall(term)
    if not compact or not tokens:
        return compact[:limit]
    match = re.search(
        r"[\W_]+".join(re.escape(token) for token in tokens),
        compact,
        flags=re.IGNORECASE,
    )
    if match is None:
        return compact[:limit]
    start = max(0, match.start() - limit // 2)
    end = min(len(compact), start + limit)
    start = max(0, end - limit)
    return compact[start:end]


def _local_evidence_text(text: str, term: str) -> str:
    key = _normalized(term)
    return next(
        (
            segment for segment in SEGMENT_RE.split(str(text))
            if _contains_normalized(_normalized(segment), key)
        ),
        str(text),
    )


def _source_mechanism_signal(source: dict[str, Any], term: str) -> bool:
    text = str(source.get("full_evidence_text") or source.get("evidence_text") or "")
    tokens = _normalized(term).split()
    if not tokens:
        return False
    if tokens[-1] in GENERIC_MECHANISM_SUFFIXES or set(tokens) & MECHANISM_HINTS:
        return True
    if (
        len(tokens) >= 2
        and tokens[-1] in COMPOUND_MECHANISM_TAILS
        and MECHANISM_FORM_RE.search(tokens[-2])
    ):
        return True
    phrase_pattern = r"[\W_]+".join(re.escape(token) for token in tokens)
    return bool(re.search(
        rf"(?:\*\*[^\w]{{0,8}}{phrase_pattern}(?:\*\*|\])|"
        rf"\[{phrase_pattern}\]|{phrase_pattern}\s*\*\*)",
        text,
        flags=re.IGNORECASE,
    ))


def _overview_structure_signal(source: dict[str, Any], term: str) -> bool:
    if source.get("source_field") != "readme_overview":
        return False
    text = str(source.get("full_evidence_text") or source.get("evidence_text") or "")
    normalized = _normalized(text)
    cue = "table of contents"
    key = _normalized(term)
    return (
        _contains_normalized(normalized, cue)
        and _contains_normalized(normalized, key)
        and _term_distance(text, term, [cue]) <= 30
    )


def normalize_mechanism_surfaces(
        values: Iterable[str], evidence: Iterable[dict[str, Any]] = (),
        ) -> tuple[list[str], list[dict[str, str]]]:
    """Return stable canonical identities plus traceable surface mappings."""
    surfaces = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
    evidence_by_term = {
        _normalized(item.get("term")): item
        for item in evidence if _normalized(item.get("term"))
    }
    normalized_surfaces = {_normalized(value): value for value in surfaces}
    equivalent_surfaces: dict[str, str] = {}
    for value in surfaces:
        equivalent_surfaces.setdefault(_canonical_token_key(value), value)
    canonical_values: list[str] = []
    mappings: list[dict[str, str]] = []
    for surface in surfaces:
        key = _normalized(surface)
        tokens = key.split()
        canonical_key = key
        reason = "identity"
        while len(tokens) > 1 and tokens[0] in PHRASE_BOUNDARY_TOKENS:
            tokens = tokens[1:]
            canonical_key = " ".join(tokens)
            reason = "fragment_prefix"
        equivalent_key = _canonical_token_key(canonical_key)
        equivalent = equivalent_surfaces.get(equivalent_key)
        if reason == "identity" and equivalent and _normalized(equivalent) != key:
            canonical_key = _normalized(equivalent)
            reason = "token_equivalence"
        if reason == "identity" and len(tokens) >= 3:
            shorter_terms = sorted(
                (
                    shorter for shorter in normalized_surfaces
                    if 2 <= len(shorter.split()) < len(tokens)
                    and tokens[-len(shorter.split()):] == shorter.split()
                ),
                key=lambda item: (-len(item.split()), item),
            )
            for shorter in shorter_terms:
                long_sources = evidence_by_term.get(key, {}).get("sources") or []
                short_sources = evidence_by_term.get(shorter, {}).get("sources") or []
                long_repos = {str(item.get("repo") or "").casefold() for item in long_sources}
                short_repos = {str(item.get("repo") or "").casefold() for item in short_sources}
                if (long_repos - {""}) & (short_repos - {""}):
                    canonical_key = shorter
                    reason = "shared_evidence_containment"
                    break
        canonical = normalized_surfaces.get(canonical_key, canonical_key)
        if _normalized(canonical) not in {_normalized(value) for value in canonical_values}:
            canonical_values.append(canonical)
        if key != _normalized(canonical):
            mappings.append({
                "surface_term": surface,
                "canonical_term": canonical,
                "normalization_reason": reason,
            })
    return sorted(canonical_values, key=str.casefold), mappings


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
    mechanism_known = {
        _normalized(term)
        for concept in request.mechanisms
        for term in concept.terms()
    }
    direction_known = {
        _normalized(term)
        for concept in request.problem_concepts + request.exploration_directions
        for term in concept.terms()
    }
    problem_known = {
        _normalized(term)
        for concept in request.problem_concepts
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
        tokens = key.split()
        if (
            not key or key in known or key in GENERIC_DISCOVERED_TERMS
            or key in TECHNOLOGY_DISCOVERED_TERMS or len(key) < 3 or len(key) > 80
            or any("#" in token or any(character.isdigit() for character in token) for token in tokens)
            or (tokens and tokens[0] in PHRASE_BOUNDARY_TOKENS | VERB_BOUNDARY_TOKENS)
            or any(_contains_normalized(key, exclusion) for exclusion in exclusions)
            or any(
                _contains_normalized(known_term, key) or _contains_normalized(key, known_term)
                for known_term in known
            )
            or any(_token_overlap(key, known_term) >= 0.3 for known_term in mechanism_known)
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
            "evidence_text": _evidence_excerpt(evidence_text, value),
            "full_evidence_text": str(evidence_text),
            "confidence": confidence,
            "relationship_backed": relationship_backed,
            "assessment_backed": bool(candidate.get("selected_for_assessment")),
            "relevance_rank": 3,
            "relevance_distance": 999,
        }
        local_evidence = _local_evidence_text(evidence_text, value)
        evidence_normalized = _normalized(local_evidence)
        path_terms = {
            _normalized(str(path.get("term") or ""))
            for path in candidate.get("discovery_paths") or []
            if _normalized(str(path.get("term") or ""))
        }
        confirmation_backed = any(
            path.get("kind") == "query"
            and str(path.get("query_kind") or "").startswith("confirmation_")
            for path in candidate.get("discovery_paths") or []
        )
        mechanism_distance = _term_distance(local_evidence, value, mechanism_known)
        direction_distance = _term_distance(local_evidence, value, direction_known)
        problem_distance = _term_distance(local_evidence, value, problem_known)
        local_problem_exact, local_problem_token = _request_relevance(
            local_evidence, request.problem_concepts
        )
        local_mechanism_exact, local_mechanism_token = _request_relevance(
            local_evidence, request.mechanisms
        )
        local_direction_exact, local_direction_token = _request_relevance(
            local_evidence, request.exploration_directions
        )
        evidence_mechanism_exact, evidence_mechanism_token = _request_relevance(
            evidence_text, [*request.mechanisms, *request.exploration_directions]
        )
        local_problem_exact = local_problem_exact and problem_distance <= 18
        local_mechanism_exact = local_mechanism_exact and mechanism_distance <= 12
        local_direction_exact = local_direction_exact and direction_distance <= 12
        primary_text = _candidate_primary_text(candidate)
        primary_problem_exact, primary_problem_token = _request_relevance(
            primary_text, request.problem_concepts
        )
        primary_mechanism_exact, primary_mechanism_token = _request_relevance(
            primary_text, request.mechanisms
        )
        primary_direction_exact, primary_direction_token = _request_relevance(
            primary_text, request.exploration_directions
        )
        incidental, context_reason = _incidental_source(
            candidate, source_field, local_evidence
        )
        relevance_score = {
            "readme_use_cases": 28,
            "readme_features": 26,
            "readme_overview": 24,
            "description": 24,
            "readme_motivation": 20,
            "readme_concept_match": 16,
            "topics": 14,
            "relationship_detail": 8,
        }.get(source_field, 10)
        relevance_reasons = [context_reason]
        if local_problem_exact:
            relevance_score += 44
            relevance_reasons.append("local_problem")
        elif local_problem_token:
            relevance_score += 24
            relevance_reasons.append("local_problem_token")
        if local_mechanism_exact:
            relevance_score += 32
            relevance_reasons.append("local_mechanism")
        elif local_mechanism_token:
            relevance_score += 12
        if local_direction_exact:
            relevance_score += 18
            relevance_reasons.append("local_direction")
        elif local_direction_token:
            relevance_score += 8
        if primary_problem_exact:
            relevance_score += 20
            relevance_reasons.append("repo_problem")
        elif primary_problem_token:
            relevance_score += 10
        if primary_mechanism_exact or primary_direction_exact:
            relevance_score += 24
            relevance_reasons.append("repo_request_alignment")
        elif primary_mechanism_token or primary_direction_token:
            relevance_score += 5
        if path_terms & mechanism_known:
            relevance_score += 10
            relevance_reasons.append("mechanism_retrieval")
        if candidate.get("selected_for_assessment"):
            relevance_score += 6
            relevance_reasons.append("assessment_shortlist")
        if incidental:
            relevance_score = min(relevance_score, 38)
        request_anchored = bool(
            local_problem_exact or local_problem_token
            or primary_problem_exact
        )
        source["evidence_relevance_score"] = max(0, min(100, relevance_score))
        source["evidence_relevance_reason"] = ",".join(dict.fromkeys(relevance_reasons))
        source["core_use_case"] = not incidental
        source["request_anchored"] = request_anchored and not incidental
        source["mechanism_anchored"] = bool(
            evidence_mechanism_exact or evidence_mechanism_token
        ) and not incidental
        source["retrieval_stage"] = "confirmation" if confirmation_backed else "discovery"
        if (
            mechanism_distance <= 12
            and any(_contains_normalized(evidence_normalized, term) for term in mechanism_known)
        ):
            source["relevance_rank"] = 0
            source["relevance_distance"] = mechanism_distance
        elif (
            direction_distance <= 12
            and any(_contains_normalized(evidence_normalized, term) for term in direction_known)
        ):
            source["relevance_rank"] = 1
            source["relevance_distance"] = direction_distance
        elif (
            bool(path_terms & mechanism_known)
            and (
                (
                    source_field in {"readme_concept_match", "topics"}
                    and _source_mechanism_signal(source, key)
                )
                or _overview_structure_signal(source, key)
            )
        ):
            source["relevance_rank"] = 2
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
    # Source terms are observations, not code-approved mechanism names. Preserve
    # discovery order and expose the previous anchoring calculations only as signals.
    ranked = list(support_repos)[:limit]
    result: list[dict[str, Any]] = []
    for key in ranked:
        entries = sources[key]
        result.append({
            "term": display[key],
            "kind": "source_term",
            "request_anchored": any(source["request_anchored"] for source in entries),
            "mechanism_anchored": any(source["mechanism_anchored"] for source in entries),
            "support_count": len(support_repos[key]),
            "sources": [
                {
                    name: value for name, value in source.items()
                    if name not in {
                        "relationship_backed", "assessment_backed", "relevance_rank",
                        "relevance_distance", "full_evidence_text",
                    }
                }
                for source in entries[:3]
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
                   negative_directions: Iterable[str] = (),
                   confirmed_directions: Iterable[str] | None = None,
                   confirmation_records: Iterable[dict[str, Any]] = ()) -> SearchBoundary:
    candidate_list = list(candidates)
    presented_list = list(presented)
    recalled = mechanism_names(candidate_list)
    presented_names = mechanism_names(presented_list)
    recalled_keys = {value.casefold() for value in recalled}
    rejected = list(dict.fromkeys(
        str(value).strip() for value in rejected_directions if str(value).strip()
    ))
    negatives = list(dict.fromkeys(
        str(value).strip() for value in negative_directions if str(value).strip()
    ))
    blocked_keys = {
        *(value.casefold() for value in rejected),
        *(value.casefold() for value in negatives),
    }
    confirmed_keys = (
        {str(value).casefold() for value in confirmed_directions}
        if confirmed_directions is not None else recalled_keys
    )
    explored = [
        concept.term for concept in request.exploration_directions
        if concept.term.casefold() in recalled_keys
        and concept.term.casefold() in confirmed_keys
        and concept.term.casefold() not in blocked_keys
    ]
    explored_keys = {value.casefold() for value in explored}
    unexplored = [
        concept.term for concept in request.exploration_directions
        if concept.term.casefold() not in explored_keys
        and concept.term.casefold() not in blocked_keys
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
        confirmation_queue=[],
        mechanism_confirmations=[dict(item) for item in confirmation_records],
        negative_directions=negatives,
    )


def boundary_delta(current: dict[str, Any], previous: dict[str, Any] | None) -> BoundaryDelta:
    previous = previous or {}

    def new_values(name: str) -> list[str]:
        old = {str(value).casefold() for value in previous.get(name) or []}
        return [str(value) for value in current.get(name) or [] if str(value).casefold() not in old]

    evidence = [
        *(previous.get("discovered_term_evidence") or []),
        *(current.get("discovered_term_evidence") or []),
    ]
    current_mechanisms, mechanism_mappings = normalize_mechanism_surfaces(
        current.get("recalled_mechanisms") or [], evidence,
    )
    previous_mechanisms, _ = normalize_mechanism_surfaces(
        previous.get("recalled_mechanisms") or [], evidence,
    )
    current_presented, presented_mappings = normalize_mechanism_surfaces(
        current.get("presented_mechanisms") or [], evidence,
    )
    previous_presented, _ = normalize_mechanism_surfaces(
        previous.get("presented_mechanisms") or [], evidence,
    )

    def canonical_new(current_values: list[str], previous_values: list[str],
                      excluded_values: Iterable[str] = ()) -> list[str]:
        old = {
            _canonical_token_key(value)
            for value in (*previous_values, *excluded_values)
        }
        return [value for value in current_values if _canonical_token_key(value) not in old]

    return BoundaryDelta(
        new_mechanisms=canonical_new(
            current_mechanisms, previous_mechanisms,
            previous.get("unexplored_directions") or [],
        ),
        new_mechanism_surfaces=new_values("recalled_mechanisms"),
        new_presented_mechanisms=canonical_new(current_presented, previous_presented),
        new_presented_mechanism_surfaces=new_values("presented_mechanisms"),
        mechanism_normalizations=[*mechanism_mappings, *presented_mappings],
        new_directions=new_values("explored_directions"),
        new_terms=new_values("discovered_terms"),
    )
