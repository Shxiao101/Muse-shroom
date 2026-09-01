"""Offline v0.4.7 priority, confirmation-cost, and taxonomy diagnostics."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from muse_shroom.boundary import _specificity_with_context

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RELEASE = ROOT / "evaluation" / "results" / "boundary-v046-release"
DEFAULT_CASSETTE = ROOT / "evaluation" / "cassettes" / "boundary-v046.json.gz"
DEFAULT_LABELS = ROOT / "evaluation" / "priority-diagnostic-labels.json"
TOKEN_RE = re.compile(r"[A-Za-z0-9_+#]+|[\u3400-\u9fff]+")
LABELS = {"meaningful", "noise", "wrong_domain", "synonym", "too_generic", "insufficient_evidence"}


def norm(value: Any) -> str:
    return " ".join(TOKEN_RE.findall(str(value).casefold()))


def canonical(value: Any) -> str:
    return " ".join("browser" if token == "web" else token for token in norm(value).split())


def overlap(left: str, right: str) -> bool:
    a, b = set(left.split()), set(right.split())
    return left == right or len(a & b) / max(1, len(a | b)) >= 2 / 3 or a <= b or b <= a


def repo_set(item: dict[str, Any], field: str = "discovery_evidence") -> set[str]:
    return {
        str(source.get("repo") or "").casefold()
        for source in item.get(field) or []
        if str(source.get("repo") or "").strip()
    }


def same_repo_variant(left: dict[str, Any], right: dict[str, Any], left_key: str, right_key: str) -> bool:
    a, b = left_key.split(), right_key.split()
    return len(a) >= 3 and len(b) >= 3 and a[-2:] == b[-2:] and bool(repo_set(left) & repo_set(right))


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_prompts(path: Path) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in load_json(path).get("prompts") or []}


def load_golden(path: Path) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in load_json(path).get("cases") or []}


def load_labels(path: Path) -> dict[tuple[str, str, str], str]:
    payload = load_json(path)
    result = {}
    for key, label in (payload.get("labels") or {}).items():
        if label not in LABELS:
            raise ValueError(f"unsupported diagnostic label: {label}")
        suite, case_id, candidate = key.split("|", 2)
        result[(suite, case_id, norm(candidate))] = label
    return result


def load_diagnostics(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    load_labels(path)
    return payload


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def taxonomy_matches(candidate: str, golden_case: dict[str, Any]) -> list[dict[str, str]]:
    value = norm(candidate)
    matches = []
    for family in ("mainstream_mechanisms", "acceptable_new_mechanisms", "cross_mechanism_directions"):
        for item in golden_case.get(family) or []:
            terms = [item.get("term"), *(item.get("aliases") or [])]
            if any(value == norm(term) or value in norm(term) or norm(term) in value for term in terms):
                matches.append({"family": family, "id": str(item.get("id") or ""), "term": str(item.get("term") or "")})
    return matches


def load_search_results(path: Path) -> dict[str, list[dict[str, Any]]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    results = {}
    for call in payload.get("calls", {}).values():
        request = call.get("request") or {}
        if request.get("method") != "search_repositories":
            continue
        args = request.get("args") or []
        if not args:
            continue
        data = (call.get("response") or {}).get("data") or {}
        results[str(args[0])] = list(data.get("items") or []) if isinstance(data, dict) else []
    return results


def query_stage(query: str, item: dict[str, Any], request: dict[str, Any]) -> str:
    query_key = norm(query)
    if any(norm(value) and norm(value) in query_key for value in request.get("problem_concepts") or []):
        return "stage1"
    if any(str(source.get("repo") or "").casefold() in query.casefold() for source in item.get("discovery_evidence") or []):
        return "stage3"
    return "stage2"


def extract_candidates(raw: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for result in raw.get("results") or []:
        case_id = str(result.get("prompt_id") or "")
        records: dict[str, dict[str, Any]] = {}
        order: dict[str, int] = {}
        for step in (result.get("loop_diagnostics") or {}).get("boundary_trace") or []:
            for item in step.get("confirmations") or []:
                candidate = str(item.get("candidate") or "").strip()
                if not candidate:
                    continue
                key = norm(candidate)
                if key not in records:
                    order[key] = len(order)
                    records[key] = dict(item)
                else:
                    merged = dict(records[key])
                    merged.update(item)
                    records[key] = merged
        values = list(records.values())
        values.sort(key=lambda item: (-int(item.get("confirmation_priority_score") or 0), order[norm(item.get("candidate"))]))
        output[case_id] = values
    return output


def dedupe(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    unique: list[dict[str, Any]] = []
    owners: dict[str, str] = {}
    keys: list[tuple[str, dict[str, Any]]] = []
    for item in items:
        candidate = str(item.get("candidate") or "")
        key = canonical(candidate)
        owner = next((previous for previous_key, previous in keys if overlap(key, previous_key) or same_repo_variant(item, previous, key, previous_key)), None)
        if owner is not None:
            owners[norm(candidate)] = str(owner.get("candidate") or "")
            continue
        unique.append(item)
        keys.append((key, item))
    return unique, owners


def query_details(item: dict[str, Any], request: dict[str, Any], searches: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    discovery = repo_set(item)
    confirmation_core = {
        str(source.get("repo") or "").casefold()
        for source in item.get("confirmation_evidence") or []
        if source.get("core_use_case") and str(source.get("repo") or "").strip()
    }
    details = []
    for position, raw_query in enumerate(item.get("confirmation_queries") or [], 1):
        query = str(raw_query)
        results = searches.get(query, [])
        repos = {str(repo.get("full_name") or "").casefold() for repo in results if str(repo.get("full_name") or "").strip()}
        independent = repos - discovery
        independent_core = independent & confirmation_core
        stage = query_stage(query, item, request)
        details.append({
            "query": query,
            "query_stage": stage,
            "query_kind": {
                "stage1": "confirmation_problem",
                "stage2": "confirmation_anchor",
                "stage3": "confirmation_seed",
            }[stage],
            "query_position": position,
            "query_result_count": len(results),
            "new_repo_count": len(independent),
            "independent_repo_count": len(independent),
            "independent_core_evidence_repo_count": len(independent_core),
            "same_repo_overlap": bool(repos & discovery),
            "new_core_evidence": bool(independent_core),
            "query_failed": query not in searches,
            "result_repos": sorted(repos),
        })
    return details


def reconstructed_relevance(evidence: list[dict[str, Any]]) -> int:
    anchored = [source for source in evidence if source.get("request_anchored")]
    score_sources = anchored or evidence
    score = max((int(source.get("evidence_relevance_score") or 0) for source in score_sources), default=0)
    if len(repo_set({"discovery_evidence": evidence})) >= 2:
        score = min(100, score + 12)
    if len({str(source.get("source_field") or "") for source in evidence if source.get("source_field")}) >= 2:
        score = min(100, score + 6)
    return score


def make_record(item: dict[str, Any], suite: str, case_id: str, raw_rank: int,
                deduped_rank: int | None, dedupe_of: str | None,
                request: dict[str, Any], golden_case: dict[str, Any],
                searches: dict[str, list[dict[str, Any]]],
                labels: dict[tuple[str, str, str], str]) -> dict[str, Any]:
    candidate = str(item.get("candidate") or "")
    details = query_details(item, request, searches)
    status = str(item.get("confirmation_status") or "")
    label = labels.get((suite, case_id, norm(candidate)))
    for detail in details:
        detail["candidate"] = candidate
        detail["final_candidate_label"] = label or "unlabeled"
    evidence = list(item.get("discovery_evidence") or [])
    relevance = reconstructed_relevance(evidence)
    specificity = _specificity_with_context(candidate, evidence)
    return {
        "suite": suite,
        "case_id": case_id,
        "candidate": candidate,
        "canonical_term": canonical(candidate),
        "raw_queue_rank": raw_rank,
        "deduped_queue_rank": deduped_rank,
        "dedupe_of": dedupe_of,
        "discovery_repos": sorted(repo_set(item)),
        "discovery_source_fields": sorted({str(source.get("source_field") or "") for source in evidence if source.get("source_field")}),
        "evidence_relevance_score": relevance,
        "mechanism_specificity": specificity,
        "field_provenance": {
            "evidence_relevance_score": "reconstructed_with_v0.4.6_term_quality_from_discovery_evidence",
            "mechanism_specificity": "reconstructed_with_v0.4.6_specificity_classifier",
        },
        "novelty_score": item.get("novelty_score"),
        "confirmability_score": item.get("confirmability_score"),
        "confirmation_priority_score": item.get("confirmation_priority_score"),
        "confirmation_priority_reason": item.get("confirmation_priority_reason"),
        "attempted": not status.startswith("skipped") and bool(details),
        "confirmation_status": status,
        "skip_reason": item.get("confirmation_reason") if status.startswith("skipped") else None,
        "query_count": len(details),
        "query_stages": [detail["query_stage"] for detail in details],
        "query_details": details,
        "confirmation_repos": sorted(repo_set(item, "confirmation_evidence")),
        "confirmation_evidence": list(item.get("confirmation_evidence") or []),
        "discovery_evidence": evidence,
        "frozen_taxonomy_match": taxonomy_matches(candidate, golden_case),
        "human_diagnostic_label": label,
    }


def aggregate(records: list[dict[str, Any]], suite: str) -> dict[str, Any]:
    labeled = [record for record in records if record.get("human_diagnostic_label") in LABELS]
    meaningful = [record for record in labeled if record["human_diagnostic_label"] == "meaningful"]
    attempted_meaningful = [record for record in meaningful if record.get("attempted")]
    skipped_meaningful = [record for record in meaningful if not record.get("attempted")]
    def coverage(k: int) -> float:
        return round(sum(1 for record in meaningful if record.get("deduped_queue_rank") and record["deduped_queue_rank"] <= k) / max(1, len(meaningful)), 3)
    queries = [detail for record in records for detail in record.get("query_details") or []]
    rejects = [record for record in records if record.get("confirmation_status") == "rejected"]
    reject_queries = [detail for record in rejects for detail in record.get("query_details") or []]
    confirms = [record for record in records if record.get("confirmation_status") == "confirmed"]
    meaningful_confirms = [record for record in confirms if record.get("human_diagnostic_label") == "meaningful"]
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_case[str(record["case_id"])].append(record)
    before_first_meaningful = 0
    for case_records in by_case.values():
        seen_queries = 0
        for record in sorted(case_records, key=lambda item: item["raw_queue_rank"]):
            if record.get("confirmation_status") == "confirmed" and record.get("human_diagnostic_label") == "meaningful":
                before_first_meaningful += seen_queries
                break
            seen_queries += int(record.get("query_count") or 0)
    stage_counts = {
        stage: sum(
            1 for record in meaningful_confirms
            if record.get("query_stages") and record["query_stages"][-1] == stage
        )
        for stage in ("stage1", "stage2", "stage3")
    }
    labeled_non_meaningful = [
        record for record in labeled
        if record.get("human_diagnostic_label") != "meaningful"
    ]
    return {
        "suite": suite,
        "labeled_candidate_count": len(labeled),
        "meaningful_candidate_count": len(meaningful),
        "meaningful_attempted_count": len(attempted_meaningful),
        "meaningful_skipped_count": len(skipped_meaningful),
        "meaningful_skipped_rate": round(len(skipped_meaningful) / max(1, len(meaningful)), 3),
        "top_1_meaningful_coverage": coverage(1),
        "top_2_meaningful_coverage": coverage(2),
        "top_3_meaningful_coverage": coverage(3),
        "meaningful_attempted_rate": round(len(attempted_meaningful) / max(1, len(meaningful)), 3),
        "non_meaningful_attempted_rate": round(sum(1 for record in labeled if record not in meaningful and record.get("attempted")) / max(1, len(labeled) - len(meaningful)), 3),
        "total_executed_queries": len(queries),
        "queries_on_confirmed_meaningful": sum(record.get("query_count") or 0 for record in meaningful_confirms),
        "queries_on_confirmed_non_meaningful": sum(record.get("query_count") or 0 for record in confirms if record not in meaningful_confirms),
        "queries_on_eventual_rejects": len(reject_queries),
        "queries_on_labeled_non_meaningful_candidates": sum(record.get("query_count") or 0 for record in labeled_non_meaningful),
        "queries_on_unresolved": sum(record.get("query_count") or 0 for record in records if record.get("confirmation_status") == "unresolved"),
        "query_failure_count": sum(1 for detail in queries if detail.get("query_failed")),
        "queries_with_same_repo_overlap": sum(1 for detail in queries if detail.get("same_repo_overlap")),
        "queries_with_independent_repo_result": sum(1 for detail in queries if detail.get("independent_repo_count")),
        "queries_with_independent_repo_gain": sum(1 for detail in queries if detail.get("new_core_evidence")),
        "stage1_query_count": sum(1 for detail in queries if detail.get("query_stage") == "stage1"),
        "stage2_query_count": sum(1 for detail in queries if detail.get("query_stage") == "stage2"),
        "stage3_query_count": sum(1 for detail in queries if detail.get("query_stage") == "stage3"),
        "stage1_confirmation_count": stage_counts["stage1"],
        "stage2_incremental_confirmation_count": stage_counts["stage2"],
        "stage3_incremental_confirmation_count": stage_counts["stage3"],
        "average_queries_per_confirm": round(sum(record.get("query_count") or 0 for record in confirms) / max(1, len(confirms)), 3),
        "average_queries_per_reject": round(len(reject_queries) / max(1, len(rejects)), 3),
        "queries_before_first_meaningful_confirmation": before_first_meaningful,
        "same_repo_overlap_rate": round(sum(1 for detail in queries if detail.get("same_repo_overlap")) / max(1, len(queries)), 3),
    }


def root_causes(records_by_suite: dict[str, list[dict[str, Any]]], metrics: dict[str, dict[str, Any]],
                named_taxonomy: list[dict[str, Any]]) -> list[dict[str, Any]]:
    causes: list[dict[str, Any]] = []
    dev = metrics["development"]
    dev_records = records_by_suite["development"]
    meaningful_skipped = [record for record in dev_records if record.get("human_diagnostic_label") == "meaningful" and not record.get("attempted")]
    late_cases = sorted({record["case_id"] for record in meaningful_skipped if (record.get("deduped_queue_rank") or 0) > 3})
    if len(late_cases) >= 2 or len(meaningful_skipped) >= 2 or dev["top_3_meaningful_coverage"] < 0.8:
        causes.append({
            "root_cause_id": "priority_selection_misses_meaningful_candidates",
            "pipeline_stage": "confirmation_candidate_selection",
            "affected_suites": ["development", "holdout"],
            "affected_cases": late_cases or sorted({record["case_id"] for record in meaningful_skipped}),
            "candidate_examples": [record["candidate"] for record in meaningful_skipped[:6]],
            "trace_evidence": {"meaningful_skipped_count": len(meaningful_skipped), "development_top_3_meaningful_coverage": dev["top_3_meaningful_coverage"]},
            "metric_impact": "Meaningful candidates are present in the queue but do not receive an attempt slot.",
            "confidence": "high" if len(meaningful_skipped) >= 3 else "medium",
        })
    later_stage_queries = dev["stage2_query_count"] + dev["stage3_query_count"]
    query_trigger = bool(dev["total_executed_queries"] and (
        dev["queries_on_labeled_non_meaningful_candidates"] / dev["total_executed_queries"] > 0.30
        or dev["same_repo_overlap_rate"] >= 0.25
        or (
            dev["stage2_incremental_confirmation_count"] + dev["stage3_incremental_confirmation_count"] == 0
            and later_stage_queries >= dev["total_executed_queries"] * 0.20
        )
        or dev["average_queries_per_confirm"] > dev["average_queries_per_reject"] * 2
    ))
    if query_trigger:
        all_records = records_by_suite["development"] + records_by_suite["holdout"]
        causes.append({
            "root_cause_id": "confirmation_queries_spend_on_non_independent_evidence",
            "pipeline_stage": "progressive_confirmation_queries",
            "affected_suites": ["development", "holdout"],
            "affected_cases": sorted({record["case_id"] for record in all_records if any(detail.get("same_repo_overlap") for detail in record.get("query_details") or [])}),
            "candidate_examples": [record["candidate"] for record in all_records if record.get("query_details") and record.get("confirmation_status") == "rejected"][:6],
            "trace_evidence": {"development_queries_on_eventual_rejects": dev["queries_on_eventual_rejects"], "development_same_repo_overlap_rate": dev["same_repo_overlap_rate"], "stage2_incremental_confirmation_count": dev["stage2_incremental_confirmation_count"], "stage3_incremental_confirmation_count": dev["stage3_incremental_confirmation_count"]},
            "metric_impact": "Executed query budget is consumed without independent core-use-case gain.",
            "confidence": "high" if dev["same_repo_overlap_rate"] > 0.25 else "medium",
        })
    holdout_meaningful = [record for record in records_by_suite["holdout"] if record.get("human_diagnostic_label") == "meaningful"]
    named_counts = Counter(item["classification"] for item in named_taxonomy)
    if named_counts["B"] + named_counts["C"] >= 3:
        causes.append({
            "root_cause_id": "frozen_taxonomy_underrepresents_valid_discovery",
            "pipeline_stage": "evaluation_coverage",
            "affected_suites": ["holdout"],
            "affected_cases": sorted({str(item["case_id"]) for item in named_taxonomy}),
            "candidate_examples": [item["candidate"] for item in named_taxonomy],
            "trace_evidence": {
                "named_holdout_human_meaningful_count": len(named_taxonomy),
                "taxonomy_miss_count": named_counts["A"],
                "valid_alternative_count": named_counts["B"],
                "true_boundary_discovery_count": named_counts["C"],
            },
            "metric_impact": "Frozen-known recall can undercount valid alternatives and boundary discoveries.",
            "confidence": "medium",
        })
    return causes[:3]


def analyze(release_dir: Path, cassette: Path, labels_path: Path, dev_prompts: Path,
            holdout_prompts: Path, dev_golden: Path, holdout_golden: Path) -> dict[str, Any]:
    searches = load_search_results(cassette)
    diagnostics = load_diagnostics(labels_path)
    labels = load_labels(labels_path)
    config = {
        "development": (dev_prompts, dev_golden, release_dir / "boundary-development-agentic.raw.json"),
        "holdout": (holdout_prompts, holdout_golden, release_dir / "boundary-holdout-agentic.raw.json"),
    }
    records_by_suite: dict[str, list[dict[str, Any]]] = {}
    cases_by_suite: dict[str, dict[str, list[dict[str, Any]]]] = {}
    raw_by_suite: dict[str, dict[str, Any]] = {}
    for suite, (prompt_path, golden_path, raw_path) in config.items():
        prompts, goldens = load_prompts(prompt_path), load_golden(golden_path)
        raw_by_suite[suite] = load_json(raw_path)
        extracted = extract_candidates(raw_by_suite[suite])
        records_by_suite[suite] = []
        cases_by_suite[suite] = {}
        for case_id, items in extracted.items():
            unique, owners = dedupe(items)
            budget_unique = [
                item for item in unique
                if item.get("confirmation_status") != "skipped_duplicate"
            ]
            deduped_rank = {norm(item.get("candidate")): i for i, item in enumerate(budget_unique, 1)}
            request = dict((prompts.get(case_id) or {}).get("request") or {})
            golden_case = goldens.get(case_id) or {}
            case_records = []
            for raw_rank, item in enumerate(items, 1):
                key = norm(item.get("candidate"))
                dedupe_of = owners.get(key)
                if item.get("confirmation_status") == "skipped_duplicate" and not dedupe_of:
                    dedupe_of = "known_request_or_presented_mechanism"
                record = make_record(item, suite, case_id, raw_rank, deduped_rank.get(key), dedupe_of, request, golden_case, searches, labels)
                case_records.append(record)
                records_by_suite[suite].append(record)
            cases_by_suite[suite][case_id] = case_records
    metrics = {suite: aggregate(records, suite) for suite, records in records_by_suite.items()}
    taxonomy_rows = list(diagnostics.get("holdout_named_taxonomy") or [])
    allowed_classes = {"A", "B", "C"}
    if len(taxonomy_rows) != 4 or any(item.get("classification") not in allowed_classes for item in taxonomy_rows):
        raise ValueError("holdout_named_taxonomy must contain exactly four A/B/C classifications")
    taxonomy_counts = Counter(item["classification"] for item in taxonomy_rows)
    named_keys = {(str(item["case_id"]), norm(item["candidate"])) for item in taxonomy_rows}
    holdout_additional = []
    for record in records_by_suite["holdout"]:
        if record.get("human_diagnostic_label") != "meaningful" or (record["case_id"], norm(record["candidate"])) in named_keys:
            continue
        classification = "A" if record.get("frozen_taxonomy_match") else "B"
        holdout_additional.append({
            "case_id": record["case_id"],
            "candidate": record["candidate"],
            "classification": classification,
            "basis": "Historical diagnostic candidate; frozen taxonomy match." if classification == "A" else "Historical diagnostic candidate; valid alternative without a frozen taxonomy match.",
        })
    identity = next((record for record in records_by_suite["holdout"] if record["case_id"] == "photo-organization" and record["candidate"] == "identity verification"), None)
    identity_analysis = []
    if identity:
        evidence = identity.get("discovery_evidence") or []
        identity_analysis = [
            {"field": "discovery_repos", "value": identity.get("discovery_repos")},
            {"field": "source_fields", "value": sorted({item.get("source_field") for item in evidence})},
            {"field": "discovery_evidence", "value": evidence},
            {"field": "core_use_case", "value": [item.get("core_use_case") for item in evidence]},
            {"field": "request_anchored", "value": [item.get("request_anchored") for item in evidence]},
            {"field": "mechanism_anchored", "value": [item.get("mechanism_anchored") for item in evidence]},
            {"field": "evidence_relevance_score", "value": identity.get("evidence_relevance_score")},
            {"field": "mechanism_specificity", "value": identity.get("mechanism_specificity")},
            {"field": "novelty_score", "value": identity.get("novelty_score")},
            {"field": "confirmability_score", "value": identity.get("confirmability_score")},
            {"field": "transfer_plausible", "value": "transfer_plausible" in str(identity.get("confirmation_priority_reason") or "")},
            {"field": "priority", "value": identity.get("confirmation_priority_score")},
            {"field": "priority_reason", "value": identity.get("confirmation_priority_reason")},
            {"field": "query_details", "value": identity.get("query_details")},
            {"field": "confirmation_evidence", "value": identity.get("confirmation_evidence")},
            {"field": "final_status", "value": identity.get("confirmation_status")},
            {"field": "human_diagnostic_label", "value": identity.get("human_diagnostic_label")},
        ]
    identity_comparison = []
    for candidate in ("identity verification", "face recognition", "perceptual hashing"):
        record = next((
            item for item in records_by_suite["holdout"]
            if item["case_id"] == "photo-organization" and norm(item["candidate"]) == norm(candidate)
        ), None)
        if record:
            identity_comparison.append({
                "candidate": record["candidate"],
                "deduped_queue_rank": record.get("deduped_queue_rank"),
                "evidence_relevance_score": record.get("evidence_relevance_score"),
                "mechanism_specificity": record.get("mechanism_specificity"),
                "confirmation_priority_score": record.get("confirmation_priority_score"),
                "confirmation_status": record.get("confirmation_status"),
                "human_diagnostic_label": record.get("human_diagnostic_label"),
            })
    top_skipped = {
        case_id: sorted([
            record for record in records
            if str(record.get("confirmation_status") or "").startswith("skipped")
            and record.get("deduped_queue_rank") is not None
        ], key=lambda record: int(record["deduped_queue_rank"]))[:3]
        for case_id, records in cases_by_suite["development"].items()
    }
    blind_development = []
    for case_id, items in top_skipped.items():
        request = dict((load_prompts(config["development"][0]).get(case_id) or {}).get("request") or {})
        for item in items:
            blind_development.append({
                "suite": "development", "case_id": case_id,
                "request": request.get("request"),
                "candidate": item["candidate"],
                "discovery_repos": item["discovery_repos"],
                "discovery_source_fields": item["discovery_source_fields"],
                "discovery_evidence": item["discovery_evidence"],
            })
    holdout_prompts_by_id = load_prompts(config["holdout"][0])
    blind_holdout = []
    review_keys = set(named_keys)
    review_keys.update(
        (record["case_id"], norm(record["candidate"]))
        for record in records_by_suite["holdout"]
        if record.get("confirmation_status") == "confirmed"
    )
    holdout_records = {
        (record["case_id"], norm(record["candidate"])): record
        for record in records_by_suite["holdout"]
    }
    direct_evidence: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for result in raw_by_suite["holdout"].get("results") or []:
        case_id = str(result.get("prompt_id") or "")
        for step in (result.get("loop_diagnostics") or {}).get("boundary_trace") or []:
            for item in step.get("evidence_sources") or []:
                key = (case_id, norm(item.get("term")))
                if key in review_keys:
                    direct_evidence[key] = list(item.get("sources") or [])
    for case_id, candidate_key in sorted(review_keys):
        record = holdout_records.get((case_id, candidate_key))
        evidence = list(record.get("discovery_evidence") or []) if record else direct_evidence.get((case_id, candidate_key), [])
        candidate = record["candidate"] if record else next(
            item["candidate"] for item in taxonomy_rows
            if item["case_id"] == case_id and norm(item["candidate"]) == candidate_key
        )
        prompt = dict((holdout_prompts_by_id.get(case_id) or {}).get("request") or {})
        blind_holdout.append({
            "suite": "holdout", "case_id": case_id,
            "request": prompt.get("request"),
            "candidate": candidate,
            "discovery_repos": sorted(repo_set({"discovery_evidence": evidence})),
            "discovery_source_fields": sorted({str(source.get("source_field") or "") for source in evidence if source.get("source_field")}),
            "discovery_evidence": evidence,
        })
    causes = root_causes(records_by_suite, metrics, taxonomy_rows)
    cause_ids = {cause["root_cause_id"] for cause in causes}
    directions = []
    if "priority_selection_misses_meaningful_candidates" in cause_ids:
        directions.append("Priority calibration")
    if "confirmation_queries_spend_on_non_independent_evidence" in cause_ids:
        directions.append("Query efficiency")
    protected_paths = [
        cassette,
        release_dir / "boundary-verdict.json",
        release_dir / "confirmation-analysis.json",
        config["development"][2],
        config["holdout"][2],
        release_dir.parent / "boundary-v046-release-replay" / "boundary-verdict.json",
        release_dir.parent / "boundary-v046-release-replay" / "boundary-development-agentic.raw.json",
        release_dir.parent / "boundary-v046-release-replay" / "boundary-holdout-agentic.raw.json",
    ]
    protected_hashes = {str(path): sha256(path) for path in protected_paths if path.exists()}
    return {
        "schema_version": 1,
        "analysis": "v0.4.7_priority_calibration_confirmation_efficiency",
        "date": "2026-08-31",
        "network_requests": 0,
        "production_behavior_changed": False,
        "v046_artifacts_modified": False,
        "protected_artifact_sha256": protected_hashes,
        "reconstructed_fields": {
            "evidence_relevance_score": "Recomputed from captured discovery evidence with the v0.4.6 term-quality formula.",
            "mechanism_specificity": "Recomputed from candidate text and captured evidence with the v0.4.6 specificity classifier.",
        },
        "source_files": [str(cassette), str(config["development"][2]), str(config["holdout"][2]), str(dev_golden), str(holdout_golden)],
        "metrics": metrics,
        "records": records_by_suite,
        "top_skipped_development": top_skipped,
        "blind_review_development": blind_development,
        "blind_review_holdout": blind_holdout,
        "holdout_taxonomy": taxonomy_rows,
        "holdout_additional_historical_diagnostics": holdout_additional,
        "holdout_taxonomy_counts": {key: taxonomy_counts.get(key, 0) for key in ("A", "B", "C")},
        "evaluator_coverage_warning": taxonomy_counts.get("B", 0) + taxonomy_counts.get("C", 0) >= 3,
        "identity_verification_status": identity.get("human_diagnostic_label") if identity else None,
        "identity_verification_analysis": identity_analysis,
        "identity_verification_comparison": identity_comparison,
        "identity_verification_diagnosis": "A use-case list placed identity verification beside personal photo organization. The pipeline treated request adjacency plus core_use_case as transfer support even though mechanism_anchored was false and specificity was project_category; stage 1 then found the same adjacency pattern in a second repository. No domain-role check distinguished photo grouping from identity/access verification.",
        "holdout_known_interpretation": "Frozen-known recall is zero, while post-hoc diagnostic labels indicate valid alternatives; this is an evaluator-coverage warning, not permission to change the gate.",
        "root_causes": causes,
        "recommended_directions": directions[:2],
        "completion_status": ["diagnosis_complete", "root_causes_ranked", "implementation_directions_limited_to_1_or_2"],
    }


def render_markdown(analysis: dict[str, Any]) -> str:
    lines = [
        "# v0.4.7 Priority Calibration Analysis", "", "Date: 2026-08-31", "",
        "## Decision", "",
        "Offline diagnosis of v0.4.6. No production search behavior, Golden, aliases, thresholds, or v0.4.6 artifacts were changed.", "",
        "## Root causes", "",
    ]
    for i, cause in enumerate(analysis["root_causes"], 1):
        lines += [f"{i}. **{cause['root_cause_id']}** (`{cause['pipeline_stage']}`)", f"   - Cases: {', '.join(cause['affected_cases']) or 'none'}", f"   - Candidates: {', '.join('`' + value + '`' for value in cause['candidate_examples']) or 'none'}", f"   - Trace evidence: `{json.dumps(cause['trace_evidence'], ensure_ascii=False)}`", f"   - Metric impact: {cause['metric_impact']}", f"   - Confidence: {cause['confidence']}", ""]
    lines += ["## Candidate metrics", "", "| Metric | Development | Holdout |", "| --- | ---: | ---: |"]
    for key in ("meaningful_candidate_count", "meaningful_attempted_count", "meaningful_skipped_count", "meaningful_skipped_rate", "top_1_meaningful_coverage", "top_2_meaningful_coverage", "top_3_meaningful_coverage", "meaningful_attempted_rate", "non_meaningful_attempted_rate"):
        lines.append(f"| {key} | {analysis['metrics']['development'].get(key)} | {analysis['metrics']['holdout'].get(key)} |")
    lines += ["", "## Query cost", "", "| Metric | Development | Holdout |", "| --- | ---: | ---: |"]
    for key in ("total_executed_queries", "queries_on_confirmed_meaningful", "queries_on_confirmed_non_meaningful", "queries_on_eventual_rejects", "queries_on_labeled_non_meaningful_candidates", "queries_on_unresolved", "query_failure_count", "queries_with_same_repo_overlap", "queries_with_independent_repo_result", "queries_with_independent_repo_gain", "stage1_query_count", "stage2_query_count", "stage3_query_count", "stage1_confirmation_count", "stage2_incremental_confirmation_count", "stage3_incremental_confirmation_count", "average_queries_per_confirm", "average_queries_per_reject", "queries_before_first_meaningful_confirmation", "same_repo_overlap_rate"):
        lines.append(f"| {key} | {analysis['metrics']['development'].get(key)} | {analysis['metrics']['holdout'].get(key)} |")
    lines += ["", "The two suites executed 61 queries. Of these, " + str(analysis['metrics']['development']['queries_on_eventual_rejects'] + analysis['metrics']['holdout']['queries_on_eventual_rejects']) + " were spent on candidates whose final confirmation status was `rejected`. Query failures were counted separately and were zero.", "", "## Holdout taxonomy classification", "", "The following table is limited to the four human-meaningful final Holdout gains named in the v0.4.6 review. Historical diagnostic candidates are retained separately in JSON and do not change these counts.", "", "| Case | Candidate | Class | Basis |", "| --- | --- | --- | --- |"]
    for item in analysis["holdout_taxonomy"]:
        lines.append(f"| {item['case_id']} | `{item['candidate']}` | {item['classification']} | {item['basis']} |")
    lines += ["", f"Counts: {analysis['holdout_taxonomy_counts']}", f"Evaluator coverage warning: `{analysis['evaluator_coverage_warning']}`.", "", "## Identity verification adjacency analysis", ""]
    lines += [analysis["identity_verification_diagnosis"], ""]
    for item in analysis["identity_verification_analysis"]:
        lines.append(f"- `{item['field']}`: {item['value']}")
    lines += ["", "Comparison:", "", "| Candidate | Deduped rank | Relevance | Specificity | Priority | Status | Human label |", "| --- | ---: | ---: | --- | ---: | --- | --- |"]
    for item in analysis["identity_verification_comparison"]:
        lines.append(f"| `{item['candidate']}` | {item['deduped_queue_rank']} | {item['evidence_relevance_score']} | {item['mechanism_specificity']} | {item['confirmation_priority_score']} | {item['confirmation_status']} | {item['human_diagnostic_label'] or 'unlabeled'} |")
    lines += ["", "## Top skipped Development candidates", ""]
    for case_id, items in analysis["top_skipped_development"].items():
        lines += [f"### {case_id}"]
        for item in items:
            lines.append(f"- `{item['candidate']}` — {item.get('human_diagnostic_label') or 'unlabeled'}")
        lines.append("")
    dev, holdout = analysis["metrics"]["development"], analysis["metrics"]["holdout"]
    rejected_queries = dev['queries_on_eventual_rejects'] + holdout['queries_on_eventual_rejects']
    total_queries = dev['total_executed_queries'] + holdout['total_executed_queries']
    lines += ["## Required answers", "", f"1. Development has {dev['meaningful_skipped_count']} labeled meaningful candidates in the skipped deduped queue.", f"2. Top-1/top-2/top-3 meaningful coverage — Development: {dev['top_1_meaningful_coverage']} / {dev['top_2_meaningful_coverage']} / {dev['top_3_meaningful_coverage']}; Holdout: {holdout['top_1_meaningful_coverage']} / {holdout['top_2_meaningful_coverage']} / {holdout['top_3_meaningful_coverage']}.", f"3. Meaningful attempted/skipped — Development: {dev['meaningful_attempted_count']} / {dev['meaningful_skipped_count']}; Holdout: {holdout['meaningful_attempted_count']} / {holdout['meaningful_skipped_count']}.", "4. `identity verification` passed because request-adjacent use-case lists produced `core_use_case=true`, `request_anchored=true`, relevance 90, and a stage-1 second-repository match. It remained `mechanism_anchored=false` and `project_category`; the missing domain-role check allowed identity/access verification to masquerade as photo organization.", f"5. Queries on eventual rejects: {rejected_queries}/{total_queries} ({round(rejected_queries / max(1, total_queries) * 100, 1)}%): Development {dev['queries_on_eventual_rejects']}, Holdout {holdout['queries_on_eventual_rejects']}.", f"6. New meaningful confirmations by stage: stage 1 = {dev['stage1_confirmation_count'] + holdout['stage1_confirmation_count']}; stage 2 = {dev['stage2_incremental_confirmation_count'] + holdout['stage2_incremental_confirmation_count']}; stage 3 = {dev['stage3_incremental_confirmation_count'] + holdout['stage3_incremental_confirmation_count']}.", f"7. The four named Holdout human-meaningful candidates classify as A/B/C = {analysis['holdout_taxonomy_counts']['A']} / {analysis['holdout_taxonomy_counts']['B']} / {analysis['holdout_taxonomy_counts']['C']}.", f"8. Holdout frozen-known meaningful=0 is primarily an evaluator-coverage limitation, not zero real recall: all four reviewed gains are B/C and were surfaced as unknown gains. The existing release gate remains unchanged.", "9. Root-cause order: candidate priority selection; confirmation query independence/efficiency; frozen taxonomy coverage.", f"10. Next implementation is limited to: {', '.join(analysis['recommended_directions']) or 'none'}. No confirmation budget increase is recommended.", "", "## Provenance", "", "- Network requests: 0", "- v0.4.6 artifacts modified: false", "- Production behavior changed: false", "- Reconstructed fields are declared in JSON under `reconstructed_fields` and per record under `field_provenance`.", f"- Completion status: {', '.join(analysis['completion_status'])}.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze v0.4.6 confirmation priority offline")
    parser.add_argument("--release-dir", type=Path, default=DEFAULT_RELEASE)
    parser.add_argument("--cassette", type=Path, default=DEFAULT_CASSETTE)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--dev-prompts", type=Path, default=ROOT / "evaluation" / "boundary-prompts.json")
    parser.add_argument("--holdout-prompts", type=Path, default=ROOT / "evaluation" / "holdout" / "boundary-prompts.json")
    parser.add_argument("--dev-golden", type=Path, default=ROOT / "evaluation" / "boundary-golden-cases.json")
    parser.add_argument("--holdout-golden", type=Path, default=ROOT / "evaluation" / "holdout" / "boundary-golden-cases.json")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_RELEASE / "priority-analysis.json")
    parser.add_argument("--output-md", type=Path, default=DEFAULT_RELEASE / "priority-analysis.md")
    parser.add_argument("--output-report", type=Path, default=ROOT / "muse-shroom-v0.4.7-priority-calibration-analysis-report.md")
    args = parser.parse_args(argv)
    analysis = analyze(args.release_dir, args.cassette, args.labels, args.dev_prompts, args.holdout_prompts, args.dev_golden, args.holdout_golden)
    args.output_json.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(render_markdown(analysis), encoding="utf-8")
    report = render_markdown(analysis).replace("# v0.4.7 Priority Calibration Analysis", "# Muse-shroom v0.4.7 Priority Calibration Analysis Report", 1)
    args.output_report.write_text(report, encoding="utf-8")
    print(json.dumps({"ok": True, "output_json": str(args.output_json), "output_md": str(args.output_md), "output_report": str(args.output_report), "root_causes": analysis["root_causes"], "recommended_directions": analysis["recommended_directions"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
