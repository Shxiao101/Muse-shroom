"""Measure what the search kernel recalls for real host requests, without an Agent.

Development-set benchmark for the recall redesign. It replays the SearchRequests a
real host wrote during the matched A/B gate, so the vocabulary was not authored for
evaluation. Acceptance is reference-free: term coverage, silent drops, seat share,
identity-matched pool size and GitHub cost. The next gate reuses these same needs, so
recall of the maintainer's preferred repositories is a diagnostic only, behind
--with-reference, and only after the design is frozen.

    python evaluation/recall_bench.py plan
    python evaluation/recall_bench.py run --config baseline --record
    python evaluation/recall_bench.py compare baseline page30

Results and cassettes go to evaluation/results/recall-bench/, which git ignores.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "src"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from evaluation.cassette import CassetteGitHub  # noqa: E402
from evaluation.score_matched_ab import reveal  # noqa: E402
from muse_shroom import github as github_module  # noqa: E402
from muse_shroom.models import SearchRequest  # noqa: E402
from muse_shroom.queries import build_queries  # noqa: E402
from muse_shroom.search import SearchEngine  # noqa: E402
from muse_shroom.selection import identity_concept_evidence  # noqa: E402
from muse_shroom.storage import Store  # noqa: E402

GATE = ROOT / "evaluation" / "results" / "matched-ab-gate"
OUTPUT = ROOT / "evaluation" / "results" / "recall-bench"
# Matches probe_golden_findability.py: comfortably under the 30/min search ceiling.
SEARCH_INTERVAL = 3.5
REQUEST_FIELDS = {
    "request", "problem_concepts", "mechanisms", "exploration_directions",
    "artifact_types", "constraints", "exclusions", "exploration_level",
}
GROUPS = (
    ("problem_concepts", "problem"),
    ("mechanisms", "mechanism"),
    ("exploration_directions", "exploration"),
)
CONFIGS: dict[str, dict[str, Any]] = {
    "baseline": {"mode": "quick", "engine": {}},
    "page30": {"mode": "quick", "engine": {"search_page_size": 30}},
    # Same parameters as baseline; recorded after the query compiler change lands.
    "compiler": {"mode": "quick", "engine": {}},
    # A scripted host: each round searches request terms the kernel left unsearched.
    "deep_script": {"mode": "deep", "engine": {}, "script_rounds": 3},
}
QUOTED = re.compile(r'"([^"]+)"')


def _objects(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from _objects(item)
    elif isinstance(value, str) and value[:1] in "{[":
        try:
            yield from _objects(json.loads(value))
        except ValueError:
            pass


def _first_request(transcript: Path) -> dict[str, Any] | None:
    for line in transcript.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        for obj in _objects(event):
            if isinstance(obj, dict) and isinstance(obj.get("problem_concepts"), list):
                return {key: value for key, value in obj.items() if key in REQUEST_FIELDS}
    return None


def load_cases(gate: Path = GATE) -> list[dict[str, Any]]:
    """The SearchRequest each candidate-arm run actually sent, keyed by need and repetition."""
    cases = []
    for path in sorted((gate / "runs").glob("*.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        if run.get("arm") != "muse-shroom":
            continue
        request = _first_request(gate / "transcripts" / run["run_id"] / "task.jsonl")
        if request is None:
            raise ValueError(f"no SearchRequest found in the transcript of {run['run_id']}")
        cases.append({
            "run_id": run["run_id"], "need_id": run["need_id"],
            "repetition": run["repetition"], "request": request,
        })
    if not cases:
        raise ValueError(f"no candidate-arm runs under {gate / 'runs'}")
    return sorted(cases, key=lambda case: (case["need_id"], case["repetition"]))


def supplied_terms(request: dict[str, Any]) -> list[tuple[str, str]]:
    """(group, term) for every primary term and alias the host supplied."""
    rows: list[tuple[str, str]] = []
    for field, group in GROUPS:
        for concept in request.get(field) or []:
            if isinstance(concept, dict):
                terms = [concept.get("term"), *(concept.get("aliases") or [])]
            else:
                terms = [concept]
            for index, term in enumerate(value for value in terms if value):
                rows.append((f"{group} {'primary' if index == 0 else 'alias'}", str(term)))
    return rows


def plan_metrics(request_payload: dict[str, Any], *, limit: int = 12) -> dict[str, Any]:
    """Offline view of the first search plan: which supplied terms get a query seat."""
    plan = build_queries(SearchRequest.from_dict(request_payload), limit=limit)
    planned = {item["term"] for item in plan}
    primaries = {
        concept["term"] for concept in request_payload.get("problem_concepts") or []
        if isinstance(concept, dict)
    }
    seats: Counter[str] = Counter()
    for item in plan:
        kind = item["kind"]
        if kind == "problem":
            kind = "problem primary" if item["term"] in primaries else "problem alias"
        seats[kind] += 1
    rows = supplied_terms(request_payload)
    unique = list(dict.fromkeys(term for _group, term in rows))
    return {
        "queries": len(plan),
        "seats": dict(seats),
        "supplied": len(unique),
        "unsearched": [term for term in unique if term not in planned],
        "supplied_by_group": dict(Counter(group for group, _term in rows)),
        "dropped_by_group": dict(Counter(group for group, term in rows if term not in planned)),
    }


def summarize_plans(rows: list[dict[str, Any]]) -> dict[str, Any]:
    seats: Counter[str] = Counter()
    supplied: Counter[str] = Counter()
    dropped: Counter[str] = Counter()
    for row in rows:
        seats.update(row["seats"])
        supplied.update(row["supplied_by_group"])
        dropped.update(row["dropped_by_group"])
    total = sum(seats.values()) or 1
    return {
        "requests": len(rows),
        "supplied_terms": sum(row["supplied"] for row in rows),
        "unsearched_terms": sum(len(row["unsearched"]) for row in rows),
        "seats": dict(seats.most_common()),
        "seat_share": {kind: round(count / total, 3) for kind, count in seats.most_common()},
        "drop_rate_by_group": {
            group: round(dropped[group] / supplied[group], 3) for group in sorted(supplied)
        },
    }


def _executed_terms(history: list[dict[str, Any]]) -> set[str]:
    terms = set()
    for row in history:
        if row.get("skipped"):
            continue
        match = QUOTED.search(str(row.get("query") or ""))
        if match:
            terms.add(match.group(1))
    return terms


def _search_metrics(store: Store, search_id: str, case: dict[str, Any],
                    cost: dict[str, int], iterations: int) -> dict[str, Any]:
    pool = store.load_search(search_id)["candidates"]
    shortlist = [item for item in pool if item.get("selected_for_assessment")]
    history = store.query_history(search_id)
    executed = _executed_terms(history)
    unique = list(dict.fromkeys(term for _group, term in supplied_terms(case["request"])))
    return {
        "need_id": case["need_id"], "repetition": case["repetition"], "search_id": search_id,
        "iterations": iterations,
        "executed_queries": sum(1 for row in history if not row.get("skipped")),
        "supplied_terms": len(unique),
        "unsearched_terms": [term for term in unique if term not in executed],
        "pool": len(pool),
        "pool_identity": sum(1 for item in pool if identity_concept_evidence(item)),
        "shortlist": len(shortlist),
        "shortlist_identity": sum(1 for item in shortlist if identity_concept_evidence(item)),
        "pool_repos": sorted(str(item.get("full_name")).lower() for item in pool),
        "shortlist_repos": sorted(str(item.get("full_name")).lower() for item in shortlist),
        "cost": cost,
    }


def _scripted_iterations(engine: SearchEngine, search_id: str, result: dict[str, Any],
                         rounds: int) -> int:
    summary = (result.get("observation") or {}).get("query_summary") or {}
    if "unsearched_terms" not in summary:
        raise SystemExit("deep_script needs observation.query_summary.unsearched_terms (query compiler change)")
    done = 0
    for _ in range(rounds):
        unsearched = ((result.get("observation") or {}).get("query_summary") or {}).get("unsearched_terms") or []
        concepts = [
            row["term"] for row in unsearched
            if isinstance(row, dict) and row.get("group") in {"problem", "mechanism"}
        ][:6]
        if result.get("next_action") != "iterate" or not concepts:
            break
        result = engine.iterate(search_id, {
            "decision": "continue",
            "concepts": concepts,
            "reason": "Scripted benchmark host: search request terms the kernel left unsearched.",
        })
        done += 1
    return done


def run_config(name: str, cases: list[dict[str, Any]], *, record: bool,
               output: Path, interval: float) -> list[dict[str, Any]]:
    config = CONFIGS[name]
    cassette = output / "cassettes" / f"{name}.json.gz"
    cassette.parent.mkdir(parents=True, exist_ok=True)
    if not record and not cassette.exists():
        raise SystemExit(f"no cassette for {name}; record it first with --record")
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix=f"recall-bench-{name}-", ignore_cleanup_errors=True) as data_dir:
        store = Store(data_dir)
        try:
            delegate = github_module.GitHubClient(store) if record else None
            github = CassetteGitHub(
                github_module, cassette, delegate=delegate,
                search_interval=interval if record else 0.0,
                serial_capture=True, auto_save=record,
            )
            engine = SearchEngine(store, github, **config["engine"])
            for case in cases:
                before = dict(github.request_counts)
                result = engine.search(SearchRequest.from_dict(case["request"]), config["mode"], refresh=True)
                iterations = 0
                if config.get("script_rounds"):
                    iterations = _scripted_iterations(engine, result["search_id"], result, config["script_rounds"])
                cost = {key: github.request_counts[key] - before.get(key, 0) for key in github.request_counts}
                rows.append(_search_metrics(store, result["search_id"], case, cost, iterations))
                print(json.dumps({key: rows[-1][key] for key in (
                    "need_id", "repetition", "executed_queries", "pool", "pool_identity", "shortlist_identity",
                )}, ensure_ascii=False), flush=True)
        finally:
            store.close()
    return rows


def summarize_runs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    github_calls = sum(sum(row["cost"].values()) for row in rows)
    pool_identity = sum(row["pool_identity"] for row in rows)
    shortlist = sum(row["shortlist"] for row in rows)
    return {
        "searches": len(rows),
        "executed_queries": sum(row["executed_queries"] for row in rows),
        "supplied_terms": sum(row["supplied_terms"] for row in rows),
        "unsearched_terms": sum(len(row["unsearched_terms"]) for row in rows),
        "pool": sum(row["pool"] for row in rows),
        "pool_identity": pool_identity,
        "shortlist": shortlist,
        "shortlist_identity_share": (
            round(sum(row["shortlist_identity"] for row in rows) / shortlist, 3) if shortlist else 0.0
        ),
        "search_calls": sum(row["cost"].get("search", 0) for row in rows),
        "github_calls": github_calls,
        "identity_per_github_call": round(pool_identity / github_calls, 4) if github_calls else 0.0,
    }


def decide(summaries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Decision rules registered in the plan before any configuration was run."""
    decisions: dict[str, Any] = {}
    base, wide = summaries.get("baseline"), summaries.get("page30")
    if base and wide:
        adopt = (
            wide["pool_identity"] > base["pool_identity"]
            and wide["shortlist_identity_share"] >= base["shortlist_identity_share"]
        )
        decisions["search_page_size"] = 30 if adopt else 10
    quick, deep = summaries.get("compiler") or base, summaries.get("deep_script")
    if quick and deep:
        q, d = quick["identity_per_github_call"], deep["identity_per_github_call"]
        if d != q:
            decisions["candidate_mode"] = "deep" if d > q else "quick"
        else:
            decisions["candidate_mode"] = "deep" if deep["github_calls"] < quick["github_calls"] else "quick"
    return decisions


