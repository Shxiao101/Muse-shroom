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
TOKEN_RE = re.compile(r"[A-Za-z0-9_+#]+|[\u3400-\u9fff]+")
FORMAL_METRICS = (
    "mechanism_redundancy",
    "new_mechanisms_per_iteration",
    "boundary_gain_per_iteration",
    "meaningful_boundary_gain",
    "mainstream_coverage",
    "new_mechanism_match_count",
    "cross_mechanism_discovery",
    "repetition_penalty",
    "direction_coverage",
    "presented_mechanism_count",
    "duplicate_query_rate",
    "unexplored_directions_at_stop",
)


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
        for item in trace
        if item.get("stage") in {"search", "iterate", "expand"}
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


def _golden_quality(result: dict[str, Any], case: dict[str, Any] | None) -> dict[str, Any]:
    if case is None:
        return {
            "golden_case_found": False,
            "mainstream_coverage": None,
            "new_mechanism_matches": [],
            "new_mechanism_match_count": 0,
            "cross_mechanism_matches": [],
            "cross_mechanism_discovery": False,
            "repetition_violations": [],
            "repetition_penalty": 0.0,
            "meaningful_boundary_gain": 0,
            "boundary_quality_passed": False,
        }
    trace = list((result.get("loop_diagnostics") or {}).get("boundary_trace") or [])
    initial = next((item for item in trace if item.get("stage") == "search"), trace[0] if trace else {})
    initial_mechanisms = list(initial.get("mechanisms_found") or [])
    later_mechanisms = [
        value for item in trace if item is not initial
        for value in item.get("new_mechanisms") or []
    ]
    cross_signal_values: list[Any] = list(later_mechanisms)
    for item in trace[1:]:
        cross_signal_values.extend(item.get("directions_uncovered") or [])
        for evidence in item.get("evidence_sources") or []:
            cross_signal_values.append(evidence.get("term"))

    mainstream = case["mainstream_mechanisms"]
    mainstream_matches = set().union(*(_matches(value, mainstream) for value in initial_mechanisms), set())
    mainstream_coverage = len(mainstream_matches) / max(1, len(mainstream))
    acceptable = case["acceptable_new_mechanisms"]
    new_matches = set().union(*(_matches(value, acceptable) for value in later_mechanisms), set())
    cross = case["cross_mechanism_directions"]
    cross_matches = set().union(*(_matches(value, cross) for value in cross_signal_values), set())

    candidate_mechanisms = [
        mechanism.get("name")
        for candidate in result.get("recalled_candidates") or result.get("candidates") or []
        for mechanism in candidate.get("mechanisms") or []
    ]
    repetition_violations = []
    repeated = 0
    for group in case["repetition_groups"]:
        count = sum(bool(_matches(value, [group])) for value in candidate_mechanisms)
        maximum = int(group.get("max_results") or 0)
        if count > maximum:
            repetition_violations.append({
                "id": group["id"], "count": count, "max_results": maximum,
            })
            repeated += count - maximum
    thresholds = case.get("thresholds") or {}
    meaningful_gain = len(new_matches)
    quality_passed = (
        mainstream_coverage >= float(thresholds.get("min_mainstream_coverage", 0.34))
        and meaningful_gain >= int(thresholds.get("min_meaningful_new_mechanisms", 1))
        and (not thresholds.get("require_cross_mechanism", True) or bool(cross_matches))
        and not repetition_violations
    )
    return {
        "golden_case_found": True,
        "mainstream_coverage": round(mainstream_coverage, 3),
        "mainstream_matches": sorted(mainstream_matches),
        "new_mechanism_matches": sorted(new_matches),
        "new_mechanism_match_count": len(new_matches),
        "cross_mechanism_matches": sorted(cross_matches),
        "cross_mechanism_discovery": bool(cross_matches),
        "repetition_violations": repetition_violations,
        "repetition_penalty": round(repeated / max(1, len(candidate_mechanisms)), 3),
        "meaningful_boundary_gain": meaningful_gain,
        "boundary_quality_passed": quality_passed,
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
            _normalized(item.get("term"))
            for item in step.get("evidence_sources") or []
            if _normalized(item.get("term"))
        }
        missing_evidence.extend(term for key, term in expected.items() if key not in evidenced)
    gains = list(loop.get("boundary_gain_per_iteration") or [])
    return {
        "prompt_id": result.get("prompt_id"),
        "mechanism_redundancy": float(diagnostics.get("mechanism_redundancy") or 0),
        "new_mechanisms_per_iteration": list(loop.get("new_mechanisms_per_iteration") or []),
        "boundary_gain_per_iteration": gains,
        "direction_coverage": float(diagnostics.get("direction_coverage") or 0),
        "presented_mechanism_count": int(diagnostics.get("presented_mechanism_count") or 0),
        "duplicate_query_rate": float(loop.get("duplicate_query_rate") or 0),
        "unexplored_directions_at_stop": list(loop.get("unexplored_directions_at_stop") or []),
        "iterations_used": int(loop.get("iterations_used") or 0),
        "queries_changed_after_initial": _queries_changed(trace),
        "evidence_backed_promotions": not missing_evidence,
        "missing_promotion_evidence": missing_evidence,
        "boundary_expanded": sum(int(value) for value in gains) > 0,
        **_golden_quality(result, case),
    }


def summarize(payload: dict[str, Any], golden_cases: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    raw_results = payload.get("results")
    if not isinstance(raw_results, list) or not raw_results:
        raise ValueError("results must be a non-empty array")
    golden_cases = golden_cases if golden_cases is not None else load_golden()
    cases = [case_metrics(item, golden_cases.get(str(item.get("prompt_id")))) for item in raw_results]
    agentic = [item for item in cases if item["iterations_used"] > 0]
    scored = [item for item in agentic if item["golden_case_found"]]
    if agentic and len(scored) == len(agentic):
        expanded_share = sum(item["boundary_expanded"] for item in agentic) / len(agentic)
        meaningful_share = sum(item["meaningful_boundary_gain"] > 0 for item in agentic) / len(agentic)
        passed: bool | None = (
            all(item["evidence_backed_promotions"] for item in agentic)
            and all(item["duplicate_query_rate"] <= 0.5 for item in agentic)
            and all(item["queries_changed_after_initial"] is not False for item in agentic)
            and all(item["boundary_quality_passed"] for item in agentic)
            and expanded_share >= 0.5
            and meaningful_share >= 0.5
        )
        verdict = "pass" if passed else "fail"
    elif agentic:
        expanded_share = 0.0
        meaningful_share = 0.0
        passed = None
        verdict = "unmapped_golden_cases"
    else:
        expanded_share = 0.0
        meaningful_share = 0.0
        passed = None
        verdict = "insufficient_agentic_cases"
    return {
        "schema_version": 2,
        "formal_metrics": list(FORMAL_METRICS),
        "case_count": len(cases),
        "agentic_case_count": len(agentic),
        "golden_case_count": len(scored),
        "aggregate": {
            "median_mechanism_redundancy": statistics.median(item["mechanism_redundancy"] for item in cases),
            "median_direction_coverage": statistics.median(item["direction_coverage"] for item in cases),
            "median_presented_mechanism_count": statistics.median(item["presented_mechanism_count"] for item in cases),
            "agentic_boundary_expansion_share": round(expanded_share, 3),
            "meaningful_boundary_expansion_share": round(meaningful_share, 3),
        },
        "verdict": verdict,
        "passed": passed,
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Muse-shroom boundary expansion")
    parser.add_argument("results", type=Path)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.results.read_text(encoding="utf-8"))
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
