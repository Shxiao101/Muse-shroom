"""What the host Agent reads: core outputs with each fact stated once.

Every later model request re-reads what earlier tool calls returned, so repeated and
diagnostic fields cost tokens many times over. Engine outputs keep every field for
storage, replay and evaluation; this view drops copies and diagnostics the Agent never
reads. The saved ranking and the Explorer keep the full record.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .search import _sync_output_bytes

# The Agent reads decisions from ``observation`` (SKILL.md §6) and state from the
# top-level ``boundary``. A field present in both places, with the same value, keeps one copy.
KEEP_TOP_LEVEL = {"boundary"}
DUPLICATE_KEYS = ("boundary", "boundary_delta", "semantic_hypotheses", "sidecar_metrics")
TERM_EVIDENCE_FIELDS = ("term", "kind", "request_anchored", "support_count", "sources")
TERM_SOURCE_FIELDS = ("repo", "source_field", "evidence_id", "evidence_text", "request_anchored")
# Rank already verified these; the saved ranking keeps them for the Explorer.
RANK_ITEM_OMITTED = ("evidence", "discovery_paths")


def _term_evidence(items: Any) -> Any:
    if not isinstance(items, list):
        return items
    compact = []
    for item in items:
        if not isinstance(item, dict):
            compact.append(item)
            continue
        entry = {key: item[key] for key in TERM_EVIDENCE_FIELDS if key in item}
        if isinstance(entry.get("sources"), list):
            entry["sources"] = [
                {key: source[key] for key in TERM_SOURCE_FIELDS if key in source}
                if isinstance(source, dict) else source
                for source in entry["sources"]
            ]
        compact.append(entry)
    return compact


def _boundary(boundary: Any) -> Any:
    # observation.discovered_term_evidence is the copy the Agent is told to read.
    if not isinstance(boundary, dict):
        return boundary
    return {key: value for key, value in boundary.items() if key != "discovered_term_evidence"}


def _dedupe(output: dict[str, Any]) -> None:
    observation = output.get("observation")
    if not isinstance(observation, dict):
        return
    for key in DUPLICATE_KEYS:
        if key in output and key in observation and output[key] == observation[key]:
            if key in KEEP_TOP_LEVEL:
                del observation[key]
            else:
                del output[key]


def session_view(output: dict[str, Any]) -> dict[str, Any]:
    """View of a search, iterate or observe output."""
    view = json.loads(json.dumps(output, ensure_ascii=False))
    _dedupe(view)
    if "boundary" in view:
        view["boundary"] = _boundary(view["boundary"])
    observation = view.get("observation")
    if isinstance(observation, dict):
        if "boundary" in observation:
            observation["boundary"] = _boundary(observation["boundary"])
        if "discovered_term_evidence" in observation:
            observation["discovered_term_evidence"] = _term_evidence(
                observation["discovered_term_evidence"]
            )
    _resize(view)
    return view


def rank_view(output: dict[str, Any]) -> dict[str, Any]:
    """View of a rank output: what presenting and correcting citations need."""
    view = json.loads(json.dumps(output, ensure_ascii=False))
    view["items"] = [
        {key: value for key, value in item.items() if key not in RANK_ITEM_OMITTED}
        if isinstance(item, dict) else item
        for item in view.get("items") or []
    ]
    if "boundary" in view:
        view["boundary"] = _boundary(view["boundary"])
    metrics = view.get("sidecar_metrics")
    if isinstance(metrics, dict):
        metrics.pop("base_ledger", None)
    return view


def _resize(view: dict[str, Any]) -> None:
    # coverage.output_bytes describes what the Agent received, so it follows the view.
    if isinstance(view.get("coverage"), dict) and "output_bytes" in view["coverage"]:
        _sync_output_bytes(view)


def candidate_digest(candidate: dict[str, Any]) -> str:
    raw = json.dumps(candidate, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ShownCandidates:
    """Per-process memo of candidates this server already returned, by search_id.

    One MCP server lives for one host session, so this is what the Agent already has in
    context. It is a view cache, not session state: losing it only means full output.
    """

    def __init__(self) -> None:
        self._shown: dict[str, dict[str, str]] = {}

    def remember(self, search_id: str, candidates: Any) -> None:
        shown = self._shown.setdefault(search_id, {})
        for candidate in candidates or []:
            if isinstance(candidate, dict) and candidate.get("full_name"):
                shown[str(candidate["full_name"]).lower()] = candidate_digest(candidate)

    def reset(self, search_id: str, candidates: Any) -> None:
        self._shown[search_id] = {}
        self.remember(search_id, candidates)

    def omit_unchanged(self, view: dict[str, Any]) -> dict[str, Any]:
        """Return only new or changed candidates; name the unchanged ones."""
        search_id = str(view.get("search_id") or "")
        candidates = view.get("candidates")
        if not search_id or not isinstance(candidates, list):
            return view
        shown = self._shown.get(search_id, {})
        fresh, unchanged = [], []
        for candidate in candidates:
            name = str(candidate.get("full_name") or "") if isinstance(candidate, dict) else ""
            if name and shown.get(name.lower()) == candidate_digest(candidate):
                unchanged.append(name)
            else:
                fresh.append(candidate)
        self.remember(search_id, fresh)
        view["candidates"] = fresh
        if unchanged:
            view["unchanged_candidates"] = unchanged
        return view