def preferred_reference(gate: Path = GATE) -> dict[str, set[str]]:
    """Repositories in the lists the maintainer preferred, per need (ties keep both arms)."""
    ratings = json.loads((gate / "ratings.json").read_text(encoding="utf-8"))
    key = json.loads((gate / "blind-key.json").read_text(encoding="utf-8"))
    finals: dict[tuple[str, int, str], set[str]] = {}
    for path in (gate / "runs").glob("*.json"):
        run = json.loads(path.read_text(encoding="utf-8"))
        finals[(run["need_id"], run["repetition"], run["arm"])] = {
            str(item.get("repo")).lower() for item in run.get("candidates") or []
        }
    reference: dict[str, set[str]] = {}
    for row in reveal(ratings, key)["evaluations"]:
        arms = ("muse-shroom", "direct") if row["preferred"] == "tie" else (row["preferred"],)
        bucket = reference.setdefault(row["need_id"], set())
        for arm in arms:
            bucket |= finals[(row["need_id"], row["repetition"], arm)]
    return reference


def reference_recall(rows: list[dict[str, Any]], reference: dict[str, set[str]]) -> dict[str, Any]:
    recalled: dict[str, dict[str, set[str]]] = {}
    for row in rows:
        entry = recalled.setdefault(row["need_id"], {"pool": set(), "shortlist": set()})
        entry["pool"] |= set(row["pool_repos"])
        entry["shortlist"] |= set(row["shortlist_repos"])
    total = sum(len(repos) for repos in reference.values())
    empty = {"pool": set(), "shortlist": set()}
    hits = {
        stage: sum(len(repos & recalled.get(need, empty)[stage]) for need, repos in reference.items())
        for stage in ("pool", "shortlist")
    }
    return {
        "reference_repos": total,
        "recall_at_pool": round(hits["pool"] / total, 3) if total else 0.0,
        "recall_at_shortlist": round(hits["shortlist"] / total, 3) if total else 0.0,
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="offline term coverage and seat share of each first plan")
    plan.add_argument("--limit", type=int, default=12)
    run = commands.add_parser("run", help="execute one configuration against its cassette or GitHub")
    run.add_argument("--config", choices=sorted(CONFIGS), required=True)
    run.add_argument("--record", action="store_true", help="call GitHub and record the cassette")
    run.add_argument("--interval", type=float, default=SEARCH_INTERVAL)
    compare = commands.add_parser("compare", help="summarize configurations and apply the decision rules")
    compare.add_argument("configs", nargs="+", choices=sorted(CONFIGS))
    compare.add_argument(
        "--with-reference", action="store_true",
        help="diagnostic recall of preferred repositories; only after the design is frozen",
    )
    for sub in (plan, run, compare):
        sub.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)

    if args.command == "plan":
        rows = [
            {"need_id": case["need_id"], "repetition": case["repetition"],
             **plan_metrics(case["request"], limit=args.limit)}
            for case in load_cases()
        ]
        summary = summarize_plans(rows)
        _write(args.output / "plan.json", {"limit": args.limit, "summary": summary, "rows": rows})
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if args.command == "run":
        rows = run_config(
            args.config, load_cases(), record=args.record, output=args.output, interval=args.interval,
        )
        config = CONFIGS[args.config]
        summary = summarize_runs(rows)
        _write(args.output / f"{args.config}.json", {
            "config": args.config, "mode": config["mode"], "engine": config["engine"],
            "recorded": args.record, "summary": summary, "rows": rows,
        })
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    payloads = {}
    for name in args.configs:
        path = args.output / f"{name}.json"
        if not path.exists():
            raise SystemExit(f"missing {path}; run --config {name} first")
        payloads[name] = json.loads(path.read_text(encoding="utf-8"))
    summaries = {name: payload["summary"] for name, payload in payloads.items()}
    report: dict[str, Any] = {"summaries": summaries, "decisions": decide(summaries)}
    if args.with_reference:
        reference = preferred_reference()
        report["diagnostic_reference_recall"] = {
            name: reference_recall(payload["rows"], reference) for name, payload in payloads.items()
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
