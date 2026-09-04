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
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = "5cc5621"
REVIEW_CHUNK_SIZE = 6


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


def _review_candidate(candidate: dict) -> dict:
    evidence = []
    for item in candidate.get("evidence") or []:
        kind = item.get("kind")
        if kind == "mechanism_match":
            facts = item.get("facts") or {}
            evidence.append({
                "id": item.get("id"), "kind": "mechanism_match",
                "facts": {
                    "mechanisms": list(facts.get("mechanisms") or [])[:3],
                    "untrusted_source": bool(facts.get("untrusted_source", False)),
                },
            })
            continue
        if kind != "readme_excerpt":
            continue
        facts = item.get("facts") or {}
        evidence.append({
            "id": item.get("id"),
            "kind": "readme_excerpt",
            "facts": {
                key: facts[key] for key in (
                    "snippet_type", "line_start", "line_end", "sha",
                    "parent_evidence_id", "text", "untrusted_source",
                ) if facts.get(key) is not None
            },
        })
        if len(evidence) >= 3:
            break
    return {
        "repo": candidate.get("repo"),
        "url": candidate.get("url"),
        "description": candidate.get("description"),
        "stars": int(candidate.get("stars", 0)),
        "topics": list(candidate.get("topics") or [])[:6],
        "language": candidate.get("language"),
        "archived": bool(candidate.get("archived", False)),
        "pushed_at": candidate.get("pushed_at"),
        "mechanisms": [
            {
                key: mechanism.get(key)
                for key in ("name", "role", "evidence_ids")
                if mechanism.get(key) is not None
            }
            for mechanism in list(candidate.get("mechanisms") or [])[:3]
        ],
        "evidence": evidence,
    }


