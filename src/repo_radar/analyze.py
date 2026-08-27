from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


GITHUB_LINK = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.I)
INSTALL_RE = re.compile(r"(?im)^#{1,3}\s*(install|installation|getting started|quick ?start|setup)\b")
USAGE_RE = re.compile(r"(?im)^#{1,3}\s*(usage|examples?|how to use)\b")


def safe_readme(text: str, max_chars: int = 200_000) -> tuple[str, bool]:
    truncated = len(text) > max_chars
    text = text[:max_chars]
    text = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", text, flags=re.I | re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
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


def age_days(timestamp: str | None) -> int:
    if not timestamp:
        return 10_000
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return max(0, (datetime.now(timezone.utc) - dt).days)
    except ValueError:
        return 10_000


def make_evidence(candidate: dict[str, Any], readme: str, readme_truncated: bool) -> list[dict[str, Any]]:
    full_name = candidate["full_name"]
    evidence = [{
        "id": f"repo:{full_name.lower()}:metadata",
        "kind": "github_metadata",
        "source": candidate.get("html_url", f"https://github.com/{full_name}"),
        "facts": {
            "description": candidate.get("description"),
            "stars": candidate.get("stargazers_count", 0),
            "forks": candidate.get("forks_count", 0),
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
            "facts": {**readme_signals(readme), "truncated": readme_truncated, "sha": candidate.get("readme_sha")},
        })
    if candidate.get("latest_release"):
        release = candidate["latest_release"]
        evidence.append({
            "id": f"repo:{full_name.lower()}:release", "kind": "github_release",
            "source": release.get("html_url"),
            "facts": {"tag_name": release.get("tag_name"), "published_at": release.get("published_at")},
        })
    return evidence
