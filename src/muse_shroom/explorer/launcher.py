"""Start a background Explorer so a finished search has a link that works.

`rank` never opens a browser: it makes sure an Explorer is answering and returns
the URL, and the host Agent shows that link. The background server stops itself
after an idle period so it cannot outlive the person who asked for it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_IDLE_TIMEOUT = 3600.0
DISABLE_ENV = "MUSE_SHROOM_NO_EXPLORER"
READY_TIMEOUT = 6.0


def session_url(search_id: str | None, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    base = f"http://{host}:{port}/"
    if not search_id:
        return base
    return f"{base}#/s/{search_id}/results"


def explorer_disabled() -> bool:
    return str(os.environ.get(DISABLE_ENV, "")).strip().lower() in {"1", "true", "yes"}


def is_running(*, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
               timeout: float = 0.8) -> bool:
    """True when a Muse-shroom Explorer — not some other service — owns the port."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/meta", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return False
    return bool(payload.get("readonly")) and "data_dir" in payload


def _spawn(*, data_dir: str | None, host: str, port: int, idle_timeout: float) -> None:
    command = [
        sys.executable, "-m", "muse_shroom",
        *(["--data-dir", data_dir] if data_dir else []),
        "explorer", "--no-browser",
        "--host", host, "--port", str(port),
        "--idle-timeout", str(int(idle_timeout)),
    ]
    kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        # Detach so the Explorer survives the rank process and never inherits its console.
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)


def ensure_explorer(search_id: str | None = None, *, data_dir: str | None = None,
                    host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                    idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
                    enabled: bool = True) -> dict[str, Any]:
    """Return a usable Explorer URL, starting a background server only if needed."""
    url = session_url(search_id, host=host, port=port)
    if not enabled or explorer_disabled():
        return {"url": url, "running": False, "started": False, "reason": "disabled"}
    if is_running(host=host, port=port):
        return {"url": url, "running": True, "started": False, "reason": "already_running"}
    try:
        _spawn(data_dir=data_dir, host=host, port=port, idle_timeout=idle_timeout)
    except (OSError, ValueError) as exc:
        return {"url": url, "running": False, "started": False, "reason": f"spawn_failed: {exc}"}
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        if is_running(host=host, port=port, timeout=0.5):
            return {"url": url, "running": True, "started": True, "reason": "started"}
        time.sleep(0.25)
    # The port may belong to something else, or startup was simply slow. Either
    # way the URL is still the right thing to hand back; say it is not confirmed.
    return {"url": url, "running": False, "started": True, "reason": "not_ready"}
