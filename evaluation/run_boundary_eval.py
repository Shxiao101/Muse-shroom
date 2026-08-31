from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"


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
