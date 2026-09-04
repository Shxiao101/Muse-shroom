from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


GITHUB_LINK = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.I)
INSTALL_RE = re.compile(r"(?im)^#{1,3}\s*(install|installation|getting started|quick ?start|setup)\b")
USAGE_RE = re.compile(r"(?im)^#{1,3}\s*(use|usage|examples?|how to use)\b")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def safe_readme(text: str, max_chars: int = 200_000) -> tuple[str, bool]:
    truncated = len(text) > max_chars
    text = text[:max_chars]
    def preserve_lines(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", preserve_lines, text, flags=re.I | re.S)
    text = re.sub(r"<!--.*?-->", preserve_lines, text, flags=re.S)
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    return text, truncated


def github_links(text: str, source_full_name: str) -> list[str]:
    found = []
    for owner, repo in GITHUB_LINK.findall(text):
        full_name = f"{owner}/{repo.rstrip('.,);]') }"
        if full_name.lower() != source_full_name.lower() and full_name.lower() not in {v.lower() for v in found}:
            found.append(full_name)
    return found


def readme_signals(text: str) -> dict[str, Any]:
    lower = text.lower()
    return {
        "has_install": bool(INSTALL_RE.search(text)),
        "has_usage": bool(USAGE_RE.search(text)),
        "mentions_uninstall": "uninstall" in lower or "remove the mod" in lower,
        "mentions_compatibility": "compatib" in lower or "requires version" in lower,
        "mentions_permissions": "permission" in lower or "scope" in lower,
        "mentions_tool_contract": "tools/list" in lower or "inputschema" in lower or "tool contract" in lower,
    }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def age_days(timestamp: str | None, *,
             reference_time: str | datetime | None = None) -> int:
    if not timestamp:
        return 10_000
    try:
        dt = _timestamp(timestamp)
        reference = _timestamp(reference_time) if reference_time is not None else _utc_now()
        return max(0, (reference - dt).days)
    except (TypeError, ValueError):
        return 10_000


def _plain_line(line: str) -> str:
    line = re.sub(r"!\[[^]]*]\([^)]*\)", "", line)
    line = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", line)
    line = re.sub(r"<[^>]+>", "", line)
    line = re.sub(r"^[>\-*+\d.\s]+", "", line)
    line = line.replace("`", "").strip()
    return " ".join(line.split())


LANGUAGE_SWITCH_RE = re.compile(
    r"^(?:english|chinese|中文|日本語|日本語版|한국어|français|deutsch|español|readme|documentation|wiki)"
    r"(?:\s*[|／/·,-]\s*(?:english|chinese|中文|日本語|한국어|français|deutsch|español|readme|documentation|wiki))+$",
    re.I,
)


def _is_thin_overview(text: str) -> bool:
    compact = re.sub(r"https?://\S+", " ", text)
    compact = re.sub(r"[\[\]()!|#*`\-–—_/\\|]+", " ", compact)
    compact = " ".join(compact.split())
    if len(compact) < 40:
        return True
    if LANGUAGE_SWITCH_RE.fullmatch(compact.casefold()):
        return True
    tokens = compact.casefold().split()
    language_tokens = {
        "english", "chinese", "中文", "日本語", "한국어", "français", "deutsch", "español",
        "readme", "wiki", "docs", "documentation", "toc",
    }
    if tokens and all(token in language_tokens or len(token) <= 2 for token in tokens):
        return True
    return False


def _section_snippet(lines: list[str], start: int, max_chars: int = 260) -> tuple[str, int]:
    parts: list[str] = []
    end = start
    for index in range(start, min(len(lines), start + 16)):
        if index > start and HEADING_RE.match(lines[index]):
            break
        plain = _plain_line(lines[index])
        if not plain:
            continue
        parts.append(plain)
        end = index
        if len(" ".join(parts)) >= max_chars:
            break
    return " ".join(parts)[:max_chars].strip(), end + 1


def readme_snippets(readme: str, concept_terms: list[str] | None = None,
                    artifact_types: list[str] | None = None) -> list[dict[str, Any]]:
    lines = readme.splitlines()
    candidates: list[tuple[str, int]] = []
    headings: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match:
            headings.append((index, match.group(2).strip().lower()))

    for index, line in enumerate(lines):
        plain = _plain_line(line)
        if not plain or HEADING_RE.match(line):
            continue
        if _is_thin_overview(plain):
            continue
        candidates.append(("overview", index))
        break

    terms = [term.casefold() for term in (concept_terms or []) if term.strip()]
    for index, line in enumerate(lines):
        lower = _plain_line(line).casefold()
        if lower and any(term in lower for term in terms):
            candidates.append(("concept_match", index))
            break

    for kind, pattern in (
        ("installation", re.compile(r"\b(install|installation|getting started|quick ?start|setup)\b")),
        ("usage", re.compile(r"\b(use|usage|examples?|how to use)\b")),
    ):
        match = next((index for index, heading in headings if pattern.search(heading)), None)
        if match is not None:
            candidates.append((kind, match))

    for kind, pattern in (
        ("features", re.compile(r"\b(features?|capabilities)\b")),
        ("use_cases", re.compile(r"\b(use cases?|workflows?|scenarios?)\b")),
        ("motivation", re.compile(r"\b(motivation|why|goals?)\b")),
        ("philosophy", re.compile(r"\b(philosophy|principles?|design)\b")),
    ):
        match = next((index for index, heading in headings if pattern.search(heading)), None)
        if match is not None:
            candidates.append((kind, match))

    types = {value.lower() for value in (artifact_types or [])}
    if "mcp" in types:
        risk_terms = ("permission", "scope", "tools/list", "inputschema")
    elif "skill" in types:
        risk_terms = ("trigger", "when to use", "skill.md")
    elif "mod" in types:
        risk_terms = ("compatib", "requires version", "uninstall", "conflict")
    else:
        risk_terms = ("permission", "requirements", "compatib", "security")
    for index, line in enumerate(lines):
        if any(term in line.casefold() for term in risk_terms):
            candidates.append(("type_risk", index))
            break

    snippets: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    kind_order = {
        "concept_match": 0, "overview": 1, "features": 2, "use_cases": 3,
        "motivation": 4, "philosophy": 5, "installation": 6, "usage": 7,
        "type_risk": 8,
    }
    candidates.sort(key=lambda item: kind_order.get(item[0], 9))
    for kind, start in candidates:
        text, end = _section_snippet(lines, start)
        identity = text.casefold()
        if not text or identity in seen_text:
            continue
        seen_text.add(identity)
        snippets.append({"snippet_type": kind, "text": text, "line_start": start + 1, "line_end": end})
        if len(snippets) >= 7:
            break
    return snippets


def make_evidence(candidate: dict[str, Any], readme: str, readme_truncated: bool,
                  *, concept_terms: list[str] | None = None,
                  artifact_types: list[str] | None = None) -> list[dict[str, Any]]:
    full_name = candidate["full_name"]
    evidence = [{
        "id": f"repo:{full_name.lower()}:metadata",
        "kind": "github_metadata",
        "source": candidate.get("html_url", f"https://github.com/{full_name}"),
        "facts": {
            "description": candidate.get("description"),
            "stars": candidate.get("stargazers_count", 0),
            "forks": candidate.get("forks_count", 0),
            "open_issues": candidate.get("open_issues_count", 0),
            "archived": candidate.get("archived", False),
            "license": (candidate.get("license") or {}).get("spdx_id"),
            "topics": candidate.get("topics", []),
            "pushed_at": candidate.get("pushed_at"),
        },
    }]
    if readme:
        evidence.append({
            "id": f"repo:{full_name.lower()}:readme",
            "kind": "readme",
            "source": f"https://github.com/{full_name}#readme",
            "facts": {
                **readme_signals(readme), "truncated": readme_truncated,
                "sha": candidate.get("readme_sha"), "untrusted_source": True,
            },
        })
        for snippet in readme_snippets(readme, concept_terms, artifact_types):
            kind = snippet["snippet_type"]
            evidence.append({
                "id": f"repo:{full_name.lower()}:readme:{kind}",
                "kind": "readme_excerpt",
                "facts": {
                    **snippet, "sha": candidate.get("readme_sha"),
                    "untrusted_source": True,
                    "parent_evidence_id": f"repo:{full_name.lower()}:readme",
                },
            })
    if candidate.get("latest_release"):
        release = candidate["latest_release"]
        evidence.append({
            "id": f"repo:{full_name.lower()}:release", "kind": "github_release",
            "source": release.get("html_url"),
            "facts": {"tag_name": release.get("tag_name"), "published_at": release.get("published_at")},
        })
    return evidence
