from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN = ROOT / "evaluation" / "boundary-golden-cases.json"
DEFAULT_HOLDOUT_GOLDEN = ROOT / "evaluation" / "holdout" / "boundary-golden-cases.json"
TOKEN_RE = re.compile(r"[A-Za-z0-9_+#]+|[\u3400-\u9fff]+")
FORMAL_METRICS = (
    "retrieval_mechanism_redundancy", "presentation_mechanism_redundancy",
    "mechanism_redundancy", "redundancy_scope", "new_mechanisms_per_iteration", "boundary_gain_per_iteration",
    "meaningful_boundary_gain", "unknown_boundary_gain", "invalid_boundary_gain",
    "mainstream_coverage", "new_mechanism_match_count", "cross_mechanism_discovery",
    "repetition_penalty", "direction_coverage", "presented_mechanism_count",
    "duplicate_query_rate", "unexplored_directions_at_stop",
    "planned_iteration_count", "executed_iteration_count",
    "retrieval_changing_iteration_count",
    "confirmation_planned_count", "confirmation_executed_count",
    "confirmation_candidates_total", "confirmation_candidates_attempted",
    "confirmation_candidates_skipped", "confirmation_skipped_count",
    "confirmation_budget_exhausted_count",
    "confirmation_confirmed_count", "confirmation_rejected_count",
    "confirmation_precision", "confirmation_recall",
    "confirmed_meaningful_count", "confirmed_wrong_domain_count",
    "confirmed_synonym_count", "confirmed_per_attempted_candidate",
    "meaningful_per_attempted_candidate", "queries_per_confirmed_mechanism",
    "queries_per_meaningful_confirmation", "confirmation_cost_per_confirmed_mechanism",
)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _normalized(value: Any) -> str:
    return " ".join(TOKEN_RE.findall(str(value).casefold()))


def _concept_terms(concept: dict[str, Any]) -> set[str]:
    return {
        normalized
        for value in [concept.get("term"), *(concept.get("aliases") or [])]
        if (normalized := _normalized(value))
    }


def _matches(value: Any, concepts: Iterable[dict[str, Any]]) -> set[str]:
    normalized = _normalized(value)
    if not normalized:
        return set()
    padded = f" {normalized} "
    matched: set[str] = set()
    for concept in concepts:
        for term in _concept_terms(concept):
            if term == normalized or f" {term} " in padded or f" {normalized} " in f" {term} ":
                matched.add(str(concept["id"]))
                break
    return matched


def _queries_changed(trace: list[dict[str, Any]]) -> bool | None:
    rounds = [
        {_normalized(query) for query in item.get("queries") or [] if _normalized(query)}
        for item in trace if item.get("stage") in {"search", "iterate", "expand"}
    ]
    if len(rounds) < 2:
        return None
    return any(rounds[index] != rounds[0] for index in range(1, len(rounds)))


def load_golden(path: Path = DEFAULT_GOLDEN) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 2 or not isinstance(payload.get("cases"), list):
        raise ValueError("golden cases must use schema_version 2")
    cases: dict[str, dict[str, Any]] = {}
    for case in payload["cases"]:
        case_id = str(case.get("id") or "").strip()
        if not case_id or case_id in cases:
            raise ValueError("golden cases require unique non-empty ids")
        for field in (
            "mainstream_mechanisms", "acceptable_new_mechanisms",
            "cross_mechanism_directions", "repetition_groups",
        ):
            if not isinstance(case.get(field), list) or not case[field]:
                raise ValueError(f"golden case {case_id} requires {field}")
        cases[case_id] = case
    return cases


def _candidate_mechanisms(candidates: Iterable[dict[str, Any]]) -> list[str]:
    return [
        str(mechanism.get("name"))
        for candidate in candidates for mechanism in candidate.get("mechanisms") or []
        if _normalized(mechanism.get("name"))
    ]


def _redundancy(mechanisms: Iterable[str]) -> float:
    values = [_normalized(value) for value in mechanisms if _normalized(value)]
    return round((len(values) - len(set(values))) / max(1, len(values)), 3)


