from __future__ import annotations

import argparse
import getpass
import json
import os
import sqlite3
import sys
import webbrowser
from pathlib import Path
from typing import Any

from . import __version__
from .auth import AuthError, TOKEN_URL, delete_saved_token, resolve_token, save_token, validate_token
from .github import GitHubClient, GitHubError
from .models import ContractError, SearchRequest
from .ranking import rank_search
from .search import SearchEngine, public_candidate
from .storage import Store


def _configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _json_input(path: str | None) -> Any:
    if path in {None, "-"}:
        return json.load(sys.stdin)
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _emit(payload: Any, output_format: str = "json") -> None:
    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
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
    auth = sub.add_parser("auth", help="configure GitHub authentication")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    login = auth_sub.add_parser("login", help="validate and save a token in the system credential store")
    login.add_argument("--no-browser", action="store_true", help="do not open the GitHub token creation page")
    login.add_argument("--token-stdin", action="store_true", help="read the token from stdin instead of a masked prompt")
    auth_sub.add_parser("status", help="show the active credential source and validate it")
    auth_sub.add_parser("logout", help="delete the token saved by Muse-shroom")
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
    candidates = sub.add_parser("candidates", help="list assessment or all recalled candidates")
    candidates.add_argument("--search-id", required=True)
    candidates.add_argument("--scope", choices=("assessment", "all"), default="assessment")
    feedback = sub.add_parser("feedback")
    feedback.add_argument("repo", nargs="?")
    feedback.add_argument("--input", help="feedback JSON path or - for stdin")
    for name in ("relevant", "interesting", "too-hard"):
        feedback.add_argument(f"--{name}", choices=("yes", "no", "unknown"), default="unknown")
    feedback.add_argument("--note")
    return parser


def _tri(value: str) -> bool | None:
    return True if value == "yes" else False if value == "no" else None


def _run_auth(args: argparse.Namespace) -> dict[str, Any]:
    if args.auth_command == "login":
        if not args.no_browser:
            opened = webbrowser.open(TOKEN_URL)
            if not opened:
                print(f"Open this page to create a token: {TOKEN_URL}", file=sys.stderr)
        else:
            print(f"Create a fine-grained token here: {TOKEN_URL}", file=sys.stderr)
        token = sys.stdin.readline().strip() if args.token_stdin else getpass.getpass("Paste GitHub token: ")
        identity = validate_token(token)
        save_token(token)
        return {"ok": True, "login": identity["login"], "credential_source": "keyring"}
    if args.auth_command == "status":
        credential = resolve_token()
        if credential is None:
            return {"ok": False, "configured": False, "credential_source": None}
        identity = validate_token(credential.token)
        return {
            "ok": True, "configured": True, "credential_source": credential.source,
            "login": identity["login"],
        }
    if args.auth_command == "logout":
        removed = delete_saved_token()
        return {
            "ok": True, "removed_from_keyring": removed,
            "environment_override_active": bool(os.environ.get("GITHUB_TOKEN")),
        }
    raise AssertionError("unreachable")


def run(args: argparse.Namespace) -> Any:
    if args.command == "auth":
        return _run_auth(args)
    store = Store(args.data_dir)
    try:
        if args.command == "doctor":
            try:
                credential = resolve_token()
                auth_error = None
            except AuthError as exc:
                credential, auth_error = None, str(exc)
            ok = credential is not None and auth_error is None
            try:
                store.db.execute("SELECT 1").fetchone()
                database = "ok"
            except sqlite3.Error as exc:
                database, ok = f"error:{exc}", False
            return {
                "ok": ok, "python": sys.version.split()[0], "database": database,
                "database_path": str(store.path),
                "github_token": "present" if credential else "missing",
                "credential_source": credential.source if credential else None,
                "credential_error": auth_error,
            }
        if args.command in {"search", "expand"}:
            github = GitHubClient(store)
            engine = SearchEngine(store, github)
            if args.command == "search":
                return engine.search(SearchRequest.from_dict(_json_input(args.request)), args.mode)
            return engine.expand(args.search_id, _json_input(args.refinement))
        if args.command == "rank":
            return rank_search(store, args.search_id, _json_input(args.assessments))
        if args.command == "candidates":
            session = store.load_search(args.search_id)
            items = session["candidates"]
            if args.scope == "assessment":
                items = [item for item in items if item.get("selected_for_assessment", False)]
            result = [public_candidate(item) for item in items]
            result.sort(key=lambda item: (
                -float(item.get("selection_score_components", {}).get("recall", 0)),
                item.get("full_name", "").lower(),
            ))
            return {
                "schema_version": 2, "search_id": args.search_id, "scope": args.scope,
                "candidate_count": len(session["candidates"]), "returned_count": len(result),
                "candidates": result,
            }
        if args.command == "inspect":
            candidate = store.get_candidate(args.repo, args.search_id)
            if candidate is None:
                raise KeyError(f"repository not found in local snapshots: {args.repo}")
            history = store.star_history(args.repo)
            ranking_item = None
            if args.search_id:
                ranking = store.get_ranking(args.search_id)
                if ranking:
                    ranking_item = next((item for bucket in ranking.get("buckets", {}).values()
                                         for item in bucket if item.get("repo", "").lower() == args.repo.lower()), None)
            return {
                "schema_version": 2, "repository": public_candidate(candidate, detailed=True),
                "star_history": history, "growth_available": len(history) >= 2,
                "ranking": ranking_item,
            }
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
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
        _emit(result, args.format)
        needs_auth = args.command == "doctor" or (args.command == "auth" and args.auth_command == "status")
        return 2 if needs_auth and not result.get("ok", False) else 0
    except (AuthError, ContractError, GitHubError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
