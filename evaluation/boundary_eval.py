from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any


FORMAL_METRICS = (
    "mechanism_redundancy",
    "new_mechanisms_per_iteration",
    "boundary_gain_per_iteration",
    "direction_coverage",
    "presented_mechanism_count",
    "duplicate_query_rate",
    "unexplored_directions_at_stop",
)


def _queries_changed(trace: list[dict[str, Any]]) -> bool | None:
    rounds = [
        {str(query).casefold() for query in item.get("queries") or [] if str(query).strip()}
        for item in trace
        if item.get("stage") in {"search", "iterate", "expand"}
    ]
    if len(rounds) < 2:
        return None
    return any(rounds[index] != rounds[0] for index in range(1, len(rounds)))


def case_metrics(result: dict[str, Any]) -> dict[str, Any]:
    diagnostics = result.get("boundary_diagnostics") or {}
    loop = result.get("loop_diagnostics") or {}
    trace = list(loop.get("boundary_trace") or [])
    missing_evidence = []
    for step in trace:
        hypothesis = step.get("hypothesis") or {}
        expected = {
            str(term).casefold(): str(term)
            for term in hypothesis.get("promote_discovered_terms") or []
        }
        expected.update({
            str(item.get("term") or "").casefold(): str(item.get("term") or "")
            for item in hypothesis.get("add_exploration_directions") or []
            if isinstance(item, dict) and str(item.get("term") or "").strip()
        })
        evidenced = {
            str(item.get("term") or "").casefold()
            for item in step.get("evidence_sources") or []
            if str(item.get("term") or "").strip()
        }
        missing_evidence.extend(
            term for key, term in expected.items() if key not in evidenced
        )
    gains = list(loop.get("boundary_gain_per_iteration") or [])
    return {
        "prompt_id": result.get("prompt_id"),
        "mechanism_redundancy": float(diagnostics.get("mechanism_redundancy") or 0),
        "new_mechanisms_per_iteration": list(
            loop.get("new_mechanisms_per_iteration") or []
        ),
        "boundary_gain_per_iteration": gains,
        "direction_coverage": float(diagnostics.get("direction_coverage") or 0),
        "presented_mechanism_count": int(
            diagnostics.get("presented_mechanism_count") or 0
        ),
        "duplicate_query_rate": float(loop.get("duplicate_query_rate") or 0),
        "unexplored_directions_at_stop": list(
            loop.get("unexplored_directions_at_stop") or []
        ),
        "iterations_used": int(loop.get("iterations_used") or 0),
        "queries_changed_after_initial": _queries_changed(trace),
        "evidence_backed_promotions": not missing_evidence,
        "missing_promotion_evidence": missing_evidence,
        "boundary_expanded": sum(int(value) for value in gains) > 0,
    }


def summarize(payload: dict[str, Any]) -> dict[str, Any]:
    raw_results = payload.get("results")
    if not isinstance(raw_results, list) or not raw_results:
        raise ValueError("results must be a non-empty array")
    cases = [case_metrics(item) for item in raw_results]
    agentic = [item for item in cases if item["iterations_used"] > 0]
    if agentic:
        expanded_share = sum(item["boundary_expanded"] for item in agentic) / len(agentic)
        passed: bool | None = (
            all(item["evidence_backed_promotions"] for item in agentic)
            and all(item["duplicate_query_rate"] <= 0.5 for item in agentic)
            and all(item["queries_changed_after_initial"] is not False for item in agentic)
            and expanded_share >= 0.5
        )
        verdict = "pass" if passed else "fail"
    else:
        expanded_share = 0.0
        passed = None
        verdict = "insufficient_agentic_cases"
    return {
        "schema_version": 1,
        "formal_metrics": list(FORMAL_METRICS),
        "case_count": len(cases),
        "agentic_case_count": len(agentic),
        "aggregate": {
            "median_mechanism_redundancy": statistics.median(
                item["mechanism_redundancy"] for item in cases
            ),
            "median_direction_coverage": statistics.median(
                item["direction_coverage"] for item in cases
            ),
            "median_presented_mechanism_count": statistics.median(
                item["presented_mechanism_count"] for item in cases
            ),
            "agentic_boundary_expansion_share": round(expanded_share, 3),
        },
        "verdict": verdict,
        "passed": passed,
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Muse-shroom boundary expansion")
    parser.add_argument("results", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.results.read_text(encoding="utf-8"))
        result = summarize(payload)
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
