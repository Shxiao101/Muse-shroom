from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .github import GitHubClient, GitHubError
from .models import ContractError, SearchRequest
from .ranking import rank_search
from .search import SearchEngine
from .storage import Store


def _json_input(path: str | None) -> Any:
    if path in {None, "-"}:
        return json.load(sys.stdin)
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _emit(payload: Any, output_format: str = "json") -> None:
    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if isinstance(payload, dict) and "buckets" in payload:
            for title, items in payload["buckets"].items():
                print(f"\n{title.upper()}")
                for item in items:
                    print(f"- {item['repo']} ({item['scores'][title[:-1] if title == 'gems' else title]}) {item.get('url')}")
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="muse-shroom", description="Evidence-backed GitHub inspiration discovery")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data-dir", default=None, help="override the platform data directory")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    search = sub.add_parser("search")
    search.add_argument("--request", required=True, help="request JSON path or - for stdin")
    search.add_argument("--mode", choices=("quick", "deep"), default="quick")
    expand = sub.add_parser("expand")
    expand.add_argument("--search-id", required=True)
    expand.add_argument("--refinement", required=True, help="refinement JSON path or - for stdin")
    rank = sub.add_parser("rank")
    rank.add_argument("--search-id", required=True)
    rank.add_argument("--assessments", required=True, help="assessment JSON path or - for stdin")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("repo")
    inspect.add_argument("--search-id")
    feedback = sub.add_parser("feedback")
    feedback.add_argument("repo", nargs="?")
    feedback.add_argument("--input", help="feedback JSON path or - for stdin")
    for name in ("relevant", "interesting", "too-hard"):
        feedback.add_argument(f"--{name}", choices=("yes", "no", "unknown"), default="unknown")
    feedback.add_argument("--note")
    return parser


def _tri(value: str) -> bool | None:
    return True if value == "yes" else False if value == "no" else None


def run(args: argparse.Namespace) -> Any:
    store = Store(args.data_dir)
    try:
        if args.command == "doctor":
            token_present = bool(os.environ.get("GITHUB_TOKEN"))
            ok = token_present
            try:
                store.db.execute("SELECT 1").fetchone()
                database = "ok"
            except sqlite3.Error as exc:
                database, ok = f"error:{exc}", False
            return {
                "ok": ok, "python": sys.version.split()[0], "database": database,
                "database_path": str(store.path), "github_token": "present" if token_present else "missing",
            }
        if args.command in {"search", "expand"}:
            github = GitHubClient(store)
            engine = SearchEngine(store, github)
            if args.command == "search":
                return engine.search(SearchRequest.from_dict(_json_input(args.request)), args.mode)
            return engine.expand(args.search_id, _json_input(args.refinement))
        if args.command == "rank":
            return rank_search(store, args.search_id, _json_input(args.assessments))
        if args.command == "inspect":
            candidate = store.get_candidate(args.repo, args.search_id)
            if candidate is None:
                raise KeyError(f"repository not found in local snapshots: {args.repo}")
            history = store.star_history(args.repo)
            return {"repository": candidate, "star_history": history, "growth_available": len(history) >= 2}
        if args.command == "feedback":
            if args.input:
                payload = _json_input(args.input)
                if not isinstance(payload, dict):
                    raise ContractError("feedback input must be an object")
                repo = str(payload.get("repo", "")).strip()
                if not repo:
                    raise ContractError("feedback repo is required")
                def value(name: str) -> bool | None:
                    raw = payload.get(name)
                    if raw is None:
                        return None
                    if not isinstance(raw, bool):
                        raise ContractError(f"feedback {name} must be true, false, or null")
                    return raw
                relevant, interesting, too_hard = value("relevant"), value("interesting"), value("too_hard")
                note = str(payload["note"]) if payload.get("note") is not None else None
            else:
                if not args.repo:
                    raise ContractError("feedback repo is required unless --input is used")
                repo = args.repo
                relevant, interesting, too_hard = _tri(args.relevant), _tri(args.interesting), _tri(args.too_hard)
                note = args.note
            store.add_feedback(repo, relevant, interesting, too_hard, note)
            return {"ok": True, "repo": repo.lower()}
        raise AssertionError("unreachable")
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
        _emit(result, args.format)
        return 2 if args.command == "doctor" and not result.get("ok", False) else 0
    except (ContractError, GitHubError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
