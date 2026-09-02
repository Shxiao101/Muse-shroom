from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


METHODS = (
    "search_repositories", "repository", "readme", "latest_release",
    "search_code", "forks", "owner_repositories",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request(method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    return {"method": method, "args": list(args), "kwargs": kwargs}


def _key(request: dict[str, Any]) -> str:
    encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
                 search_interval: float = 0.0, serial_capture: bool | None = None) -> None:
        self.api_module = api_module
        self.cassette_path = cassette_path
        self.delegate = delegate
        self.search_interval = max(0.0, search_interval)
        self.serial_capture = delegate is not None if serial_capture is None else serial_capture
        self.payload = load_cassette(cassette_path)
        if delegate is not None and not self.payload.get("captured_at"):
            self.payload["captured_at"] = _utc_now()
        self.payload.setdefault("capture_diagnostics", [])
        self.request_counts = {"core": 0, "search": 0, "code_search": 0}
        self.rate_limits: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._serial_lock = threading.Lock()
        self._next_search_at = 0.0

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
        if not self.search_interval:
            return
        if method != "search_repositories" and not self.serial_capture:
            return
        wait = self._next_search_at - time.monotonic()
        if wait > 0:
            time.sleep(wait)
            self._record_diag({"event": "throttle_wait", "method": method, "seconds": round(wait, 3)})
        interval = self.search_interval if method == "search_repositories" else 0.0
        self._next_search_at = time.monotonic() + interval

    def _record_diag(self, item: dict[str, Any]) -> None:
        payload = {**item, "at": _utc_now()}
        self.payload.setdefault("capture_diagnostics", []).append(payload)

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
            delay = max(1, int(reset) - int(time.time()))
            reason = "rate_limit_reset"
        else:
            delay = max(60, 2 ** attempt)
            reason = "secondary_limit"
        self._record_diag({
            "event": "rate_limit_wait", "reason": reason, "seconds": delay,
            "attempt": attempt, "rate_limit": rate_limit,
        })
        time.sleep(delay)

    def _capture_call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        rate_limit_error = getattr(self.api_module, "GitHubRateLimitError", RuntimeError)
        last_error: Exception | None = None
        for attempt in range(4):
            self._throttle(method)
            try:
                return getattr(self.delegate, method)(*args, **kwargs)
            except rate_limit_error as exc:
                last_error = exc
                if attempt >= 3:
                    raise
                rate_limit = dict(getattr(self.delegate, "rate_limits", {}) or {})
                resource = self._resource(method)
                self._wait_for_limit(rate_limit.get(resource) or rate_limit.get("core"), attempt)
        raise last_error or RuntimeError(f"capture failed for {method}")

    def _invoke(self, method: str, *args: Any, **kwargs: Any) -> Any:
        resource = self._resource(method)
        with self._lock:
            self.request_counts[resource] += 1
            request = _request(method, args, kwargs)
            key = _key(request)
            recorded = self.payload["calls"].get(key)
            if recorded is not None and recorded.get("request") != request:
                raise ValueError(f"cassette key collision for {method}")
        if recorded is not None:
            return self._result(recorded["response"])
        if self.delegate is None:
            raise RuntimeError(f"cassette miss in replay mode: {method} {args!r} {kwargs!r}")

        def run() -> Any:
            try:
                result = self._capture_call(method, *args, **kwargs)
            except getattr(self.api_module, "GitHubNotFoundError") as exc:
                response = {"kind": "not_found", "message": str(exc)}
            else:
                response = {
                    "kind": "result", "data": result.data,
                    "stale": bool(getattr(result, "stale", False)),
                    "cached_at": getattr(result, "cached_at", None),
                    "rate_limit": getattr(result, "rate_limit", None),
                }
            with self._lock:
                self.payload["calls"][key] = {"request": request, "response": response}
                self.rate_limits = dict(getattr(self.delegate, "rate_limits", {}))
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