def _presentation_mechanisms(result: dict[str, Any]) -> tuple[list[str], str]:
    candidates = list(result.get("candidates") or [])
    recalled = list(result.get("recalled_candidates") or [])
    ranking_items = list((result.get("ranking") or {}).get("items") or [])
    if ranking_items:
        by_repo = {
            str(item.get("repo") or item.get("full_name") or "").casefold(): item
            for item in [*candidates, *recalled]
        }
        mechanisms: list[str] = []
        for item in ranking_items:
            repo = str(item.get("repo") or item.get("full_name") or "").casefold()
            candidate = by_repo.get(repo)
            mechanisms.extend(
                _candidate_mechanisms([candidate]) if candidate is not None
                else [str(value) for value in item.get("new_mechanisms") or []]
            )
        return mechanisms, "ranking_items"
    selected = [item for item in candidates if item.get("selected_for_assessment")]
    if selected:
        return _candidate_mechanisms(selected), "selected_for_assessment"
    return _candidate_mechanisms(candidates), "candidates"


def _unknown_entry(term: str, step: dict[str, Any], iteration: int) -> dict[str, Any]:
    term_key = _normalized(term)
    evidence = [
        item for item in step.get("evidence_sources") or []
        if not term_key or _normalized(item.get("term")) == term_key
    ]
    if not evidence:
        evidence = list(step.get("evidence_sources") or [])
    repos = sorted({
        str(source.get("repo"))
        for item in evidence for source in item.get("sources") or [] if source.get("repo")
    })
    return {
        "term": term, "status": "needs_review", "evidence_sources": evidence,
        "iteration": iteration, "repos": repos,
    }


def _golden_quality(result: dict[str, Any], case: dict[str, Any] | None,
                    presentation_mechanisms: list[str], redundancy_scope: str) -> dict[str, Any]:
    if case is None:
        return {
            "golden_case_found": False, "mainstream_coverage": None,
            "new_mechanism_matches": [], "new_mechanism_match_count": 0,
            "cross_mechanism_matches": [], "cross_mechanism_discovery": False,
            "repetition_violations": [], "repetition_penalty": 0.0,
            "meaningful_boundary_gain": 0, "unknown_boundary_gain": 0,
            "invalid_boundary_gain": 0, "unknown_mechanisms": [],
            "invalid_mechanisms": [], "boundary_quality_passed": False,
        }
    trace = list((result.get("loop_diagnostics") or {}).get("boundary_trace") or [])
    initial = next((item for item in trace if item.get("stage") == "search"), trace[0] if trace else {})
    initial_mechanisms = list(initial.get("mechanisms_found") or [])
    later_steps = [item for item in trace if item is not initial]
    later_mechanisms = [
        str(value) for item in later_steps for value in item.get("new_mechanisms") or []
        if _normalized(value)
    ]
    cross_signal_values: list[Any] = list(later_mechanisms)
    for item in later_steps:
        cross_signal_values.extend(item.get("directions_uncovered") or [])
        cross_signal_values.extend(
            evidence.get("term") for evidence in item.get("evidence_sources") or []
        )

    mainstream = case["mainstream_mechanisms"]
    mainstream_matches = set().union(*(_matches(value, mainstream) for value in initial_mechanisms), set())
    mainstream_coverage = len(mainstream_matches) / max(1, len(mainstream))
    acceptable = case["acceptable_new_mechanisms"]
    new_matches = set().union(*(_matches(value, acceptable) for value in later_mechanisms), set())
    cross = case["cross_mechanism_directions"]
    cross_matches = set().union(*(_matches(value, cross) for value in cross_signal_values), set())

    repetition_violations = []
    repeated = 0
    for group in case["repetition_groups"]:
        count = sum(bool(_matches(value, [group])) for value in presentation_mechanisms)
        maximum = int(group.get("max_results") or 0)
        if count > maximum:
            repetition_violations.append({
                "id": group["id"], "count": count, "max_results": maximum,
                "scope": redundancy_scope,
            })
            repeated += count - maximum

    invalid: list[str] = []
    unknown: list[dict[str, Any]] = []
    for iteration, step in enumerate(later_steps, 1):
        for raw in step.get("new_mechanisms") or []:
            term = str(raw)
            if _matches(term, acceptable):
                continue
            if _matches(term, mainstream) or any(
                _matches(term, [group]) for group in case["repetition_groups"]
            ):
                invalid.append(term)
            else:
                unknown.append(_unknown_entry(term, step, iteration))

    thresholds = case.get("thresholds") or {}
    meaningful_gain = len(new_matches)
    quality_passed = (
        mainstream_coverage >= float(thresholds.get("min_mainstream_coverage", 0.34))
        and meaningful_gain >= int(thresholds.get("min_meaningful_new_mechanisms", 1))
        and (not thresholds.get("require_cross_mechanism", True) or bool(cross_matches))
        and not repetition_violations and not invalid
    )
    return {
        "golden_case_found": True, "mainstream_coverage": round(mainstream_coverage, 3),
        "mainstream_matches": sorted(mainstream_matches),
        "new_mechanism_matches": sorted(new_matches), "new_mechanism_match_count": len(new_matches),
        "cross_mechanism_matches": sorted(cross_matches), "cross_mechanism_discovery": bool(cross_matches),
        "repetition_violations": repetition_violations,
        "repetition_penalty": round(repeated / max(1, len(presentation_mechanisms)), 3),
        "meaningful_boundary_gain": meaningful_gain, "unknown_boundary_gain": len(unknown),
        "invalid_boundary_gain": len(invalid), "unknown_mechanisms": unknown,
        "invalid_mechanisms": invalid, "boundary_quality_passed": quality_passed,
    }


