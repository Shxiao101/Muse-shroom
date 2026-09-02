"""Isolated semantic sidecar for host-Agent world-knowledge hypotheses.

Host hypotheses may retrieve and enrich outside the evidence-driven path.
They must not consume base query, README, shortlist, or Boundary capacity
until a validated result is merged at rank time.
"""

from __future__ import annotations

from typing import Any, Iterable

from .boundary import _contains, _normalized, _readme_match
from .models import (
    ContractError, ExplorationAddition, SearchHypothesis, SearchRequest, repo_key,
)
from .queries import _qualifiers, _quote, query_fingerprint, term_blocked_by_negative


HOST_HYPOTHESIS = "host_hypothesis"
SEMANTIC_QUERY_BUDGET = 4
SEMANTIC_HYPOTHESIS_LIMIT = 2
SEMANTIC_CANDIDATE_CAP = 40
SEMANTIC_README_PER_HYPOTHESIS = 2
SEMANTIC_ASSESSMENT_PER_HYPOTHESIS = 1
SEMANTIC_RELEASE_LIMIT = 2
SEMANTIC_QUERIES_PER_HYPOTHESIS = 2
HOST_HYPOTHESIS_WINDOW = (1, 2)
SEMANTIC_QUERY_KINDS = frozenset({"semantic_pure", "semantic_bridge"})
SEMANTIC_PARTITION = "semantic_sidecar"


def empty_sidecar_state() -> dict[str, Any]:
    return {
        "hypotheses": [],
        "candidates": [],
        "queries": [],
        "metrics": empty_sidecar_metrics(),
    }


def empty_sidecar_metrics() -> dict[str, Any]:
    return {
        "semantic_queries_planned": 0,
        "semantic_queries_executed": 0,
        "semantic_queries_reused": 0,
        "semantic_queries_failed": 0,
        "semantic_candidate_count": 0,
        "semantic_readme_enrichments": 0,
        "semantic_assessment_count": 0,
        "semantic_release_lookups": 0,
        "base_semantic_overlap": 0,
        "sidecar_api_calls": 0,
        "validated_presented": 0,
        "regular_top10_displaced": 0,
        "secondary_terms_blocked_request_anchored": 0,
    }


def is_host_hypothesis(addition: ExplorationAddition | dict[str, Any]) -> bool:
    evidence = (
        addition.evidence if isinstance(addition, ExplorationAddition)
        else str(addition.get("evidence") or "")
    )
    return str(evidence).strip() == HOST_HYPOTHESIS


def split_additions(
    hypothesis: SearchHypothesis,
) -> tuple[list[ExplorationAddition], list[ExplorationAddition]]:
    host: list[ExplorationAddition] = []
    ordinary: list[ExplorationAddition] = []
    for addition in hypothesis.add_exploration_directions:
        if is_host_hypothesis(addition):
            host.append(addition)
        else:
            ordinary.append(addition)
    return host, ordinary


def problem_anchor_map(request: SearchRequest) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for concept in request.problem_concepts:
        for term in concept.terms():
            key = _normalized(term)
            if key:
                mapping.setdefault(key, term)
    return mapping


def _host_term_set(additions: Iterable[ExplorationAddition]) -> set[str]:
    return {_normalized(item.term) for item in additions if item.term.strip()}


