"""Isolated host-evaluation prepare / collect / score workflow."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPTS = ROOT / "evaluation" / "boundary-prompts.json"
DEFAULT_GOLDEN = ROOT / "evaluation" / "boundary-golden-cases.json"
DEFAULT_HOLDOUT_GOLDEN = ROOT / "evaluation" / "holdout" / "boundary-golden-cases.json"
TOKEN_RE = __import__("re").compile(r"[A-Za-z0-9_+#]+|[\u3400-\u9fff]+")


def _normalized(value: Any) -> str:
    return " ".join(TOKEN_RE.findall(str(value).casefold()))


def _concept_keys(concepts: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for concept in concepts:
        for raw in [concept.get("term"), *(concept.get("aliases") or [])]:
            key = _normalized(raw)
            if key:
                keys.add(key)
    return keys


def prepare(bundle: Path, *, prompts: Path = DEFAULT_PROMPTS) -> dict[str, Any]:
    """Create a sanitized bundle: production code, Skill, anonymous cases."""
    if bundle.exists():
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True)
    shutil.copytree(ROOT / "src", bundle / "src")
    shutil.copytree(ROOT / "skills", bundle / "skills")
    for name in ("pyproject.toml", "LICENSE", "README.md"):
        source = ROOT / name
        if source.exists():
            shutil.copy2(source, bundle / name)
    payload = json.loads(prompts.read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    anonymous = []
    for index, prompt in enumerate(payload.get("prompts") or [], start=1):
        anon_id = f"case-{index:02d}"
        real_id = str(prompt.get("id") or anon_id)
        mapping[anon_id] = real_id
        anonymous.append({
            "id": anon_id,
            "request": prompt.get("request"),
        })
    (bundle / "cases.json").write_text(
        json.dumps({"schema_version": 1, "cases": anonymous}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    mapping_path = bundle.parent / f"{bundle.name}.case-map.json"
    mapping_path.write_text(
        json.dumps({"mapping": mapping}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"bundle": str(bundle), "mapping": str(mapping_path), "cases": len(anonymous)}


def _engine(source_root: Path, data_dir: Path, github: Any, *, semantic: bool):
    sys.path.insert(0, str(source_root / "src"))
    from muse_shroom.models import SearchRequest
    from muse_shroom.search import SearchEngine
    from muse_shroom.ranking import rank_search
    from muse_shroom.storage import Store
    store = Store(data_dir)
    engine = SearchEngine(store, github, semantic_sidecar=semantic)
    return engine, store, SearchRequest, rank_search


def collect(
    *,
    transcript: Path,
    cassette: Path,
    output: Path,
    data_dir: Path,
    source_root: Path = ROOT,
    mode: str = "replay",
    search_interval: float = 3.5,
    semantic: bool = True,
) -> dict[str, Any]:
    """Replay a host transcript against capture or cassette replay."""
    sys.path.insert(0, str(source_root / "src"))
    sys.path.insert(0, str(source_root / "evaluation"))
    try:
        from cassette import CassetteGitHub
    except ModuleNotFoundError:
        from evaluation.cassette import CassetteGitHub
    import muse_shroom.github as github_module
    from muse_shroom.storage import Store

    payload = json.loads(transcript.read_text(encoding="utf-8"))
    cases = payload.get("cases") or []
    data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(data_dir)
    delegate = None
    if mode == "capture":
        delegate = github_module.GitHubClient(store)
    github = CassetteGitHub(
        github_module, cassette, delegate=delegate,
        search_interval=search_interval if mode == "capture" else 0.0,
        serial_capture=mode == "capture",
    )
    engine, store, SearchRequest, rank_search = _engine(
        source_root, data_dir, github, semantic=semantic,
    )
    results = []
    try:
        for case in cases:
            actions = list(case.get("actions") or [])
            search_id = None
            last = None
            for action in actions:
                tool = str(action.get("tool") or "")
                if tool == "search":
                    request = SearchRequest.from_dict(action["request"], strict=True)
                    last = engine.search(request, str(action.get("mode") or "deep"))
                    search_id = last["search_id"]
                elif tool == "observe":
                    last = engine.observe(search_id)
                elif tool == "iterate":
                    last = engine.iterate(search_id, action.get("hypothesis") or {})
                elif tool == "rank":
                    last = rank_search(
                        store, search_id, action.get("assessments") or {"assessments": []},
                        strict=True,
                    )
                else:
                    raise ValueError(f"unknown host action {tool!r}")
            results.append({
                "case_id": case.get("id"),
                "search_id": search_id,
                "output": last,
                "semantic_hypotheses": (last or {}).get("semantic_hypotheses") or (
                    ((last or {}).get("observation") or {}).get("semantic_hypotheses") or []
                ),
                "sidecar_metrics": (last or {}).get("sidecar_metrics") or (
                    ((last or {}).get("observation") or {}).get("sidecar_metrics") or {}
                ),
            })
    finally:
        github.save()
    output.write_text(json.dumps({
        "schema_version": 1,
        "mode": mode,
        "semantic_sidecar": semantic,
        "results": results,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"cases": len(results), "output": str(output), "cassette": str(cassette)}


def _golden_directions(golden: dict[str, Any], case_id: str) -> list[dict[str, Any]]:
    for case in golden.get("cases") or []:
        if str(case.get("id") or "") == case_id:
            return list(case.get("cross_mechanism_directions") or [])
    return []


def score_case(result: dict[str, Any], directions: list[dict[str, Any]]) -> dict[str, Any]:
    wanted = _concept_keys(directions)
    hypotheses = list(result.get("semantic_hypotheses") or [])
    proposed = [str(item.get("term") or "") for item in hypotheses]
    golden_proposed = [
        term for term in proposed if _normalized(term) in wanted
        or any(_normalized(term) == key for key in wanted)
        or any(key in f" {_normalized(term)} " or _normalized(term) in key for key in wanted)
    ]
    queries = [
        query for item in hypotheses for query in item.get("queries") or []
        if query.get("executed")
    ]
    golden_queries = [
        query for query in queries
        if any(key in _normalized(query.get("query")) for key in wanted)
    ]
    evidence = [
        item for item in hypotheses
        if item.get("status") in {"evidence_found", "validated", "presented"}
        or item.get("evidence_repos")
    ]
    golden_evidence = [
        item for item in evidence
        if _normalized(item.get("term")) in wanted
        or any(key in _normalized(item.get("term")) for key in wanted)
    ]
    return {
        "host_hypotheses_proposed": len(hypotheses),
        "golden_cross_domain_hypotheses_proposed": len(golden_proposed),
        "pure_and_bridge_queries_executed": len(queries),
        "golden_direction_queries_executed": len(golden_queries),
        "mechanism_evidence_found": len(evidence),
        "golden_mechanism_evidence_found": len(golden_evidence),
        "validated": sum(1 for item in hypotheses if item.get("status") in {"validated", "presented"}),
        "presented": sum(1 for item in hypotheses if item.get("presented") or item.get("status") == "presented"),
        "capability_query_hit": bool(golden_queries),
        "capability_evidence_hit": bool(golden_evidence),
    }


def score(
    collect_path: Path,
    *,
    mapping_path: Path | None,
    golden: Path = DEFAULT_GOLDEN,
    holdout_golden: Path = DEFAULT_HOLDOUT_GOLDEN,
    disabled_path: Path | None = None,
) -> dict[str, Any]:
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    payload = json.loads(collect_path.read_text(encoding="utf-8"))
    golden_payload = json.loads(golden.read_text(encoding="utf-8"))
    mapping = {}
    if mapping_path and mapping_path.exists():
        mapping = json.loads(mapping_path.read_text(encoding="utf-8")).get("mapping") or {}
    cases = []
    for result in payload.get("results") or []:
        anon_id = str(result.get("case_id") or "")
        real_id = mapping.get(anon_id, anon_id)
        directions = _golden_directions(golden_payload, real_id)
        scored = score_case(result, directions)
        scored["case_id"] = real_id
        scored["anonymous_id"] = anon_id
        scored["sidecar_metrics"] = result.get("sidecar_metrics") or {}
        cases.append(scored)
    query_hits = sum(1 for item in cases if item["capability_query_hit"])
    evidence_hits = sum(1 for item in cases if item["capability_evidence_hit"])
    no_can = None
    if disabled_path and disabled_path.exists():
        no_can = compare_no_cannibalization(payload, json.loads(disabled_path.read_text(encoding="utf-8")))
    gate = {
        "valid_transcript": bool(cases),
        "no_cannibalization_passed": None if no_can is None else bool(no_can.get("passed")),
        "development_query_hit": query_hits >= 1,
        "development_evidence_hit": evidence_hits >= 1,
    }
    gate["passed"] = (
        gate["valid_transcript"]
        and gate["development_query_hit"]
        and gate["development_evidence_hit"]
        and (gate["no_cannibalization_passed"] is not False)
    )
    return {
        "schema_version": 1,
        "cases": cases,
        "aggregate": {
            "query_hits": query_hits,
            "evidence_hits": evidence_hits,
            "host_hypotheses_proposed": sum(item["host_hypotheses_proposed"] for item in cases),
        },
        "no_cannibalization": no_can,
        "capability_gate": gate,
        "observational": {
            "holdout_discovery_metrics": "not_scored_here",
            "development_aggregate_quality": "observational",
        },
    }


def compare_no_cannibalization(enabled: dict[str, Any], disabled: dict[str, Any]) -> dict[str, Any]:
    from muse_shroom.sidecar import compare_base_artifacts

    diffs: list[dict[str, Any]] = []
    enabled_by = {str(item.get("case_id")): item for item in enabled.get("results") or []}
    disabled_by = {str(item.get("case_id")): item for item in disabled.get("results") or []}
    for case_id, enabled_item in enabled_by.items():
        other = disabled_by.get(case_id)
        if other is None:
            diffs.append({"case_id": case_id, "fields": ["missing_disabled_case"]})
            continue
        left = ((enabled_item.get("output") or {}).get("observation") or {})
        right = ((other.get("output") or {}).get("observation") or {})
        # Prefer stored base_artifacts when present on sidecar metrics path.
        left_snap = (enabled_item.get("sidecar_metrics") or {}).get("base_compare") or {
            "query_fingerprints": [
                str(item.get("fingerprint") or "")
                for item in ((left.get("query_summary") or {}).get("executed") or [])
            ],
        }
        right_snap = (other.get("sidecar_metrics") or {}).get("base_compare") or {
            "query_fingerprints": [
                str(item.get("fingerprint") or "")
                for item in ((right.get("query_summary") or {}).get("executed") or [])
            ],
        }
        changed = compare_base_artifacts(left_snap, right_snap) if left_snap.keys() & {
            "candidate_names", "shortlist_names",
        } else (
            ["query_fingerprints"] if left_snap.get("query_fingerprints") != right_snap.get("query_fingerprints")
            else []
        )
        if changed:
            diffs.append({"case_id": case_id, "fields": changed})
    return {"passed": not diffs, "diffs": diffs}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Host evaluation prepare / collect / score")
    sub = parser.add_subparsers(dest="command", required=True)

    prepare_cmd = sub.add_parser("prepare")
    prepare_cmd.add_argument("--bundle", type=Path, required=True)
    prepare_cmd.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS)

    collect_cmd = sub.add_parser("collect")
    collect_cmd.add_argument("--transcript", type=Path, required=True)
    collect_cmd.add_argument("--cassette", type=Path, required=True)
    collect_cmd.add_argument("--output", type=Path, required=True)
    collect_cmd.add_argument("--data-dir", type=Path, required=True)
    collect_cmd.add_argument("--mode", choices=("capture", "replay"), default="replay")
    collect_cmd.add_argument("--search-interval", type=float, default=3.5)
    collect_cmd.add_argument("--semantic-disabled", action="store_true")
    collect_cmd.add_argument("--source-root", type=Path, default=ROOT)

    score_cmd = sub.add_parser("score")
    score_cmd.add_argument("--collect", type=Path, required=True)
    score_cmd.add_argument("--mapping", type=Path)
    score_cmd.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    score_cmd.add_argument("--disabled", type=Path)
    score_cmd.add_argument("--output", type=Path)

    args = parser.parse_args(argv)
    if args.command == "prepare":
        result = prepare(args.bundle, prompts=args.prompts)
    elif args.command == "collect":
        result = collect(
            transcript=args.transcript, cassette=args.cassette, output=args.output,
            data_dir=args.data_dir, source_root=args.source_root, mode=args.mode,
            search_interval=args.search_interval, semantic=not args.semantic_disabled,
        )
    else:
        result = score(
            args.collect, mapping_path=args.mapping, golden=args.golden,
            disabled_path=args.disabled,
        )
        if args.output:
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "score" and not (result.get("capability_gate") or {}).get("passed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
