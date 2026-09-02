from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any

try:
    from cassette import CassetteGitHub
    from synthetic_fixture import SyntheticFixtureGitHub
except ModuleNotFoundError:  # Imported as evaluation.version_worker in tests.
    from evaluation.cassette import CassetteGitHub
    from evaluation.synthetic_fixture import SyntheticFixtureGitHub


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
    parser.add_argument("--agentic", action="store_true")
    parser.add_argument("--agentic-iterations", type=int, default=2)
    parser.add_argument("--boundary-rank", action="store_true")
    parser.add_argument("--synthetic-fixture", action="store_true")
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
        "selected_for_assessment": bool(candidate.get("selected_for_assessment", False)),
    }


def _mechanism_redundancy(candidates: list[dict[str, Any]]) -> float:
    mechanisms = [
        str(mechanism.get("name") or "").strip().casefold()
        for candidate in candidates
        for mechanism in candidate.get("mechanisms") or []
        if str(mechanism.get("name") or "").strip()
    ]
    return round((len(mechanisms) - len(set(mechanisms))) / max(1, len(mechanisms)), 3)


def deterministic_hypothesis(observation: dict[str, Any], used: set[str]) -> dict[str, Any] | None:
    """Choose only an observed direction; golden answers never enter this policy."""
    evidence = [
        item for _, item in sorted(
            enumerate(observation.get("discovered_term_evidence") or []),
            key=lambda pair: (
                0 if pair[1].get("promotion_confidence") == "high"
                else 1 if pair[1].get("promotion_confidence") == "medium" else 2,
                0 if pair[1].get("kind") == "candidate_mechanism"
                else 1 if pair[1].get("kind") == "cross_domain_direction" else 2,
                pair[0],
            ),
        )
    ]
    def promote(item: dict[str, Any]) -> dict[str, Any] | None:
        term = str(item.get("term") or "").strip()
        key = term.casefold()
        if not term or key in used:
            return None
        used.add(key)
        return {
            "decision": "continue",
            "target_direction": term,
            "promote_discovered_terms": [term],
            "strategies": ["keyword"],
            "reason": "deterministic evaluation: promote observed evidence-backed term",
        }

    for item in evidence:
        if item.get("kind") not in {"candidate_mechanism", "cross_domain_direction"}:
            continue
        if item.get("promotable") is False:
            continue
        hypothesis = promote(item)
        if hypothesis is not None:
            return hypothesis
    for value in observation.get("unexplored_directions") or []:
        term = str(value).strip()
        key = term.casefold()
        if not term or key in used:
            continue
        used.add(key)
        anchor = next(
            (
                str(item.get("term") or "").strip()
                for item in observation.get("anchors") or []
                if str(item.get("term") or "").strip().casefold() != key
            ),
            "",
        )
        seed = next(
            (
                str(item.get("repo") or "").strip()
                for item in observation.get("anchors") or []
                if "/" in str(item.get("repo") or "")
            ),
            "",
        )
        return {
            "decision": "continue",
            "target_direction": term,
            **({"concepts": [f"{term} {anchor}"]} if anchor else {}),
            **({"seeds": [seed]} if seed else {}),
            "strategies": ["keyword", *(["relationship"] if seed else [])],
            "reason": "deterministic evaluation: cover observed unexplored direction",
        }
    return None


def deterministic_assessment(candidate: dict[str, Any], request_payload: dict[str, Any]) -> dict[str, Any]:
    """Build a traceable ranking fixture; this is not a semantic quality judge."""
    evidence = list(candidate.get("evidence") or [])
    excerpt = next((item for item in evidence if item.get("kind") == "readme_excerpt"), None)
    metadata = next((item for item in evidence if item.get("kind") == "github_metadata"), None)
    citation = excerpt or metadata or (evidence[0] if evidence else None)
    if citation is None or not citation.get("id"):
        raise ValueError(f"evaluation candidate lacks evidence: {candidate.get('full_name')}")
    excerpt_text = str(((excerpt or {}).get("facts") or {}).get("text") or "").strip()
    mechanisms = list(candidate.get("mechanisms") or [])
    topics = list(candidate.get("topics") or [])
    artifact_types = list(request_payload.get("artifact_types") or [])
    return {
        "repo": candidate["full_name"],
        "relevance": 75,
        "uniqueness": 72,
        "usability": 70 if excerpt else 45,
        "difficulty": "unknown",
        "use_case": excerpt_text[:180] if excerpt_text else "unknown",
        "category": (
            str((mechanisms[0] or {}).get("name") or "") if mechanisms
            else str(topics[0]) if topics else "boundary candidate"
        ),
        "artifact_type": str(artifact_types[0]) if artifact_types else "application",
        "reasons": [{
            "text": "Deterministic evaluation fixture based on candidate evidence",
            "evidence_ids": [citation["id"]],
        }],
        "risks": [{
            "text": "Semantic usefulness still requires blind human review",
            "evidence_ids": [(metadata or citation)["id"]],
        }],
        "transferability": 65,
        "boundary_value": 70 if mechanisms else 45,
    }


