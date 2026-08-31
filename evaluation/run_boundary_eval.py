from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"


def _confirmation_records(raw_path: Path) -> list[dict[str, object]]:
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    records: list[dict[str, object]] = []
    for result in payload.get("results") or []:
        seen: set[tuple[str, str]] = set()
        trace = (result.get("loop_diagnostics") or {}).get("boundary_trace") or []
        for step in trace:
            for item in step.get("confirmations") or []:
                identity = (
                    str(item.get("candidate") or "").casefold(),
                    str(item.get("confirmation_status") or ""),
                )
                if not identity[0] or identity in seen:
                    continue
                seen.add(identity)
                records.append({
                    "prompt_id": result.get("prompt_id"),
                    "candidate": item.get("candidate"),
                    "confirmation_status": item.get("confirmation_status"),
                    "confirmation_reason": item.get("confirmation_reason"),
                    "confirmation_queries": list(item.get("confirmation_queries") or []),
                    "discovery_repos": sorted({
                        str(source.get("repo"))
                        for source in item.get("discovery_evidence") or []
                        if source.get("repo")
                    }),
                    "confirmation_repos": sorted({
                        str(source.get("repo"))
                        for source in item.get("confirmation_evidence") or []
                        if source.get("repo")
                    }),
                })
    return records


