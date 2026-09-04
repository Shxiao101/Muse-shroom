from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


METHODS = (
    "search_repositories", "repository", "readme", "latest_release",
    "search_code", "forks", "owner_repositories",
)
SECRET_RE = re.compile(r"(?i)(ghp_|github_pat_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9_]+")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request(method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    return {"method": method, "args": list(args), "kwargs": kwargs}


def _key(request: dict[str, Any]) -> str:
    encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_error(message: str) -> str:
    token = os.environ.get("GITHUB_TOKEN")
    text = message.replace(token, "[redacted]") if token else message
    return SECRET_RE.sub("[redacted]", text)


def load_cassette(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "captured_at": None, "calls": {}}
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schema_version") != 1 or not isinstance(payload.get("calls"), dict):
        raise ValueError(f"unsupported cassette schema: {path}")
    return payload


def save_cassette(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["captured_at"] = payload.get("captured_at") or _utc_now()
    temporary = path.with_name(path.name + ".tmp")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(temporary, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    os.replace(temporary, path)


class CassetteGitHub:
    """Record or replay the small GitHub-client surface used by SearchEngine."""

    def __init__(self, api_module: Any, cassette_path: Path, *, delegate: Any | None,
                 search_interval: float = 0.0, serial_capture: bool | None = None,
                 auto_save: bool = False, monotonic: Any = time.monotonic,
                 wall_time: Any = time.time, sleeper: Any = time.sleep) -> None:
        self.api_module = api_module
        self.cassette_path = cassette_path
        self.delegate = delegate
        self.search_interval = max(0.0, search_interval)
        self.serial_capture = delegate is not None if serial_capture is None else serial_capture
        self.auto_save = bool(auto_save)
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._sleep = sleeper
        self.payload = load_cassette(cassette_path)
        if delegate is not None and not self.payload.get("captured_at"):
            self.payload["captured_at"] = _utc_now()
        self.payload.setdefault("capture_diagnostics", [])
        self.request_counts = {"core": 0, "search": 0, "code_search": 0}
        self.rate_limits: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._serial_lock = threading.Lock()
        self._next_search_at = 0.0
        self._request_sequence = 0

    def _resource(self, method: str) -> str:
        if method == "search_code":
            return "code_search"
        if method == "search_repositories":
            return "search"
        return "core"

    def _result(self, response: dict[str, Any]) -> Any:
        if response["kind"] == "not_found":
            error_type = getattr(self.api_module, "GitHubNotFoundError")
            raise error_type(response.get("message", "recorded GitHub 404"))
        if response["kind"] == "error":
            error_name = str(response.get("error_type") or "GitHubError")
            error_type = getattr(
                self.api_module, error_name,
                getattr(self.api_module, "GitHubError", RuntimeError),
            )
            raise error_type(response.get("message", "recorded GitHub error"))
        if response["kind"] != "result":
            raise ValueError(f"unsupported recorded GitHub response: {response['kind']!r}")
        result_type = getattr(self.api_module, "ApiResult")
        values = {
            "data": response.get("data"),
            "stale": bool(response.get("stale", False)),
            "cached_at": response.get("cached_at"),
        }
        try:
            return result_type(**values, rate_limit=response.get("rate_limit"))
        except TypeError:
            return result_type(**values)

    def _throttle(self, method: str) -> None:
        if not self.search_interval or method != "search_repositories":
            return
        wait = self._next_search_at - self._monotonic()
        if wait > 0:
            self._sleep(wait)
            self._record_diag({"event": "throttle_wait", "method": method, "seconds": round(wait, 3)})
        started = self._monotonic()
        self._record_diag({
            "event": "search_started", "method": method,
            "monotonic": round(started, 6), "interval": self.search_interval,
        })
        self._next_search_at = started + self.search_interval

    def _record_diag(self, item: dict[str, Any]) -> None:
        payload = {**item, "at": _utc_now()}
        with self._lock:
            self.payload.setdefault("capture_diagnostics", []).append(payload)

    def diagnostic_cursor(self) -> int:
        with self._lock:
            return len(self.payload.get("capture_diagnostics") or [])

    def diagnostics_since(self, cursor: int) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(item)
                for item in (self.payload.get("capture_diagnostics") or [])[cursor:]
            ]

    def _wait_for_limit(self, rate_limit: dict[str, Any] | None, attempt: int) -> None:
        retry_after = None
        reset = None
        remaining = None
        if rate_limit:
            retry_after = rate_limit.get("retry_after")
            reset = rate_limit.get("reset")
            remaining = rate_limit.get("remaining")
        if retry_after:
            delay = max(1, int(retry_after))
            reason = "retry_after"
        elif remaining == 0 and reset:
            delay = max(1, int(reset) - int(self._wall_time()))
            reason = "rate_limit_reset"
        else:
            delay = max(60, 2 ** attempt)
            reason = "secondary_limit"
        self._record_diag({
            "event": "rate_limit_wait", "reason": reason, "seconds": delay,
            "attempt": attempt, "rate_limit": rate_limit,
        })
        self._sleep(delay)

    def _capture_call(
        self, method: str, request_key: str, request_sequence: int,
        request: dict[str, Any],
        *args: Any, **kwargs: Any,
    ) -> Any:
        rate_limit_error = getattr(self.api_module, "GitHubRateLimitError", ())
        last_error: Exception | None = None
        for attempt in range(4):
            self._throttle(method)
            self._record_diag({
                "event": "api_attempt", "method": method,
                "resource": self._resource(method), "attempt": attempt + 1,
                "outcome": "started", "request_key": request_key,
                "request_sequence": request_sequence,
                "request": request,
            })
            try:
                result = getattr(self.delegate, method)(*args, **kwargs)
            except rate_limit_error as exc:
                last_error = exc
                self._record_diag({
                    "event": "api_attempt", "method": method,
                    "resource": self._resource(method), "attempt": attempt + 1,
                    "outcome": "rate_limited", "request_key": request_key,
                    "request_sequence": request_sequence,
                })
                if attempt >= 3:
                    raise
                rate_limit = dict(getattr(self.delegate, "rate_limits", {}) or {})
                resource = self._resource(method)
                self._wait_for_limit(rate_limit.get(resource) or rate_limit.get("core"), attempt)
            except Exception as exc:
                self._record_diag({
                    "event": "api_attempt", "method": method,
                    "resource": self._resource(method), "attempt": attempt + 1,
                    "outcome": "failed", "error_type": type(exc).__name__,
                    "request_key": request_key, "request_sequence": request_sequence,
                })
                raise
            else:
                self._record_diag({
                    "event": "api_attempt", "method": method,
                    "resource": self._resource(method), "attempt": attempt + 1,
                    "outcome": "succeeded", "request_key": request_key,
                    "request_sequence": request_sequence,
                })
                return result
        raise last_error or RuntimeError(f"capture failed for {method}")

    def _invoke(self, method: str, *args: Any, **kwargs: Any) -> Any:
        resource = self._resource(method)
        with self._lock:
            self.request_counts[resource] += 1
            request = _request(method, args, kwargs)
            key = _key(request)
            self._request_sequence += 1
            request_sequence = self._request_sequence
            recorded = self.payload["calls"].get(key)
            if recorded is not None and recorded.get("request") != request:
                raise ValueError(f"cassette key collision for {method}")
        if recorded is not None:
            self._record_diag({
                "event": "api_outcome", "method": method, "resource": resource,
                "outcome": "cassette_hit", "request_key": key,
                "request_sequence": request_sequence,
                "request": request,
            })
            if self.auto_save:
                self.save()
            return self._result(recorded["response"])
        if self.delegate is None:
            raise RuntimeError(f"cassette miss in replay mode: {method} {args!r} {kwargs!r}")

        def run() -> Any:
            try:
                result = self._capture_call(
                    method, key, request_sequence, request, *args, **kwargs,
                )
            except getattr(self.api_module, "GitHubNotFoundError") as exc:
                response = {"kind": "not_found", "message": _safe_error(str(exc))}
                self._record_diag({
                    "event": "api_outcome", "method": method,
                    "resource": resource, "outcome": "not_found", "request_key": key,
                    "request_sequence": request_sequence,
                    "request": request,
                })
            except Exception as exc:
                response = {
                    "kind": "error", "error_type": type(exc).__name__,
                    "message": _safe_error(str(exc)),
                }
                self._record_diag({
                    "event": "api_outcome", "method": method,
                    "resource": resource, "outcome": "error", "request_key": key,
                    "request_sequence": request_sequence,
                    "error_type": type(exc).__name__,
                    "request": request,
                })
                with self._lock:
                    self.payload["calls"][key] = {"request": request, "response": response}
                    self.rate_limits = dict(getattr(self.delegate, "rate_limits", {}))
                    if self.auto_save:
                        save_cassette(self.cassette_path, self.payload)
                raise
            else:
                response = {
                    "kind": "result", "data": result.data,
                    "stale": bool(getattr(result, "stale", False)),
                    "cached_at": getattr(result, "cached_at", None),
                    "rate_limit": getattr(result, "rate_limit", None),
                }
                self._record_diag({
                    "event": "api_outcome", "method": method,
                    "resource": resource, "outcome": "result",
                    "rate_limit": getattr(result, "rate_limit", None),
                    "request_key": key, "request_sequence": request_sequence,
                    "request": request,
                })
            with self._lock:
                self.payload["calls"][key] = {"request": request, "response": response}
                self.rate_limits = dict(getattr(self.delegate, "rate_limits", {}))
                if self.auto_save:
                    save_cassette(self.cassette_path, self.payload)
            return self._result(response)

        if self.serial_capture:
            with self._serial_lock:
                return run()
        return run()

    def save(self) -> None:
        with self._lock:
            save_cassette(self.cassette_path, self.payload)


def _method(name: str):
    def invoke(self: CassetteGitHub, *args: Any, **kwargs: Any) -> Any:
        return self._invoke(name, *args, **kwargs)
    invoke.__name__ = name
    return invoke


for _name in METHODS:
    setattr(CassetteGitHub, _name, _method(_name))