def case_metrics(result: dict[str, Any], case: dict[str, Any] | None = None) -> dict[str, Any]:
    diagnostics = result.get("boundary_diagnostics") or {}
    loop = result.get("loop_diagnostics") or {}
    trace = list(loop.get("boundary_trace") or [])
    missing_evidence = []
    for step in trace:
        hypothesis = step.get("hypothesis") or {}
        expected = {
            _normalized(term): str(term)
            for term in hypothesis.get("promote_discovered_terms") or []
        }
        expected.update({
            _normalized(item.get("term")): str(item.get("term") or "")
            for item in hypothesis.get("add_exploration_directions") or []
            if isinstance(item, dict) and _normalized(item.get("term"))
        })
        evidenced = {
            _normalized(item.get("term")) for item in step.get("evidence_sources") or []
            if _normalized(item.get("term"))
        }
        missing_evidence.extend(term for key, term in expected.items() if key not in evidenced)

    recalled = list(result.get("recalled_candidates") or result.get("candidates") or [])
    presentation, scope = _presentation_mechanisms(result)
    retrieval_redundancy = float(
        diagnostics.get("retrieval_mechanism_redundancy", _redundancy(_candidate_mechanisms(recalled)))
    )
    presentation_redundancy = float(
        diagnostics.get("presentation_mechanism_redundancy", _redundancy(presentation))
    )
    scope = str(diagnostics.get("redundancy_scope") or scope)
    gains = list(loop.get("boundary_gain_per_iteration") or [])
    iteration_steps = [
        item for item in trace if item.get("stage") in {"iterate", "expand"}
    ]
    planned_iterations = int(
        loop.get("planned_iteration_count")
        if loop.get("planned_iteration_count") is not None
        else len(iteration_steps)
    )
    executed_iterations = int(
        loop.get("executed_iteration_count")
        if loop.get("executed_iteration_count") is not None
        else sum(bool(item.get("queries")) for item in iteration_steps)
    )
    retrieval_changing_iterations = int(
        loop.get("retrieval_changing_iteration_count")
        if loop.get("retrieval_changing_iteration_count") is not None
        else sum(bool(item.get("new_mechanisms")) for item in iteration_steps if item.get("queries"))
    )
    confirmations: list[dict[str, Any]] = []
    seen_confirmations: set[tuple[str, str]] = set()
    for step in trace:
        for item in step.get("confirmations") or []:
            identity = (
                _normalized(item.get("candidate")),
                str(item.get("confirmation_status") or ""),
            )
            if not identity[0] or identity in seen_confirmations:
                continue
            seen_confirmations.add(identity)
            confirmations.append(item)
    confirmed_terms = [
        str(item.get("candidate") or "") for item in confirmations
        if item.get("confirmation_status") == "confirmed"
    ]
    acceptable = (case or {}).get("acceptable_new_mechanisms") or []
    mainstream = (case or {}).get("mainstream_mechanisms") or []
    confirmed_meaningful_matches = set().union(
        *(_matches(term, acceptable) for term in confirmed_terms), set()
    )
    confirmed_synonyms = sum(bool(_matches(term, mainstream)) for term in confirmed_terms)
    confirmation_query_count = int(loop.get("confirmation_query_count") or 0)
    confirmation_confirmed_count = int(
        loop.get("confirmation_confirmed_count")
        if loop.get("confirmation_confirmed_count") is not None
        else len(confirmed_terms)
    )
    confirmation_attempted_count = int(
        loop.get("confirmation_candidates_attempted")
        if loop.get("confirmation_candidates_attempted") is not None
        else loop.get("confirmation_executed_count") or 0
    )
    return {
        "prompt_id": result.get("prompt_id"),
        "retrieval_mechanism_redundancy": retrieval_redundancy,
        "presentation_mechanism_redundancy": presentation_redundancy,
        "mechanism_redundancy": presentation_redundancy, "redundancy_scope": scope,
        "new_mechanisms_per_iteration": list(loop.get("new_mechanisms_per_iteration") or []),
        "boundary_gain_per_iteration": gains, "boundary_gain": sum(int(value) for value in gains),
        "direction_coverage": float(diagnostics.get("direction_coverage") or 0),
        "presented_mechanism_count": int(
            diagnostics.get("presented_mechanism_count") or len(set(map(_normalized, presentation)))
        ),
        "duplicate_query_rate": float(loop.get("duplicate_query_rate") or 0),
        "unexplored_directions_at_stop": list(loop.get("unexplored_directions_at_stop") or []),
        "iterations_used": int(loop.get("iterations_used") or 0),
        "planned_iteration_count": planned_iterations,
        "executed_iteration_count": executed_iterations,
        "retrieval_changing_iteration_count": retrieval_changing_iterations,
        "confirmation_planned_count": int(loop.get("confirmation_planned_count") or 0),
        "confirmation_executed_count": int(loop.get("confirmation_executed_count") or 0),
        "confirmation_candidates_total": int(
            loop.get("confirmation_candidates_total")
            if loop.get("confirmation_candidates_total") is not None
            else loop.get("confirmation_planned_count") or 0
        ),
        "confirmation_candidates_attempted": confirmation_attempted_count,
        "confirmation_candidates_skipped": int(
            loop.get("confirmation_candidates_skipped") or 0
        ),
        "confirmation_skipped_count": int(loop.get("confirmation_skipped_count") or 0),
        "confirmation_budget_exhausted_count": int(
            loop.get("confirmation_budget_exhausted_count") or 0
        ),
        "confirmation_confirmed_count": confirmation_confirmed_count,
        "confirmation_rejected_count": int(loop.get("confirmation_rejected_count") or 0),
        "confirmation_unresolved_count": int(loop.get("confirmation_unresolved_count") or 0),
        "confirmation_query_count": confirmation_query_count,
        "confirmation_precision": round(
            len(confirmed_meaningful_matches) / max(1, confirmation_confirmed_count), 3
        ),
        "confirmation_recall": round(
            len(confirmed_meaningful_matches) / max(1, len(acceptable)), 3
        ),
        "confirmed_meaningful_count": len(confirmed_meaningful_matches),
        "confirmed_wrong_domain_count": None,
        "confirmed_synonym_count": confirmed_synonyms,
        "confirmed_per_attempted_candidate": round(
            confirmation_confirmed_count / max(1, confirmation_attempted_count), 3
        ),
        "meaningful_per_attempted_candidate": round(
            len(confirmed_meaningful_matches) / max(1, confirmation_attempted_count), 3
        ),
        "queries_per_confirmed_mechanism": round(
            confirmation_query_count / max(1, confirmation_confirmed_count), 3
        ),
        "queries_per_meaningful_confirmation": round(
            confirmation_query_count / max(1, len(confirmed_meaningful_matches)), 3
        ),
        "confirmation_cost_per_confirmed_mechanism": round(
            confirmation_query_count / max(1, confirmation_confirmed_count), 3
        ),
        "queries_changed_after_initial": _queries_changed(trace),
        "evidence_backed_promotions": not missing_evidence,
        "missing_promotion_evidence": missing_evidence,
        "boundary_expanded": sum(int(value) for value in gains) > 0,
        **_golden_quality(result, case, presentation, scope),
    }