def _compact_ranking(ranking: dict[str, Any] | None) -> dict[str, Any] | None:
    if not ranking:
        return None
    return {
        "display_order": list(ranking.get("display_order") or []),
        "items": [{
            "repo": item.get("repo"),
            "boundary_role": item.get("boundary_role"),
            "new_mechanisms": list(item.get("new_mechanisms") or []),
            "why_different": item.get("why_different"),
        } for item in ranking.get("items") or []],
        "boundary_summary": ranking.get("boundary_summary") or {},
        "coverage": ranking.get("coverage") or {},
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    sys.path.insert(0, str(args.source_root.resolve() / "src"))
    github_module = importlib.import_module("muse_shroom.github")
    models_module = importlib.import_module("muse_shroom.models")
    search_module = importlib.import_module("muse_shroom.search")
    ranking_module = importlib.import_module("muse_shroom.ranking")
    storage_module = importlib.import_module("muse_shroom.storage")
    version_module = importlib.import_module("muse_shroom")

    prompts_payload = json.loads(args.prompts.read_text(encoding="utf-8"))
    prompts = prompts_payload.get("prompts", [])
    if not isinstance(prompts, list) or not prompts:
        raise ValueError("prompt file must contain a non-empty prompts array")

    args.data_dir.mkdir(parents=True, exist_ok=True)
    store = storage_module.Store(args.data_dir)
    delegate = None
    if args.mode == "capture":
        delegate = (
            SyntheticFixtureGitHub(github_module) if args.synthetic_fixture
            else github_module.GitHubClient(store)
        )
    github = CassetteGitHub(
        github_module, args.cassette, delegate=delegate,
        search_interval=args.search_interval,
    )
    engine_options = {}
    if "reference_time" in inspect.signature(search_module.SearchEngine).parameters:
        engine_options["reference_time"] = github.payload.get("captured_at")
    engine = search_module.SearchEngine(store, github, **engine_options)
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
            output = engine.search(request, "deep" if args.agentic else "quick")
            if args.agentic:
                # The committed synthetic fixture freezes the historical query
                # set. Real evaluations may combine an uncovered direction with
                # an observed anchor and relationship seed.
                used_directions: set[str] = (
                    {
                        str(item.get("term") or "").strip().casefold()
                        for item in (
                            ((output.get("observation") or {}).get("query_summary") or {})
                            .get("executed") or []
                        )
                        if str(item.get("term") or "").strip()
                    }
                    if args.synthetic_fixture else set()
                )
                for _ in range(max(0, args.agentic_iterations)):
                    observation = output.get("observation") or {}
                    hypothesis = deterministic_hypothesis(observation, used_directions)
                    if hypothesis is None or output.get("next_action") != "iterate":
                        break
                    output = engine.iterate(output["search_id"], hypothesis)
                    if output.get("next_action") != "iterate":
                        break
                    output = engine.observe(output["search_id"])
                if output.get("next_action") == "iterate":
                    output = engine.iterate(output["search_id"], {
                        "decision": "stop",
                        "stop_reason": "deterministic evaluation policy exhausted",
                    })
            candidates = list(output.get("candidates", []))[:args.candidate_limit]
            session = store.load_search(output["search_id"])
            if args.agentic:
                selected = [
                    item for item in session.get("candidates", [])
                    if item.get("selected_for_assessment")
                ]
                candidates = selected[:args.candidate_limit]
            ranking = None
            if args.boundary_rank:
                if not args.agentic:
                    raise ValueError("--boundary-rank requires --agentic")
                assessments = [
                    deterministic_assessment(candidate, request_payload)
                    for candidate in candidates
                ]
                if assessments:
                    rank_options = {}
                    if "reference_time" in inspect.signature(
                        ranking_module.rank_search
                    ).parameters:
                        rank_options["reference_time"] = github.payload.get("captured_at")
                    ranking = ranking_module.rank_search(
                        store, output["search_id"], assessments, **rank_options,
                    )
            boundary = dict(output.get("boundary") or {})
            if ranking:
                boundary = dict(ranking.get("boundary") or boundary)
            elif args.agentic:
                snapshot = store.latest_boundary_snapshot(output["search_id"]) or {}
                boundary = dict(snapshot.get("boundary") or boundary)
            presented_count = len(boundary.get("presented_mechanisms") or [])
            recalled_candidates = list(session.get("candidates", []))
            if ranking:
                ranked_repos = {
                    str(item.get("repo") or "").casefold()
                    for item in ranking.get("items") or []
                }
                presentation_candidates = [
                    item for item in recalled_candidates
                    if str(item.get("full_name") or "").casefold() in ranked_repos
                ]
                redundancy_scope = "ranking_items"
            else:
                presentation_candidates = candidates
                redundancy_scope = "selected_for_assessment" if args.agentic else "candidates"
            try:
                iteration_module = importlib.import_module("muse_shroom.iteration")
                loop_diagnostics = iteration_module.session_loop_diagnostics(
                    store, output["search_id"]
                )
            except (ImportError, AttributeError, KeyError):
                loop_diagnostics = {
                    "iterations_used": 0, "mode": "single-pass",
                    "planned_iteration_count": 0,
                    "executed_iteration_count": 0,
                    "retrieval_changing_iteration_count": 0,
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
                    "retrieval_mechanism_redundancy": _mechanism_redundancy(recalled_candidates),
                    "presentation_mechanism_redundancy": _mechanism_redundancy(
                        presentation_candidates
                    ),
                    "mechanism_redundancy": _mechanism_redundancy(presentation_candidates),
                    "redundancy_scope": redundancy_scope,
                    "boundary_gain": len(
                        (output.get("boundary_delta") or {}).get("new_mechanisms") or []
                    ),
                    "direction_coverage": (output.get("coverage") or {}).get(
                        "direction_coverage", 0.0
                    ),
                    "newly_presented_mechanism_count": len(
                        (output.get("boundary_delta") or {}).get("new_presented_mechanisms") or []
                    ),
                },
                "loop_diagnostics": loop_diagnostics,
                "ranking": _compact_ranking(ranking),
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
        "policy": "deterministic",
        "stage": (
            "agentic_boundary_rank" if args.boundary_rank
            else "agentic_assessment_shortlist" if args.agentic
            else "assessment_shortlist"
        ),
        "agentic": bool(args.agentic), "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
