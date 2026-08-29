from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

from cassette import CassetteGitHub


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one Muse-shroom version against a GitHub cassette")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--prompts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cassette", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--mode", choices=("capture", "replay"), required=True)
    parser.add_argument("--search-interval", type=float, default=0.0)
    parser.add_argument("--candidate-limit", type=int, default=24)
    return parser


def _compact(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "repo": candidate.get("full_name"),
        "url": candidate.get("html_url"),
        "description": candidate.get("description"),
        "stars": int(candidate.get("stargazers_count", 0)),
        "topics": candidate.get("topics", []),
        "language": candidate.get("language"),
        "archived": bool(candidate.get("archived", False)),
        "pushed_at": candidate.get("pushed_at"),
        "selection_lanes": candidate.get("selection_lanes", []),
        "selection_score_components": candidate.get("selection_score_components", {}),
        "discovery_paths": candidate.get("discovery_paths", []),
        "evidence": candidate.get("evidence", []),
        "mechanisms": candidate.get("mechanisms", []),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    sys.path.insert(0, str(args.source_root.resolve() / "src"))
    github_module = importlib.import_module("muse_shroom.github")
    models_module = importlib.import_module("muse_shroom.models")
    search_module = importlib.import_module("muse_shroom.search")
    storage_module = importlib.import_module("muse_shroom.storage")
    version_module = importlib.import_module("muse_shroom")

    prompts_payload = json.loads(args.prompts.read_text(encoding="utf-8"))
    prompts = prompts_payload.get("prompts", [])
    if not isinstance(prompts, list) or not prompts:
        raise ValueError("prompt file must contain a non-empty prompts array")

    args.data_dir.mkdir(parents=True, exist_ok=True)
    store = storage_module.Store(args.data_dir)
    delegate = github_module.GitHubClient(store) if args.mode == "capture" else None
    github = CassetteGitHub(
        github_module, args.cassette, delegate=delegate,
        search_interval=args.search_interval,
    )
    engine = search_module.SearchEngine(store, github)
    results = []
    try:
        for index, prompt in enumerate(prompts, 1):
            request_payload = prompt["request"]
            fields = getattr(models_module.SearchRequest, "__dataclass_fields__", {})
            if "problem_concepts" in request_payload and "problem_concepts" not in fields:
                # Historical baselines only understand the v0.3 contract.
                legacy_payload = {
                    **request_payload,
                    "core_concepts": list(request_payload.get("problem_concepts") or [])
                    + list(request_payload.get("mechanisms") or []),
                    "adjacent_concepts": list(request_payload.get("exploration_directions") or []),
                }
                for field in ("problem_concepts", "mechanisms", "exploration_directions"):
                    legacy_payload.pop(field, None)
                request = models_module.SearchRequest.from_dict(legacy_payload)
            else:
                request = models_module.SearchRequest.from_dict(request_payload)
            output = engine.search(request, "quick")
            candidates = list(output.get("candidates", []))[:args.candidate_limit]
            session = store.load_search(output["search_id"])
            boundary = dict(output.get("boundary") or {})
            assignments = sum(len(item.get("mechanisms") or []) for item in candidates)
            presented_count = len(boundary.get("presented_mechanisms") or [])
            try:
                iteration_module = importlib.import_module("muse_shroom.iteration")
                loop_diagnostics = iteration_module.session_loop_diagnostics(
                    store, output["search_id"]
                )
            except (ImportError, AttributeError, KeyError):
                loop_diagnostics = {
                    "iterations_used": 0, "mode": "single-pass",
                    "queries_per_iteration": [], "new_mechanisms_per_iteration": [],
                    "boundary_gain_per_iteration": [], "duplicate_query_rate": 0.0,
                    "candidate_novelty_per_iteration": [], "stop_reason": None,
                    "unexplored_directions_at_stop": list(boundary.get("unexplored_directions") or []),
                }
            results.append({
                "prompt_id": prompt["id"], "category": prompt["category"],
                "request": prompt["request"],
                "schema_version": output.get("schema_version"),
                "candidate_count": output.get("candidate_count", len(candidates)),
                "assessment_candidate_count": output.get("assessment_candidate_count", len(candidates)),
                "coverage": output.get("coverage", {}),
                "boundary": boundary,
                "boundary_delta": output.get("boundary_delta", {}),
                "boundary_diagnostics": {
                    "mechanism_count": len(boundary.get("recalled_mechanisms") or []),
                    "presented_mechanism_count": presented_count,
                    "mechanism_redundancy": round(
                        max(0, assignments - presented_count) / max(1, assignments), 3
                    ),
                    "boundary_gain": len(
                        (output.get("boundary_delta") or {}).get("new_mechanisms") or []
                    ),
                    "direction_coverage": (output.get("coverage") or {}).get(
                        "direction_coverage", 0.0
                    ),
                },
                "loop_diagnostics": loop_diagnostics,
                "stale": bool(output.get("stale", False)),
                "incomplete_phase": output.get("incomplete_phase"),
                "candidates": [_compact(candidate) for candidate in candidates],
                "recalled_candidates": [
                    _compact(candidate) for candidate in session.get("candidates", [])
                ],
            })
            if args.mode == "capture":
                github.save()
            print(f"[{args.label}] {index}/{len(prompts)} {prompt['id']}: {len(candidates)} candidates", flush=True)
    finally:
        store.close()
    payload = {
        "schema_version": 1, "label": args.label,
        "muse_shroom_version": getattr(version_module, "__version__", "unknown"),
        "stage": "assessment_shortlist", "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
