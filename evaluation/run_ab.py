from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = "5cc5621"


def _run(command: list[str], *, cwd: Path) -> None:
    completed = subprocess.run(command, cwd=cwd, text=True)
    if completed.returncode:
        raise RuntimeError(f"command failed with exit code {completed.returncode}: {' '.join(command)}")


@contextmanager
def materialize(ref: str, repository: Path) -> Iterator[Path]:
    if ref == "worktree":
        yield repository
        return
    with tempfile.TemporaryDirectory(prefix="muse-shroom-eval-") as temporary:
        temporary_path = Path(temporary)
        archive = temporary_path / "source.zip"
        subprocess.run(
            ["git", "-c", f"safe.directory={repository.as_posix()}", "archive", "--format=zip", "-o", str(archive), ref],
            cwd=repository, check=True,
        )
        source = temporary_path / "source"
        source.mkdir()
        with zipfile.ZipFile(archive) as handle:
            handle.extractall(source)
        yield source


def _worker(source: Path, *, label: str, mode: str, prompts: Path,
            cassette: Path, output: Path, data_dir: Path,
            search_interval: float) -> None:
    command = [
        sys.executable, str(ROOT / "evaluation" / "version_worker.py"),
        "--source-root", str(source), "--prompts", str(prompts),
        "--output", str(output), "--cassette", str(cassette),
        "--data-dir", str(data_dir), "--label", label, "--mode", mode,
        "--search-interval", str(search_interval),
    ]
    _run(command, cwd=ROOT)


def build_blind_pack(baseline_path: Path, candidate_path: Path, *,
                     blind_path: Path, key_path: Path, seed: str) -> None:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    baseline_results = {item["prompt_id"]: item for item in baseline["results"]}
    candidate_results = {item["prompt_id"]: item for item in candidate["results"]}
    if baseline_results.keys() != candidate_results.keys():
        raise ValueError("baseline and candidate prompt IDs differ")
    rng = random.Random(seed)
    cases = []
    mappings = {}
    for prompt_id in baseline_results:
        order = ["baseline", "candidate"]
        rng.shuffle(order)
        sources = {"baseline": baseline_results[prompt_id], "candidate": candidate_results[prompt_id]}
        mappings[prompt_id] = {"A": order[0], "B": order[1]}
        case = {
            "prompt_id": prompt_id,
            "category": sources["baseline"]["category"],
            "request": sources["baseline"]["request"],
            "stage": "assessment_shortlist",
            "lists": {
                "A": sources[order[0]]["candidates"],
                "B": sources[order[1]]["candidates"],
            },
        }
        cases.append(case)
    blind_path.parent.mkdir(parents=True, exist_ok=True)
    blind_path.write_text(json.dumps({
        "schema_version": 1, "stage": "assessment_shortlist", "cases": cases,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(json.dumps({
        "schema_version": 1,
        "baseline_version": baseline.get("muse_shroom_version"),
        "candidate_version": candidate.get("muse_shroom_version"),
        "mappings": mappings,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def execute(args: argparse.Namespace) -> None:
    repository = args.repository.resolve()
    prompts = args.prompts.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cassette = args.cassette.resolve()
    baseline_output = output_dir / "baseline.raw.json"
    candidate_output = output_dir / "candidate.raw.json"
    with tempfile.TemporaryDirectory(prefix="muse-shroom-ab-data-") as temporary_data:
        data_root = Path(temporary_data)
        with materialize(args.baseline_ref, repository) as baseline_source:
            _worker(
                baseline_source, label="baseline", mode=args.action, prompts=prompts,
                cassette=cassette, output=baseline_output,
                data_dir=data_root / "baseline", search_interval=args.search_interval,
            )
        with materialize(args.candidate_ref, repository) as candidate_source:
            _worker(
                candidate_source, label="candidate", mode=args.action, prompts=prompts,
                cassette=cassette, output=candidate_output,
                data_dir=data_root / "candidate", search_interval=args.search_interval,
            )
    build_blind_pack(
        baseline_output, candidate_output,
        blind_path=output_dir / "blind-review.json",
        key_path=output_dir / "blind-key.json", seed=args.seed,
    )
    print(json.dumps({
        "ok": True, "mode": args.action, "cassette": str(cassette),
        "blind_review": str(output_dir / "blind-review.json"),
        "blind_key": str(output_dir / "blind-key.json"),
    }, ensure_ascii=False, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture or replay a blind Muse-shroom A/B evaluation")
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("capture", "replay"):
        command = subparsers.add_parser(action)
        command.add_argument("--repository", type=Path, default=ROOT)
        command.add_argument("--prompts", type=Path, default=ROOT / "evaluation" / "ab-prompts.json")
        command.add_argument("--cassette", type=Path, default=ROOT / "evaluation" / "cassettes" / "ab-v1.json.gz")
        command.add_argument("--output-dir", type=Path, default=ROOT / "evaluation" / "results")
        command.add_argument("--baseline-ref", default=DEFAULT_BASELINE)
        command.add_argument("--candidate-ref", default="worktree")
        command.add_argument("--seed", default="muse-shroom-ab-v1")
        command.add_argument("--search-interval", type=float, default=2.1 if action == "capture" else 0.0)
        command.set_defaults(handler=execute)
    blind = subparsers.add_parser("blind", help="rebuild the anonymous review pack from raw results")
    blind.add_argument("--baseline", type=Path, required=True)
    blind.add_argument("--candidate", type=Path, required=True)
    blind.add_argument("--output", type=Path, required=True)
    blind.add_argument("--key", type=Path, required=True)
    blind.add_argument("--seed", default="muse-shroom-ab-v1")
    blind.set_defaults(handler=lambda args: build_blind_pack(
        args.baseline, args.candidate, blind_path=args.output, key_path=args.key, seed=args.seed
    ))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.handler(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
