"""Local stdlib HTTP server for the read-only Explorer UI."""

from __future__ import annotations

import json
import mimetypes
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .. import __version__
from .read_model import ExplorerReadModel

STATIC_DIR = Path(__file__).resolve().parent / "static"
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "localhost."}


def is_loopback_host(host: str) -> bool:
    value = (host or "").strip().lower()
    if value in LOOPBACK_HOSTS:
        return True
    if value.startswith("127."):
        parts = value.split(".")
        return len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)
    return False


def _json_bytes(payload: Any, status: int = 200) -> tuple[int, bytes, str]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


def _bool_query(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "debug"}


class ExplorerHandler(BaseHTTPRequestHandler):
    data_dir: str | None = None

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _model(self) -> ExplorerReadModel:
        return ExplorerReadModel(data_dir=self.data_dir)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        debug = _bool_query((query.get("debug") or [None])[0])
        at = (query.get("at") or [None])[0]
        try:
            status, body, content_type = self._route(path, debug=debug, at=at)
        except KeyError as exc:
            status, body, content_type = _json_bytes(
                {"error": "KeyError", "message": str(exc)}, 404,
            )
        except ValueError as exc:
            status, body, content_type = _json_bytes(
                {"error": "ValueError", "message": str(exc)}, 400,
            )
        except Exception as exc:
            status, body, content_type = _json_bytes(
                {"error": type(exc).__name__, "message": str(exc)}, 500,
            )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _route(self, path: str, *, debug: bool, at: str | None) -> tuple[int, bytes, str]:
        if path == "/api/meta":
            model = self._model()
            store = model._store()
            try:
                return _json_bytes({
                    "version": __version__,
                    "data_dir": str(store.data_dir),
                    "readonly": True,
                })
            finally:
                store.close()
        if path == "/api/searches":
            return _json_bytes(self._model().list_searches())
        parts = [item for item in path.split("/") if item]
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "searches":
            search_id = parts[2]
            if len(parts) == 3:
                return _json_bytes(self._model().search_summary(search_id, debug=debug))
            if len(parts) == 4 and parts[3] == "boundary":
                return _json_bytes(self._model().boundary_view(search_id, at=at, debug=debug))
            if len(parts) == 4 and parts[3] == "iterations":
                return _json_bytes(self._model().iteration_timeline(search_id, debug=debug))
            if len(parts) == 4 and parts[3] == "result":
                return _json_bytes(self._model().result_view(search_id, debug=debug))
            if len(parts) >= 6 and parts[3] == "repos":
                repo = "/".join(parts[4:])
                return _json_bytes(self._model().repo_detail(search_id, repo, debug=debug))
        if path.startswith("/api/"):
            return _json_bytes({"error": "NotFound", "message": "unknown explorer endpoint"}, 404)
        return self._static(path)

    def _static(self, path: str) -> tuple[int, bytes, str]:
        relative = path.lstrip("/") or "index.html"
        if path.startswith("/s/") or path == "/s":
            relative = "index.html"
        candidate = (STATIC_DIR / relative).resolve()
        root = STATIC_DIR.resolve()
        if candidate != root and root not in candidate.parents:
            return 404, b"not found", "text/plain; charset=utf-8"
        if not candidate.is_file():
            candidate = STATIC_DIR / "index.html"
        data = candidate.read_bytes()
        content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        if candidate.suffix in {".html", ".js", ".css", ".json", ".svg"}:
            content_type = {
                ".html": "text/html; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".json": "application/json; charset=utf-8",
                ".svg": "image/svg+xml",
            }[candidate.suffix]
        return 200, data, content_type


def build_server(
    *,
    data_dir: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    allow_remote: bool = False,
) -> ThreadingHTTPServer:
    if not is_loopback_host(host) and not allow_remote:
        raise ValueError(
            "Explorer is a localhost UI; refusing to bind "
            f"{host!r} without --allow-remote (that would expose local search data with no auth)"
        )
    ExplorerHandler.data_dir = data_dir
    return ThreadingHTTPServer((host, port), ExplorerHandler)


def run_explorer(
    *,
    data_dir: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    allow_remote: bool = False,
) -> int:
    if allow_remote and not is_loopback_host(host):
        print(
            f"WARNING: Explorer has no authentication; {host} will expose local search sessions.",
            flush=True,
        )
    server = build_server(data_dir=data_dir, host=host, port=port, allow_remote=allow_remote)
    url = f"http://{host}:{port}/"
    print(f"Muse-shroom Explorer {__version__} (read-only) at {url}", flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nExplorer stopped.", flush=True)
    finally:
        server.server_close()
    return 0