def validate_host_hypotheses(
    hypothesis: SearchHypothesis,
    request: SearchRequest,
    *,
    iteration: int,
    existing: list[dict[str, Any]],
    negatives: Iterable[str] = (),
) -> list[ExplorationAddition]:
    """Accept at most two session-wide host hypotheses in iterations 1-2."""
    host, _ordinary = split_additions(hypothesis)
    if not host:
        return []
    if iteration not in HOST_HYPOTHESIS_WINDOW:
        raise ContractError(
            "host_hypothesis additions are only allowed during post-search iterations 1 and 2"
        )
    existing_count = len(existing)
    if existing_count + len(host) > SEMANTIC_HYPOTHESIS_LIMIT:
        raise ContractError(
            f"at most {SEMANTIC_HYPOTHESIS_LIMIT} host_hypothesis additions are allowed per session"
        )
    anchors = problem_anchor_map(request)
    exclusions = {_normalized(value) for value in request.exclusions if _normalized(value)}
    blocked = {_normalized(value) for value in negatives if _normalized(value)}
    seen_existing = {_normalized(item.get("term")) for item in existing}
    seen_round: set[str] = set()
    host_keys = _host_term_set(host)
    ordinary_fields = [
        hypothesis.target_direction, hypothesis.target_mechanism,
        *hypothesis.concepts, *hypothesis.adjacent_concepts, *hypothesis.aliases,
        *hypothesis.promote_discovered_terms,
    ]
    for value in ordinary_fields:
        if _normalized(value) in host_keys:
            raise ContractError(
                "host hypothesis terms must not be repeated in ordinary hypothesis fields; "
                "the sidecar routes them"
            )
    for addition in host:
        term_key = _normalized(addition.term)
        if not term_key:
            raise ContractError("host_hypothesis term is required")
        if term_key in seen_existing or term_key in seen_round:
            raise ContractError(f"duplicate host_hypothesis term {addition.term!r}")
        seen_round.add(term_key)
        if term_key in exclusions or term_key in blocked:
            raise ContractError(
                f"host_hypothesis term {addition.term!r} is excluded or a negative direction"
            )
        if term_blocked_by_negative(addition.term, negatives):
            raise ContractError(
                f"host_hypothesis term {addition.term!r} is excluded or a negative direction"
            )
        anchor = str(addition.request_anchor or "").strip()
        if not anchor:
            raise ContractError("host_hypothesis requires request_anchor matching a problem concept or alias")
        if _normalized(anchor) not in anchors:
            raise ContractError(
                "host_hypothesis request_anchor must match an original problem_concepts term or alias"
            )
        if "/" in addition.term and addition.term.count("/") == 1:
            raise ContractError("host_hypothesis must not name a repository")
    return host


def hypothesis_record(
    addition: ExplorationAddition, *, iteration: int, index: int,
) -> dict[str, Any]:
    hypothesis_id = f"h{iteration}:{index}:{_normalized(addition.term).replace(' ', '-')}"
    return {
        "id": hypothesis_id,
        "term": addition.term,
        "request_anchor": addition.request_anchor,
        "reason": addition.reason,
        "evidence": HOST_HYPOTHESIS,
        "source_iteration": iteration,
        "status": "proposed",
        "queries": [],
        "evidence_repos": [],
        "assessment_repo": None,
        "presented": False,
        "overlap_repos": [],
    }


