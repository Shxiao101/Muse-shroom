from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def execute(args: argparse.Namespace) -> None:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    raw = output_dir / "boundary-agentic.raw.json"
    single = output_dir / "boundary-single-pass.raw.json"
    verdict = output_dir / "boundary-verdict.json"
    with tempfile.TemporaryDirectory(prefix="muse-shroom-boundary-") as data_dir:
        common = [
            sys.executable, str(ROOT / "evaluation" / "version_worker.py"),
            "--source-root", str(args.repository.resolve()),
            "--prompts", str(args.prompts.resolve()),
            "--cassette", str(args.cassette.resolve()),
            "--mode", args.action,
            "--search-interval", str(args.search_interval),
        ]
        subprocess.run([
            *common, "--output", str(single),
            "--data-dir", str(Path(data_dir) / "single"),
            "--label", "boundary-single-pass",
        ], cwd=ROOT, check=True)
        worker = [
            *common, "--output", str(raw),
            "--data-dir", str(Path(data_dir) / "agentic"),
            "--label", "boundary-agentic",
            "--agentic",
            "--agentic-iterations", str(args.iterations),
            "--boundary-rank",
        ]
        subprocess.run(worker, cwd=ROOT, check=True)
    evaluator = [
        sys.executable, str(ROOT / "evaluation" / "boundary_eval.py"),
        str(raw), "--golden", str(args.golden.resolve()), "--output", str(verdict),
    ]
    completed = subprocess.run(evaluator, cwd=ROOT)
    print(json.dumps({
        "ok": completed.returncode == 0,
        "mode": args.action,
        "single_pass_results": str(single),
        "raw_results": str(raw),
        "verdict": str(verdict),
    }, ensure_ascii=False, indent=2))
    if completed.returncode:
        raise SystemExit(completed.returncode)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Capture or replay the complete deterministic Boundary evaluation",
    )
    subparsers = result.add_subparsers(dest="action", required=True)
    for action in ("capture", "replay"):
        command = subparsers.add_parser(action)
        command.add_argument("--repository", type=Path, default=ROOT)
        command.add_argument("--prompts", type=Path, default=ROOT / "evaluation" / "boundary-prompts.json")
        command.add_argument("--golden", type=Path, default=ROOT / "evaluation" / "boundary-golden-cases.json")
        command.add_argument("--cassette", type=Path, default=ROOT / "evaluation" / "cassettes" / "boundary-v1.json.gz")
        command.add_argument("--output-dir", type=Path, default=ROOT / "evaluation" / "results" / "boundary")
        command.add_argument("--iterations", type=int, default=2)
        command.add_argument("--search-interval", type=float, default=2.1 if action == "capture" else 0.0)
        command.set_defaults(handler=execute)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.handler(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
