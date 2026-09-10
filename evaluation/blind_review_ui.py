"""Local localhost UI for scoring a matched-pair blind-review pack.

Reads only `blind-review.json`. Writes only `ratings.json`. Never opens,
names, or displays the un-blinding key. Resume is the ratings file: a
reopened server reloads whatever was last saved.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "localhost."}
STATIC_DIR = Path(__file__).resolve().parent / "blind_review_static"
DIMENSIONS = ("relevance", "interesting", "evidence", "actionability", "diversity")
PREFERRED = frozenset({"A", "B", "tie"})


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


def load_pack(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("blind review pack must carry a non-empty cases array")
    seen: set[tuple[str, int]] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"case {index} must be an object")
        need_id = str(case.get("need_id") or "")
        repetition = case.get("repetition", 1)
        if not need_id:
            raise ValueError(f"case {index} need_id is required")
        if not isinstance(repetition, int) or isinstance(repetition, bool) or repetition < 1:
            raise ValueError(f"case {need_id} repetition must be a positive integer")
        key = (need_id, repetition)
        if key in seen:
            raise ValueError(f"duplicate case for {need_id} repetition {repetition}")
        seen.add(key)
        lists = case.get("lists")
        if not isinstance(lists, dict) or set(lists) != {"A", "B"}:
            raise ValueError(f"case {need_id} repetition {repetition} must have lists A and B")
    return payload


def load_ratings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"evaluations": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    evaluations = payload.get("evaluations")
    if evaluations is None:
        return {"evaluations": []}
    if not isinstance(evaluations, list):
        raise ValueError("ratings evaluations must be an array")
    return {"evaluations": evaluations}


def _score_block(block: Any, *, label: str) -> dict[str, int]:
    if not isinstance(block, dict) or set(block) != set(DIMENSIONS):
        raise ValueError(f"{label} scores must be exactly {', '.join(DIMENSIONS)}")
    scores: dict[str, int] = {}
    for name in DIMENSIONS:
        value = block[name]
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
            raise ValueError(f"{label} {name} must be an integer 1–5")
        scores[name] = value
    return scores


def validate_evaluation(item: Any, case_keys: set[tuple[str, int]]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("each evaluation must be an object")
    need_id = str(item.get("need_id") or "")
    repetition = item.get("repetition")
    if not need_id:
        raise ValueError("evaluation need_id is required")
    if not isinstance(repetition, int) or isinstance(repetition, bool) or repetition < 1:
        raise ValueError(f"evaluation repetition must be a positive integer for {need_id}")
    if (need_id, repetition) not in case_keys:
        raise ValueError(f"evaluation {need_id} repetition {repetition} is not in the pack")
    preferred = item.get("preferred")
    if preferred not in PREFERRED:
        raise ValueError("preferred must be A, B, or tie")
    return {
        "need_id": need_id,
        "repetition": repetition,
        "preferred": preferred,
        "A": _score_block(item.get("A"), label="A"),
        "B": _score_block(item.get("B"), label="B"),
    }


def validate_ratings(payload: Any, pack: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("ratings must be an object")
    evaluations = payload.get("evaluations")
    if not isinstance(evaluations, list):
        raise ValueError("ratings evaluations must be an array")
    case_keys = {
        (str(case["need_id"]), int(case.get("repetition", 1)))
        for case in pack.get("cases") or []
    }
    validated: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for item in evaluations:
        row = validate_evaluation(item, case_keys)
        key = (row["need_id"], row["repetition"])
        if key in seen:
            raise ValueError(f"duplicate rating for {row['need_id']} repetition {row['repetition']}")
        seen.add(key)
        validated.append(row)
    return {"evaluations": validated}


class ReviewHandler(BaseHTTPRequestHandler):
    pack_path: Path
    ratings_path: Path
    pack: dict[str, Any]

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            status, body, content_type = self._route_get(path)
        except ValueError as exc:
            status, body, content_type = _json_bytes(
                {"error": "ValueError", "message": str(exc)}, 400,
            )
        except OSError as exc:
            status, body, content_type = _json_bytes(
                {"error": "OSError", "message": str(exc)}, 500,
            )
        except Exception as exc:
            status, body, content_type = _json_bytes(
                {"error": type(exc).__name__, "message": str(exc)}, 500,
            )
        self._send(status, body, content_type)

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            status, body, content_type = self._route_put(parsed.path)
        except ValueError as exc:
            status, body, content_type = _json_bytes(
                {"error": "ValueError", "message": str(exc)}, 400,
            )
        except OSError as exc:
            status, body, content_type = _json_bytes(
                {"error": "OSError", "message": str(exc)}, 500,
            )
        except Exception as exc:
            status, body, content_type = _json_bytes(
                {"error": type(exc).__name__, "message": str(exc)}, 500,
            )
        self._send(status, body, content_type)

    def _route_get(self, path: str) -> tuple[int, bytes, str]:
        if path == "/api/pack":
            return _json_bytes({
                "schema_version": self.pack.get("schema_version"),
                "stage": self.pack.get("stage"),
                "cases": self.pack.get("cases"),
            })
        if path == "/api/ratings":
            return _json_bytes(load_ratings(self.ratings_path))
        if path == "/api/meta":
            ratings = load_ratings(self.ratings_path)
            return _json_bytes({
                "case_count": len(self.pack.get("cases") or []),
                "rated_count": len(ratings["evaluations"]),
                "dimensions": list(DIMENSIONS),
            })
        if path.startswith("/api/"):
            return _json_bytes({"error": "NotFound", "message": "unknown endpoint"}, 404)
        return self._static(path)

    def _route_put(self, path: str) -> tuple[int, bytes, str]:
        if path != "/api/ratings":
            return _json_bytes({"error": "NotFound", "message": "unknown endpoint"}, 404)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"ratings JSON is invalid: {exc}") from exc
        ratings = validate_ratings(payload, self.pack)
        self.ratings_path.parent.mkdir(parents=True, exist_ok=True)
        self.ratings_path.write_text(
            json.dumps(ratings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        return _json_bytes(ratings)

    def _static(self, path: str) -> tuple[int, bytes, str]:
        relative = path.lstrip("/") or "index.html"
        candidate = (STATIC_DIR / relative).resolve()
        root = STATIC_DIR.resolve()
        if candidate != root and root not in candidate.parents:
            return 404, b"not found", "text/plain; charset=utf-8"
        if not candidate.is_file():
            candidate = STATIC_DIR / "index.html"
        data = candidate.read_bytes()
        suffix = candidate.suffix
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
        }.get(suffix) or mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        return 200, data, content_type


def build_server(
    *,
    pack_path: Path,
    ratings_path: Path,
    host: str = "127.0.0.1",
    port: int = 8768,
) -> ThreadingHTTPServer:
    if not is_loopback_host(host):
        raise ValueError(
            "the review UI is a localhost tool; refusing to bind "
            f"{host!r} (that would expose ratings with no auth)"
        )
    pack = load_pack(pack_path)
    ReviewHandler.pack_path = pack_path
    ReviewHandler.ratings_path = ratings_path
    ReviewHandler.pack = pack
    return ThreadingHTTPServer((host, port), ReviewHandler)


def run_review_ui(
    *,
    pack_path: Path,
    ratings_path: Path,
    host: str = "127.0.0.1",
    port: int = 8768,
    open_browser: bool = True,
) -> int:
    server = build_server(pack_path=pack_path, ratings_path=ratings_path, host=host, port=port)
    bound_host, bound_port = server.server_address[:2]
    url = f"http://{bound_host}:{bound_port}/"
    print(json.dumps({
        "ok": True,
        "url": url,
        "review": str(pack_path),
        "ratings": str(ratings_path),
        "cases": len(ReviewHandler.pack.get("cases") or []),
    }, ensure_ascii=False, indent=2), flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nReview UI stopped.", flush=True)
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score a matched-pair blind-review pack in a localhost UI",
    )
    parser.add_argument("--review", type=Path, required=True, help="path to blind-review.json")
    parser.add_argument("--ratings", type=Path, required=True, help="path to write ratings.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run_review_ui(
            pack_path=args.review, ratings_path=args.ratings,
            host=args.host, port=args.port, open_browser=not args.no_browser,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "message": str(exc)}, ensure_ascii=False), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