def plan_sidecar_queries(
    records: list[dict[str, Any]],
    request: SearchRequest,
    *,
    negatives: Iterable[str] = (),
    known_fingerprints: Iterable[str] = (),
    remaining_budget: int = SEMANTIC_QUERY_BUDGET,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Emit separately quoted pure and bridge queries. Never one combined phrase."""
    suffix = _qualifiers(request)
    known = set(known_fingerprints)
    planned: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    remaining = max(0, remaining_budget)

    def make(term: str, kind: str, record: dict[str, Any], extra: str | None = None) -> dict[str, Any] | None:
        quoted = _quote(term)
        if not quoted:
            return None
        if extra:
            extra_quoted = _quote(extra)
            if not extra_quoted:
                return None
            query = f"{quoted} {extra_quoted} in:name,description,topics,readme {suffix}"
        else:
            query = f"{quoted} in:name,description,topics,readme {suffix}"
        normalized = " ".join(query.split())
        return {
            "query": normalized,
            "kind": kind,
            "sort": "stars",
            "concept_id": f"sidecar:{record['id']}",
            "term": term,
            "lane_kind": "semantic",
            "fingerprint": query_fingerprint(normalized),
            "hypothesis_id": record["id"],
            "retrieval_partition": SEMANTIC_PARTITION,
        }

    for record in records:
        if term_blocked_by_negative(record["term"], negatives):
            continue
        specs = [
            make(record["term"], "semantic_pure", record),
            make(record["term"], "semantic_bridge", record, extra=record.get("request_anchor")),
        ]
        for spec in specs:
            if spec is None:
                continue
            if spec["fingerprint"] in known or any(
                item["fingerprint"] == spec["fingerprint"] for item in planned
            ):
                skipped.append({**spec, "skipped": True, "skip_reason": "duplicate"})
                continue
            if remaining <= 0:
                skipped.append({**spec, "skipped": True, "skip_reason": "semantic_budget"})
                continue
            known.add(spec["fingerprint"])
            planned.append(spec)
            remaining -= 1
            record.setdefault("queries", []).append({
                "kind": spec["kind"], "query": spec["query"], "fingerprint": spec["fingerprint"],
            })
    return planned, skipped


def match_hypothesized_term(candidate: dict[str, Any], term: str) -> list[dict[str, Any]]:
    description = str(candidate.get("description") or "")
    topics = [str(value) for value in candidate.get("topics") or []]
    readme = str(candidate.get("readme") or "")
    readme_lines = [
        (index, line, _normalized(line))
        for index, line in enumerate(readme.splitlines(), 1)
    ]
    matches: list[dict[str, Any]] = []
    readme_hit = _readme_match(readme_lines, term)
    if readme_hit:
        text, line = readme_hit
        matches.append({
            "source": "readme", "matched_term": term, "text": text, "line_start": line,
        })
    if _contains(description, term):
        matches.append({
            "source": "description", "matched_term": term,
            "text": " ".join(description.split())[:220],
        })
    topic = next((value for value in topics if _contains(value.replace("-", " "), term)), None)
    if topic is not None:
        matches.append({"source": "topics", "matched_term": term, "text": topic})
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for match in matches:
        identity = (str(match["source"]), str(match["matched_term"]).casefold())
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(match)
    return unique


def apply_semantic_mechanism(
    candidate: dict[str, Any], term: str, hypothesis_id: str,
) -> bool:
    matches = match_hypothesized_term(candidate, term)
    if not matches:
        return False
    full_name = str(candidate.get("full_name") or "").lower()
    evidence_id = f"repo:{full_name}:semantic:{hypothesis_id}"
    primary = matches[0]
    mechanism = {
        "name": term,
        "role": "exploration",
        "matched_terms": list(dict.fromkeys(str(item["matched_term"]) for item in matches)),
        "sources": list(dict.fromkeys(str(item["source"]) for item in matches)),
        "evidence_ids": [evidence_id],
        "semantic_origin": True,
        "hypothesis_id": hypothesis_id,
    }
    existing = [
        item for item in candidate.get("mechanisms") or []
        if str(item.get("name") or "").casefold() != term.casefold()
        or not item.get("semantic_origin")
    ]
    existing.append(mechanism)
    candidate["mechanisms"] = existing
    fact = {
        "mechanism": term,
        "role": "exploration",
        "source_field": primary["source"],
        "matched_term": primary["matched_term"],
        "text": primary["text"],
        "hypothesis_id": hypothesis_id,
        "semantic_origin": True,
        "source": (
            f"https://github.com/{candidate.get('full_name')}#readme"
            if primary["source"] == "readme" else candidate.get("html_url")
        ),
        "untrusted_source": primary["source"] == "readme",
    }
    evidence_item = {
        "id": evidence_id,
        "kind": "mechanism_match",
        "source": candidate.get("html_url"),
        "facts": {
            "mechanisms": [fact],
            "hypothesis_id": hypothesis_id,
            "semantic_origin": True,
            "untrusted_source": fact["untrusted_source"],
        },
    }
    evidence = [item for item in candidate.get("evidence") or [] if item.get("id") != evidence_id]
    evidence.append(evidence_item)
    candidate["evidence"] = evidence
    return True


def select_enrichment_targets(
    recalled: list[dict[str, Any]],
    *,
    limit: int = SEMANTIC_README_PER_HYPOTHESIS,
) -> list[dict[str, Any]]:
    pending = [item for item in recalled if "readme" not in item]
    pending.sort(key=lambda item: (
        0 if any(
            path.get("query_kind") == "semantic_bridge"
            for path in item.get("discovery_paths") or []
        ) else 1,
        -int(item.get("stargazers_count") or 0),
        str(item.get("full_name") or "").lower(),
    ))
    return pending[: max(0, limit)]


def select_assessment_candidate(
    recalled: list[dict[str, Any]],
    *,
    term: str,
    regular_shortlist: Iterable[str],
) -> dict[str, Any] | None:
    shortlist = {name.casefold() for name in regular_shortlist if str(name).strip()}
    evidenced = [
        item for item in recalled
        if any(
            str(mechanism.get("name") or "").casefold() == term.casefold()
            and mechanism.get("semantic_origin")
            for mechanism in item.get("mechanisms") or []
        )
    ]
    if not evidenced:
        return None
    overlap = [
        item for item in evidenced
        if str(item.get("full_name") or "").casefold() in shortlist
    ]
    pool = overlap or evidenced
    pool.sort(key=lambda item: (
        0 if str(item.get("full_name") or "").casefold() in shortlist else 1,
        -int(item.get("stargazers_count") or 0),
        str(item.get("full_name") or "").lower(),
    ))
    return pool[0]


def derive_hypothesis_status(record: dict[str, Any]) -> str:
    queries = list(record.get("queries") or [])
    planned = [item for item in queries if not item.get("skipped")]
    skipped = [item for item in queries if item.get("skipped")]
    failed = bool(record.get("failed"))
    incomplete = bool(record.get("incomplete"))
    executed = [item for item in planned if item.get("executed")]
    if record.get("presented"):
        return "presented"
    if record.get("validated"):
        return "validated"
    if record.get("evidence_repos"):
        return "evidence_found"
    if failed or incomplete or (skipped and len(executed) < SEMANTIC_QUERIES_PER_HYPOTHESIS):
        if not executed and not record.get("evidence_repos"):
            return "inconclusive" if (skipped or failed or incomplete) else "proposed"
        return "inconclusive"
    if len(executed) >= SEMANTIC_QUERIES_PER_HYPOTHESIS and not record.get("evidence_repos"):
        return "rejected"
    if executed:
        return "searched"
    return "proposed"


def refresh_statuses(records: Iterable[dict[str, Any]]) -> None:
    for record in records:
        record["status"] = derive_hypothesis_status(record)


def assessment_cites_mechanism(
    assessment: dict[str, Any], candidate: dict[str, Any], term: str,
) -> bool:
    named = str(assessment.get("mechanism") or "").strip()
    if named.casefold() != term.casefold():
        return False
    allowed_ids = {
        str(evidence_id)
        for mechanism in candidate.get("mechanisms") or []
        if str(mechanism.get("name") or "").casefold() == term.casefold()
        for evidence_id in mechanism.get("evidence_ids") or []
    }
    cited: set[str] = set()
    for reason in assessment.get("reasons") or []:
        for evidence_id in reason.get("evidence_ids") or []:
            cited.add(str(evidence_id))
    return bool(allowed_ids & cited)


def mark_validated(
    records: list[dict[str, Any]],
    *,
    assessments: dict[str, dict[str, Any]],
    candidates_by_name: dict[str, dict[str, Any]],
    eligible_names: set[str],
) -> None:
    for record in records:
        term = str(record.get("term") or "")
        for name, assessment in assessments.items():
            candidate = candidates_by_name.get(name)
            if candidate is None or name not in eligible_names:
                continue
            if assessment_cites_mechanism(assessment, candidate, term):
                record["validated"] = True
                record["assessment_repo"] = candidate.get("full_name") or name
                break
        record["status"] = derive_hypothesis_status(record)


def mark_presented(records: list[dict[str, Any]], display_order: Iterable[str]) -> None:
    shown = {str(name).casefold() for name in display_order}
    for record in records:
        repo = str(record.get("assessment_repo") or "").casefold()
        record["presented"] = bool(record.get("validated") and repo in shown)
        record["status"] = derive_hypothesis_status(record)


def unvalidated_terms(records: Iterable[dict[str, Any]]) -> set[str]:
    return {
        _normalized(item.get("term"))
        for item in records
        if not item.get("validated") and _normalized(item.get("term"))
    }


def strip_unvalidated_semantic_terms(
    names: Iterable[str], blocked: set[str],
) -> list[str]:
    result: list[str] = []
    for name in names:
        if _normalized(name) in blocked:
            continue
        result.append(name)
    return result


def base_artifact_snapshot(
    *,
    query_fingerprints: list[str],
    candidate_names: list[str],
    readme_names: list[str],
    shortlist_names: list[str],
    lane_counts: dict[str, int],
    recalled_mechanisms: list[str],
    presented_mechanisms: list[str],
    explored_directions: list[str],
) -> dict[str, Any]:
    return {
        "query_fingerprints": list(query_fingerprints),
        "candidate_names": sorted(name.casefold() for name in candidate_names),
        "readme_names": sorted(name.casefold() for name in readme_names),
        "shortlist_names": [name.casefold() for name in shortlist_names],
        "lane_counts": dict(lane_counts),
        "recalled_mechanisms": sorted(name.casefold() for name in recalled_mechanisms),
        "presented_mechanisms": sorted(name.casefold() for name in presented_mechanisms),
        "explored_directions": sorted(name.casefold() for name in explored_directions),
    }


def compare_base_artifacts(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    diffs: list[str] = []
    for key in (
        "query_fingerprints", "candidate_names", "readme_names", "shortlist_names",
        "lane_counts", "recalled_mechanisms", "presented_mechanisms", "explored_directions",
    ):
        if left.get(key) != right.get(key):
            diffs.append(key)
    return diffs


def public_hypothesis(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "term": record.get("term"),
        "request_anchor": record.get("request_anchor"),
        "reason": record.get("reason"),
        "source_iteration": record.get("source_iteration"),
        "status": derive_hypothesis_status(record),
        "queries": [
            {
                "kind": item.get("kind"),
                "query": item.get("query"),
                "skipped": bool(item.get("skipped")),
                "skip_reason": item.get("skip_reason"),
                "executed": bool(item.get("executed")),
            }
            for item in record.get("queries") or []
        ],
        "evidence_repos": list(record.get("evidence_repos") or []),
        "assessment_repo": record.get("assessment_repo"),
        "presented": bool(record.get("presented")),
        "overlap_repos": list(record.get("overlap_repos") or []),
    }


def merge_candidate_view(base: dict[str, Any], semantic: dict[str, Any]) -> dict[str, Any]:
    view = dict(base)
    mechanisms = list(base.get("mechanisms") or [])
    seen = {str(item.get("name") or "").casefold() for item in mechanisms}
    for mechanism in semantic.get("mechanisms") or []:
        key = str(mechanism.get("name") or "").casefold()
        if key and key not in seen:
            mechanisms.append(mechanism)
            seen.add(key)
    view["mechanisms"] = mechanisms
    evidence = list(base.get("evidence") or [])
    evidence_ids = {str(item.get("id")) for item in evidence}
    for item in semantic.get("evidence") or []:
        if str(item.get("id")) not in evidence_ids:
            evidence.append(item)
    view["evidence"] = evidence
    view["semantic_overlap"] = True
    return view


def sidecar_used_query_budget(state: dict[str, Any]) -> int:
    return sum(
        1 for item in state.get("queries") or []
        if not item.get("skipped") and item.get("kind") in SEMANTIC_QUERY_KINDS
        and item.get("executed")
    )
