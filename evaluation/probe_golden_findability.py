"""Probe whether Golden cross-mechanism directions exist in the GitHub corpus.

A Golden `cross_mechanism_direction` is only a usable target if some repository
actually carries the term. The holdout release gate requires an exact-Golden
hypothesis to reach `mechanism_match` evidence from a retrieved repository, so a
term absent from GitHub makes that chain impossible for any Agent and any code:
the case is not hard, it is unpassable by construction.

This probe issues one read-only repository search per term and records what comes
back. It writes only to `evaluation/results/`, which is git-ignored, so holdout
vocabulary never reaches an Agent-visible file.

    python evaluation/probe_golden_findability.py --suite holdout
    python evaluation/probe_golden_findability.py --suite development --suite holdout

The default pacing matches the capture clock used elsewhere (3.5s between search
requests, comfortably under the 30/min search ceiling).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from muse_shroom.github import GitHubClient, GitHubError  # noqa: E402
from muse_shroom.storage import Store  # noqa: E402

SUITES = {
    "development": ROOT / "evaluation" / "boundary-golden-cases.json",
    "holdout": ROOT / "evaluation" / "holdout" / "boundary-golden-cases.json",
}
DEFAULT_OUTPUT = ROOT / "evaluation" / "results" / "golden-findability.json"
SEARCH_INTERVAL = 3.5
# The engine only ever matches a mechanism against these fields, so a term that
# exists solely in, say, commit messages is still unusable as a Golden target.
SEARCH_FIELDS = "in:name,description,topics,readme"
CJK = re.compile(r"[㐀-鿿]")


def _quote(term: str) -> str:
    cleaned = " ".join(str(term).split())
    return f'"{cleaned}"' if cleaned else ""


def _terms(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for case in payload.get("cases") or []:
        for direction in case.get("cross_mechanism_directions") or []:
            primary = direction.get("term")
            for term in [primary, *(direction.get("aliases") or [])]:
                text = " ".join(str(term or "").split())
                if not text:
                    continue
                rows.append({
                    "case_id": str(case.get("id") or ""),
                    "direction_id": str(direction.get("id") or ""),
                    "term": text,
                    "is_alias": text != " ".join(str(primary or "").split()),
                    "cjk": bool(CJK.search(text)),
                })
    return rows


def probe(
    suites: list[str], *, output: Path, per_page: int = 10,
    interval: float = SEARCH_INTERVAL, data_dir: Path | None = None,
) -> dict[str, Any]:
    store = Store(data_dir or (ROOT / "evaluation" / "results" / "probe-data"))
    client = GitHubClient(store)
    results: list[dict[str, Any]] = []
    last = 0.0
    try:
        for suite in suites:
            for row in _terms(SUITES[suite]):
                query = f"{_quote(row['term'])} {SEARCH_FIELDS}"
                wait = interval - (time.monotonic() - last)
                if last and wait > 0:
                    time.sleep(wait)
                last = time.monotonic()
                record = {"suite": suite, **row, "query": query}
                try:
                    result = client.search_repositories(query, per_page=per_page, sort="stars")
                except GitHubError as exc:
                    record.update({
                        "error": f"{type(exc).__name__}: {exc}", "total_count": None,
                    })
                    results.append(record)
                    print(json.dumps(record, ensure_ascii=False), flush=True)
                    continue
                payload = result.data if isinstance(result.data, dict) else {}
                items = list(payload.get("items") or [])
                record.update({
                    "total_count": int(payload.get("total_count") or 0),
                    "returned": len(items),
                    "top_repos": [
                        {
                            "full_name": item.get("full_name"),
                            "stars": item.get("stargazers_count"),
                        }
                        for item in items[:3]
                    ],
                })
                results.append(record)
                print(json.dumps(record, ensure_ascii=False), flush=True)
    finally:
        store.close()

    by_case: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in results:
        by_case.setdefault((item["suite"], item["case_id"]), []).append(item)
    cases = []
    for (suite, case_id), rows in sorted(by_case.items()):
        findable = [r for r in rows if (r.get("total_count") or 0) > 0]
        cases.append({
            "suite": suite, "case_id": case_id,
            "terms": len(rows), "findable_terms": len(findable),
            "unfindable": sorted(
                r["term"] for r in rows if (r.get("total_count") or 0) == 0 and not r.get("error")
            ),
            "errors": sorted(r["term"] for r in rows if r.get("error")),
            # A case with no findable term cannot satisfy the capability chain.
            "reachable": bool(findable),
        })
    summary = {
        "schema_version": 1,
        "search_fields": SEARCH_FIELDS,
        "suites": {
            suite: {
                "cases": sum(1 for c in cases if c["suite"] == suite),
                "unreachable_cases": sorted(
                    c["case_id"] for c in cases if c["suite"] == suite and not c["reachable"]
                ),
                "terms": sum(c["terms"] for c in cases if c["suite"] == suite),
                "findable_terms": sum(
                    c["findable_terms"] for c in cases if c["suite"] == suite
                ),
            }
            for suite in suites
        },
        "cases": cases,
        "probes": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite", action="append", choices=sorted(SUITES), dest="suites",
        help="repeatable; defaults to both suites",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--per-page", type=int, default=10)
    parser.add_argument("--interval", type=float, default=SEARCH_INTERVAL)
    args = parser.parse_args(argv)
    suites = args.suites or sorted(SUITES)
    summary = probe(
        suites, output=args.output, per_page=args.per_page, interval=args.interval,
    )
    print(json.dumps({
        "output": str(args.output),
        "suites": summary["suites"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