def summarize(payload: dict[str, Any], golden_cases: dict[str, dict[str, Any]] | None = None,
              *, suite: str = "development") -> dict[str, Any]:
    raw_results = payload.get("results")
    if not isinstance(raw_results, list) or not raw_results:
        raise ValueError("results must be a non-empty array")
    golden_cases = golden_cases if golden_cases is not None else load_golden()
    cases = [case_metrics(item, golden_cases.get(str(item.get("prompt_id")))) for item in raw_results]
    agentic = [item for item in cases if item["executed_iteration_count"] > 0]
    scored = [item for item in cases if item["golden_case_found"]]
    unknown_count = sum(item["unknown_boundary_gain"] for item in scored)
    if agentic and len(scored) == len(cases):
        expanded_share = sum(item["boundary_expanded"] for item in cases) / len(cases)
        meaningful_share = sum(item["meaningful_boundary_gain"] > 0 for item in cases) / len(cases)
        passed_quality = (
            len(agentic) == len(cases)
            and all(item["evidence_backed_promotions"] for item in cases)
            and all(item["duplicate_query_rate"] <= 0.5 for item in cases)
            and all(item["queries_changed_after_initial"] is not False for item in cases)
            and all(item["boundary_quality_passed"] for item in cases)
            and expanded_share >= 0.5 and meaningful_share >= 0.5
        )
        reviewable_unknown = bool(unknown_count) and (
            all(item["evidence_backed_promotions"] for item in cases)
            and all(item["duplicate_query_rate"] <= 0.5 for item in cases)
            and all(item["queries_changed_after_initial"] is not False for item in cases)
            and all(not item["repetition_violations"] for item in scored)
            and all(not item["invalid_boundary_gain"] for item in scored)
            and expanded_share >= 0.5
        )
        if unknown_count and (passed_quality or reviewable_unknown):
            verdict, passed = "needs_review", None
        elif not passed_quality:
            verdict, passed = "fail", False
        else:
            verdict, passed = "pass", True
    else:
        expanded_share = meaningful_share = 0.0
        verdict, passed = "insufficient_data", None
    result = {
        "schema_version": 4, "suite": suite, "formal_metrics": list(FORMAL_METRICS),
        "case_count": len(cases), "agentic_case_count": len(agentic),
        "golden_case_count": len(scored),
        "aggregate": {
            "median_retrieval_mechanism_redundancy": statistics.median(
                item["retrieval_mechanism_redundancy"] for item in cases
            ),
            "median_presentation_mechanism_redundancy": statistics.median(
                item["presentation_mechanism_redundancy"] for item in cases
            ),
            "median_direction_coverage": statistics.median(item["direction_coverage"] for item in cases),
            "median_presented_mechanism_count": statistics.median(item["presented_mechanism_count"] for item in cases),
            "agentic_boundary_expansion_share": round(expanded_share, 3),
            "meaningful_boundary_expansion_share": round(meaningful_share, 3),
            "unknown_mechanism_review_count": unknown_count,
            "planned_iteration_count": sum(item["planned_iteration_count"] for item in cases),
            "executed_iteration_count": sum(item["executed_iteration_count"] for item in cases),
            "retrieval_changing_iteration_count": sum(
                item["retrieval_changing_iteration_count"] for item in cases
            ),
            "executed_iteration_case_share": round(len(agentic) / len(cases), 3),
            "duplicate_only_iteration_count": sum(
                max(0, item["planned_iteration_count"] - item["executed_iteration_count"])
                for item in cases
            ),
            "confirmation_planned_count": sum(
                item["confirmation_planned_count"] for item in cases
            ),
            "confirmation_executed_count": sum(
                item["confirmation_executed_count"] for item in cases
            ),
            "confirmation_candidates_total": sum(
                item["confirmation_candidates_total"] for item in cases
            ),
            "confirmation_candidates_attempted": sum(
                item["confirmation_candidates_attempted"] for item in cases
            ),
            "confirmation_candidates_skipped": sum(
                item["confirmation_candidates_skipped"] for item in cases
            ),
            "confirmation_skipped_count": sum(
                item["confirmation_skipped_count"] for item in cases
            ),
            "confirmation_budget_exhausted_count": sum(
                item["confirmation_budget_exhausted_count"] for item in cases
            ),
            "confirmation_confirmed_count": sum(
                item["confirmation_confirmed_count"] for item in cases
            ),
            "confirmation_rejected_count": sum(
                item["confirmation_rejected_count"] for item in cases
            ),
            "confirmation_unresolved_count": sum(
                item["confirmation_unresolved_count"] for item in cases
            ),
            "confirmation_query_count": sum(item["confirmation_query_count"] for item in cases),
            "confirmed_meaningful_count": sum(
                item["confirmed_meaningful_count"] for item in cases
            ),
            "confirmed_synonym_count": sum(item["confirmed_synonym_count"] for item in cases),
            "confirmed_per_attempted_candidate": round(
                sum(item["confirmation_confirmed_count"] for item in cases)
                / max(1, sum(item["confirmation_candidates_attempted"] for item in cases)),
                3,
            ),
            "meaningful_per_attempted_candidate": round(
                sum(item["confirmed_meaningful_count"] for item in cases)
                / max(1, sum(item["confirmation_candidates_attempted"] for item in cases)),
                3,
            ),
            "confirmation_precision": round(
                sum(item["confirmed_meaningful_count"] for item in cases)
                / max(1, sum(item["confirmation_confirmed_count"] for item in cases)),
                3,
            ),
            "confirmation_recall": round(
                statistics.mean(item["confirmation_recall"] for item in cases), 3
            ),
            "confirmation_cost_per_confirmed_mechanism": round(
                sum(item["confirmation_query_count"] for item in cases)
                / max(1, sum(item["confirmation_confirmed_count"] for item in cases)),
                3,
            ),
            "queries_per_confirmed_mechanism": round(
                sum(item["confirmation_query_count"] for item in cases)
                / max(1, sum(item["confirmation_confirmed_count"] for item in cases)),
                3,
            ),
            "queries_per_meaningful_confirmation": round(
                sum(item["confirmation_query_count"] for item in cases)
                / max(1, sum(item["confirmed_meaningful_count"] for item in cases)),
                3,
            ),
        },
        "verdict": verdict, "passed": passed, "cases": cases,
    }
    if suite == "development":
        raw_by_id = {str(item.get("prompt_id")): item for item in raw_results}
        result["blind_unknown_review"] = {
            "labels": [
                "meaningful", "noise", "synonym", "too_generic",
                "wrong_domain", "insufficient_evidence",
            ],
            "items": [
                {
                    "prompt_id": case_result["prompt_id"],
                    "request": (raw_by_id.get(str(case_result["prompt_id"])) or {}).get("request") or {},
                    "mechanism": unknown["term"],
                    "evidence": unknown["evidence_sources"],
                    "repos": unknown["repos"],
                    "iteration_source": unknown["iteration"],
                    "label": None,
                }
                for case_result in cases
                for unknown in case_result["unknown_mechanisms"]
            ],
        }
    return result