def _write_confirmation_analysis(verdict_path: Path, dev_raw: Path,
                                 holdout_raw: Path | None, output: Path) -> None:
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    suites: dict[str, object] = {}
    for name, raw in (("development", dev_raw), ("holdout", holdout_raw)):
        if raw is None or not raw.exists():
            continue
        suite = verdict.get(name) if "development" in verdict else verdict
        aggregate = (suite or {}).get("aggregate") or {}
        suites[name] = {
            "metrics": {
                key: aggregate.get(key) for key in (
                    "confirmation_planned_count", "confirmation_executed_count",
                    "confirmation_confirmed_count", "confirmation_rejected_count",
                    "confirmation_unresolved_count", "confirmation_query_count",
                    "confirmed_meaningful_count", "confirmed_synonym_count",
                    "confirmation_precision", "confirmation_recall",
                    "confirmation_cost_per_confirmed_mechanism",
                )
            },
            "records": _confirmation_records(raw),
        }
    output.write_text(json.dumps({
        "schema_version": 1,
        "note": (
            "confirmed_wrong_domain_count and blind precision require human labels; "
            "automatic precision is Golden-known precision only"
        ),
        "suites": suites,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run_worker(args: argparse.Namespace, prompts: Path, output: Path, data_dir: Path,
                label: str, *, agentic: bool) -> None:
    command = [
        sys.executable, str(ROOT / "evaluation" / "version_worker.py"),
        "--source-root", str(args.repository.resolve()),
        "--prompts", str(prompts.resolve()),
        "--cassette", str(args.cassette.resolve()),
        "--mode", args.action,
        "--search-interval", str(args.search_interval),
        "--output", str(output), "--data-dir", str(data_dir), "--label", label,
    ]
    if agentic:
        command.extend([
            "--agentic", "--agentic-iterations", str(args.iterations), "--boundary-rank",
        ])
    if args.ci:
        command.append("--synthetic-fixture")
    subprocess.run(command, cwd=ROOT, check=True)


def execute(args: argparse.Namespace) -> None:
    if args.ci:
        if args.action != "replay":
            raise SystemExit("--ci is replay-only; use build_boundary_ci_fixture.py to rebuild it")
        args.prompts = FIXTURES / "boundary-ci-prompts.json"
        args.golden = FIXTURES / "boundary-ci-golden.json"
        args.cassette = FIXTURES / "boundary-ci-v1.json.gz"
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    dev_raw = output_dir / "boundary-development-agentic.raw.json"
    dev_single = output_dir / "boundary-development-single-pass.raw.json"
    holdout_raw = output_dir / "boundary-holdout-agentic.raw.json"
    holdout_single = output_dir / "boundary-holdout-single-pass.raw.json"
    verdict = output_dir / "boundary-verdict.json"
    confirmation_analysis = output_dir / "confirmation-analysis.json"

    with tempfile.TemporaryDirectory(prefix="muse-shroom-boundary-") as data_dir:
        root = Path(data_dir)
        _run_worker(args, args.prompts, dev_single, root / "development-single",
                    "boundary-development-single-pass", agentic=False)
        _run_worker(args, args.prompts, dev_raw, root / "development-agentic",
                    "boundary-development-agentic", agentic=True)
        if not args.ci:
            _run_worker(args, args.holdout_prompts, holdout_single, root / "holdout-single",
                        "boundary-holdout-single-pass", agentic=False)
            _run_worker(args, args.holdout_prompts, holdout_raw, root / "holdout-agentic",
                        "boundary-holdout-agentic", agentic=True)

    leakage = subprocess.run([
        sys.executable, str(ROOT / "evaluation" / "check_boundary_leakage.py"),
        "--holdout", str(args.holdout_golden.resolve()),
    ], cwd=ROOT)
    if leakage.returncode > 1:
        raise SystemExit(leakage.returncode)
    evaluator = [
        sys.executable, str(ROOT / "evaluation" / "boundary_eval.py"),
        str(dev_raw), "--golden", str(args.golden.resolve()), "--output", str(verdict),
    ]
    if not args.ci:
        evaluator.extend([
            "--holdout-results", str(holdout_raw),
            "--holdout-golden", str(args.holdout_golden.resolve()),
        ])
    if leakage.returncode:
        evaluator.append("--leakage-detected")
    completed = subprocess.run(evaluator, cwd=ROOT)
    if verdict.exists():
        _write_confirmation_analysis(
            verdict, dev_raw, None if args.ci else holdout_raw, confirmation_analysis,
        )
    print(json.dumps({
        "ok": completed.returncode == 0,
        "mode": args.action,
        "suite": "ci" if args.ci else "development+holdout",
        "single_pass_results": str(dev_single),
        "raw_results": str(dev_raw),
        **({
            "holdout_single_pass_results": str(holdout_single),
            "holdout_raw_results": str(holdout_raw),
        } if not args.ci else {}),
        "verdict": str(verdict),
        "confirmation_analysis": str(confirmation_analysis),
    }, ensure_ascii=False, indent=2))
    if completed.returncode:
        raise SystemExit(completed.returncode)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Capture or replay deterministic development and holdout Boundary evaluation",
    )
    subparsers = result.add_subparsers(dest="action", required=True)
    for action in ("capture", "replay"):
        command = subparsers.add_parser(action)
        command.add_argument("--repository", type=Path, default=ROOT)
        command.add_argument("--prompts", type=Path, default=ROOT / "evaluation" / "boundary-prompts.json")
        command.add_argument("--golden", type=Path, default=ROOT / "evaluation" / "boundary-golden-cases.json")
        command.add_argument("--holdout-prompts", type=Path, default=ROOT / "evaluation" / "holdout" / "boundary-prompts.json")
        command.add_argument("--holdout-golden", type=Path, default=ROOT / "evaluation" / "holdout" / "boundary-golden-cases.json")
        command.add_argument("--cassette", type=Path, default=ROOT / "evaluation" / "cassettes" / "boundary-v2.json.gz")
        command.add_argument("--output-dir", type=Path, default=ROOT / "evaluation" / "results" / "boundary")
        command.add_argument("--iterations", type=int, default=2)
        command.add_argument("--search-interval", type=float, default=2.1 if action == "capture" else 0.0)
        command.add_argument(
            "--ci", action="store_true",
            help="Replay the committed one-case synthetic fixture without network",
        )
        command.set_defaults(handler=execute)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.handler(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
