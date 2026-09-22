"""Start a background Explorer so a finished search has a link that works.

`rank` never opens a browser: it makes sure an Explorer is answering and returns
the URL, and the host Agent shows that link. The background server stops itself
after an idle period so it cannot outlive the person who asked for it.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_IDLE_TIMEOUT = 3600.0
DISABLE_ENV = "MUSE_SHROOM_NO_EXPLORER"
READY_TIMEOUT = 6.0
# How far past the usual port to look for one this search can have.
PORT_SPAN = 10


def session_url(search_id: str | None, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    base = f"http://{host}:{port}/"
    if not search_id:
        return base
    return f"{base}#/s/{search_id}/results"


def explorer_disabled() -> bool:
    return str(os.environ.get(DISABLE_ENV, "")).strip().lower() in {"1", "true", "yes"}


def served_data_dir(*, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                    timeout: float = 0.8) -> str | None:
    """The store an Explorer on this port is serving, or None if it is not one.

    Which store matters. An Explorer started for one MUSE_SHROOM_DATA_DIR answers
    on the same port as any other, so reusing it for a search recorded elsewhere
    hands back a link to a session it cannot see.
    """
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/meta", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    served = payload.get("data_dir")
    return served if payload.get("readonly") and isinstance(served, str) and served else None


def free_port(host: str, port: int) -> bool:
    """Whether nothing holds this port, asked of the socket rather than of HTTP.

    A closed port does not always refuse: behind a local firewall the connection
    is dropped instead, and every port then looks occupied until the timeout.
    Binding answers immediately and without guessing.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def same_data_dir(served: str, wanted: str | Path) -> bool:
    try:
        return Path(served).expanduser().resolve() == Path(wanted).expanduser().resolve()
    except OSError:
        return False


def wanted_data_dir(data_dir: str | None) -> Path:
    """The store a caller means, with None standing for the default one."""
    from ..storage import default_data_dir

    return Path(data_dir) if data_dir else default_data_dir()


def is_running(*, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
               timeout: float = 0.8, data_dir: str | None = None) -> bool:
    """True when a Muse-shroom Explorer — not some other service — owns the port.

    With `data_dir`, it must also be serving that store.
    """
    served = served_data_dir(host=host, port=port, timeout=timeout)
    if served is None:
        return False
    return data_dir is None or same_data_dir(served, data_dir)


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

    # Walk forward from the usual port. An Explorer already serving this store is
    # reused; one serving another store, or anything else holding the port, is left
    # alone and this search gets its own on the next free port.
    wanted = wanted_data_dir(data_dir)
    free = None
    for candidate in range(port, port + PORT_SPAN):
        if free_port(host, candidate):
            free = candidate
            break
        served = served_data_dir(host=host, port=candidate)
        if served is not None and same_data_dir(served, wanted):
            return {"url": session_url(search_id, host=host, port=candidate),
                    "running": True, "started": False, "reason": "already_running"}
    if free is None:
        return {"url": url, "running": False, "started": False, "reason": "no_free_port"}

    url = session_url(search_id, host=host, port=free)
    try:
        _spawn(data_dir=data_dir, host=host, port=free, idle_timeout=idle_timeout)
    except (OSError, ValueError) as exc:
        return {"url": url, "running": False, "started": False, "reason": f"spawn_failed: {exc}"}
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        if is_running(host=host, port=free, timeout=0.5, data_dir=str(wanted)):
            return {"url": url, "running": True, "started": True, "reason": "started"}
        time.sleep(0.25)
    # The port may belong to something else, or startup was simply slow. Either
    # way the URL is still the right thing to hand back; say it is not confirmed.
    return {"url": url, "running": False, "started": True, "reason": "not_ready"}