def summarize_suites(development_payload: dict[str, Any], holdout_payload: dict[str, Any],
                     development_golden: dict[str, dict[str, Any]],
                     holdout_golden: dict[str, dict[str, Any]], *, leakage: bool = False) -> dict[str, Any]:
    development = summarize(development_payload, development_golden, suite="development")
    holdout = summarize(holdout_payload, holdout_golden, suite="holdout")
    if leakage:
        verdict, passed = "leakage_detected", False
    elif "fail" in {development["verdict"], holdout["verdict"]}:
        verdict, passed = "fail", False
    elif "insufficient_data" in {development["verdict"], holdout["verdict"]}:
        verdict, passed = "insufficient_data", None
    elif "needs_review" in {development["verdict"], holdout["verdict"]}:
        verdict, passed = "needs_review", None
    else:
        verdict, passed = "pass", True
    return {
        "schema_version": 4, "verdict": verdict, "passed": passed,
        "development": development, "holdout": holdout,
    }


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Evaluate Muse-shroom boundary expansion")
    parser.add_argument("results", type=Path)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--holdout-results", type=Path)
    parser.add_argument("--holdout-golden", type=Path, default=DEFAULT_HOLDOUT_GOLDEN)
    parser.add_argument("--leakage-detected", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.results.read_text(encoding="utf-8"))
        if args.holdout_results:
            holdout_payload = json.loads(args.holdout_results.read_text(encoding="utf-8"))
            result = summarize_suites(
                payload, holdout_payload, load_golden(args.golden),
                load_golden(args.holdout_golden), leakage=args.leakage_detected,
            )
        else:
            result = summarize(payload, load_golden(args.golden))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 1 if result["passed"] is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