def _write_case_chunks(case_dir: Path, cases: list[dict]) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "stage": "assessment_shortlist", "cases": []}
    for case in cases:
        entry = {
            "prompt_id": case["prompt_id"], "category": case["category"],
            "request": case["request"], "files": {"A": [], "B": []},
        }
        for label in ("A", "B"):
            candidates = case["lists"][label]
            for start in range(0, len(candidates), REVIEW_CHUNK_SIZE):
                part = start // REVIEW_CHUNK_SIZE + 1
                filename = f"{case['prompt_id']}-{label}-{part}.json"
                payload = {
                    "schema_version": 1, "stage": "assessment_shortlist",
                    "prompt_id": case["prompt_id"], "category": case["category"],
                    "request": case["request"], "list": label, "part": part,
                    "candidates": candidates[start:start + REVIEW_CHUNK_SIZE],
                }
                (case_dir / filename).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
                )
                entry["files"][label].append(filename)
        manifest["cases"].append(entry)
    (case_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def build_blind_pack(baseline_path: Path, candidate_path: Path, *,
                     blind_path: Path, key_path: Path, seed: str,
                     shortlist_limit: int | None = None,
                     case_dir: Path | None = None) -> None:
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
        lists = {
            "A": list(sources[order[0]].get("candidates") or []),
            "B": list(sources[order[1]].get("candidates") or []),
        }
        if shortlist_limit is not None:
            lists = {label: items[:shortlist_limit] for label, items in lists.items()}
            lists = {
                label: [_review_candidate(item) for item in items]
                for label, items in lists.items()
            }
        case = {
            "prompt_id": prompt_id,
            "category": sources["baseline"]["category"],
            "request": sources["baseline"]["request"],
            "stage": "assessment_shortlist",
            "comparison": "standard" if shortlist_limit is not None else "natural",
            "lists": lists,
        }
        cases.append(case)
    blind_path.parent.mkdir(parents=True, exist_ok=True)
    blind_path.write_text(json.dumps({
        "schema_version": 1, "stage": "assessment_shortlist", "cases": cases,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if case_dir is not None:
        _write_case_chunks(case_dir, cases)
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
    key_path = output_dir / "blind-key.json"
    build_blind_pack(
        baseline_output, candidate_output,
        blind_path=output_dir / "blind-review.json",
        key_path=key_path, seed=args.seed,
    )
    build_blind_pack(
        baseline_output, candidate_output,
        blind_path=output_dir / "blind-review-standard.json",
        key_path=key_path, seed=args.seed, shortlist_limit=12,
        case_dir=output_dir / "blind-cases",
    )
    print(json.dumps({
        "ok": True, "mode": args.action, "cassette": str(cassette),
        "blind_review": str(output_dir / "blind-review.json"),
        "blind_review_standard": str(output_dir / "blind-review-standard.json"),
        "blind_case_manifest": str(output_dir / "blind-cases" / "manifest.json"),
        "blind_key": str(key_path),
    }, ensure_ascii=False, indent=2))


def adapt_direct_arm(
    requests_payload: dict[str, Any], direct_payload: dict[str, Any],
) -> dict[str, Any]:
    """Validate direct-host output and emit the same result envelope as Muse-shroom."""
    requests = {
        str(item.get("prompt_id") or ""): item
        for item in requests_payload.get("requests") or []
    }
    direct = {
        str(item.get("prompt_id") or ""): item
        for item in direct_payload.get("results") or []
    }
    if not requests or set(requests) != set(direct):
        raise ValueError("direct arm prompt IDs must exactly match ab-requests.json")
    metadata = direct_payload.get("metadata")
    required_metadata = {
        "model_id", "muse_shroom_revision", "skill_component_digest",
        "timestamp", "configuration",
    }
    if not isinstance(metadata, dict) or not required_metadata <= set(metadata):
        raise ValueError("direct arm metadata is incomplete")
    results: list[dict[str, Any]] = []
    for prompt_id, request in requests.items():
        item = direct[prompt_id]
        candidates = item.get("candidates")
        if not isinstance(candidates, list):
            raise ValueError(f"direct arm candidates must be an array for {prompt_id}")
        results.append({
            "prompt_id": prompt_id,
            "category": request.get("category"),
            "request": request.get("request"),
            "candidates": candidates,
        })
    return {
        "schema_version": 2,
        "arm": "direct",
        "metadata": metadata,
        "results": results,
    }


def check_claim_traceability(
    arm_payload: dict[str, Any], repository_facts: dict[str, Any],
) -> dict[str, Any]:
    """Check existence, archive state, and exact quoted text without judging claims."""
    rows: list[dict[str, Any]] = []
    for result in arm_payload.get("results") or []:
        for candidate in result.get("candidates") or []:
            repo_name = str(candidate.get("repo") or candidate.get("full_name") or "")
            facts = repository_facts.get(repo_name.casefold()) or {}
            failures: list[str] = []
            if not facts.get("exists"):
                failures.append("repository_not_found")
            elif facts.get("archived"):
                failures.append("repository_archived")
            quote = str(candidate.get("quote") or "")
            source_term = str(candidate.get("source_term") or "")
            if quote:
                sources = [
                    source for source in facts.get("sources") or []
                    if isinstance(source, dict) and source.get("sha")
                ]
                if not any(
                    quote in str(source.get("text") or "")
                    and (not source_term or source_term in str(source.get("text") or ""))
                    for source in sources
                ):
                    failures.append("quote_not_verbatim_at_recorded_sha")
            rows.append({
                "prompt_id": result.get("prompt_id"),
                "repo": repo_name,
                "passed": not failures,
                "failures": failures,
            })
    return {
        "arm": arm_payload.get("arm"),
        "checked": len(rows),
        "passed": sum(item["passed"] for item in rows),
        "failed": sum(not item["passed"] for item in rows),
        "repositories": rows,
        "measurement": "claim_traceability_only",
    }


def _adapt_direct_command(args: argparse.Namespace) -> None:
    payload = adapt_direct_arm(
        json.loads(args.requests.read_text(encoding="utf-8")),
        json.loads(args.input.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _check_claims_command(args: argparse.Namespace) -> None:
    payload = check_claim_traceability(
        json.loads(args.arm.read_text(encoding="utf-8")),
        json.loads(args.repository_facts.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
    direct = subparsers.add_parser("adapt-direct", help="validate direct-host structured output")
    direct.add_argument("--requests", type=Path, required=True)
    direct.add_argument("--input", type=Path, required=True)
    direct.add_argument("--output", type=Path, required=True)
    direct.set_defaults(handler=_adapt_direct_command)
    claims = subparsers.add_parser("check-claims", help="check repository and quote facts")
    claims.add_argument("--arm", type=Path, required=True)
    claims.add_argument("--repository-facts", type=Path, required=True)
    claims.add_argument("--output", type=Path, required=True)
    claims.set_defaults(handler=_check_claims_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.handler(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
