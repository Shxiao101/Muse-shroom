"""Thin MCP adapter over Muse-shroom Core. Session state stays in SQLite."""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any, Literal

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

from . import __version__
from .auth import AuthError
from .github import GitHubError
from .mcp_schema import (
    HOST_INSTRUCTIONS,
    MUSE_ITERATE_DESCRIPTION,
    MUSE_RANK_DESCRIPTION,
    MUSE_SEARCH_DESCRIPTION,
    publish_agent_schemas,
)
from .models import ContractError
from .services import MuseCore

_TOKEN_RE = re.compile(
    r"(?i)(ghp_|github_pat_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9_]+"
)


def _sdk():
    try:
        from mcp.server import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
        from mcp.types import ToolAnnotations
    except ImportError as exc:
        raise ImportError(
            "Muse-shroom MCP requires the optional extra: pip install 'muse-shroom[mcp]'"
        ) from exc
    return MCPServer, ToolError, ToolAnnotations


def _redact(text: str) -> str:
    value = os.environ.get("GITHUB_TOKEN")
    if value:
        text = text.replace(value, "[redacted]")
    return _TOKEN_RE.sub("[redacted]", text)


def create_server(*, data_dir: str | None = None, github: Any | None = None, log_level: LogLevel = "INFO"):
    MCPServer, ToolError, ToolAnnotations = _sdk()
    core = MuseCore(data_dir=data_dir, github=github)
    mcp = MCPServer(
        "muse-shroom",
        version=__version__,
        log_level=log_level,
        instructions=HOST_INSTRUCTIONS,
    )
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    local_write = ToolAnnotations(read_only_hint=False, open_world_hint=False)
    github_write = ToolAnnotations(read_only_hint=False, open_world_hint=True)

    def invoke(fn):
        try:
            return fn()
        except (ContractError, AuthError, GitHubError, KeyError, ValueError) as exc:
            raise ToolError(f"{type(exc).__name__}: {_redact(str(exc))}") from exc

    @mcp.tool(annotations=read_only)
    def muse_status() -> dict[str, Any]:
        """Report version, whether a GitHub credential is configured, and database availability. Never returns a token."""
        return invoke(core.status)

    @mcp.tool(annotations=github_write, description=MUSE_SEARCH_DESCRIPTION)
    def muse_search(
        request: dict[str, Any],
        mode: Literal["quick", "deep"] = "quick",
        refresh: bool = False,
    ) -> dict[str, Any]:
        return invoke(lambda: core.search(request, mode, refresh=refresh))

    @mcp.tool(annotations=read_only)
    def muse_observe(search_id: str) -> dict[str, Any]:
        """Read-only restore of a search session. No GitHub calls, no iteration, no boundary writes. Returns observation, remaining_budget, next_action, can_iterate."""
        return invoke(lambda: core.observe(search_id))

    @mcp.tool(annotations=github_write, description=MUSE_ITERATE_DESCRIPTION)
    def muse_iterate(search_id: str, hypothesis: dict[str, Any]) -> dict[str, Any]:
        return invoke(lambda: core.iterate(search_id, hypothesis))

    @mcp.tool(annotations=local_write, description=MUSE_RANK_DESCRIPTION)
    def muse_rank(search_id: str, assessments: list[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
        return invoke(lambda: core.rank(search_id, assessments))

    @mcp.tool(annotations=read_only)
    def muse_inspect(repo: str, search_id: str | None = None) -> dict[str, Any]:
        """Debug-only local snapshot of one repository. Not part of the default search → observe → iterate → rank flow."""
        return invoke(lambda: core.inspect(repo, search_id))

    publish_agent_schemas(mcp)
    return mcp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="muse-shroom-mcp",
        description="Muse-shroom MCP server (stdio by default)",
    )
    parser.add_argument("--data-dir", default=None, help="override the platform data directory")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="stdio is the local default; streamable-http is a local extension entry, not a hosted service",
    )
    parser.add_argument("--host", default="127.0.0.1", help="streamable-http bind host")
    parser.add_argument("--port", type=int, default=8000, help="streamable-http bind port")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _sdk()
    except ImportError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    data_dir = args.data_dir or os.environ.get("MUSE_SHROOM_DATA_DIR")
    mcp = create_server(data_dir=data_dir)
    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
