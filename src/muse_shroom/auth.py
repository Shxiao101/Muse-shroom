from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

import keyring
import keyring.errors

from . import __version__


SERVICE_NAME = "Muse-shroom"
ACCOUNT_NAME = "github.com"
TOKEN_URL = (
    "https://github.com/settings/personal-access-tokens/new"
    "?name=Muse-shroom&description=Public+repository+discovery&expires_in=90"
)


class AuthError(RuntimeError):
    """Raised when a credential cannot be read, validated, or stored safely."""


@dataclass(frozen=True, slots=True)
class Credential:
    token: str
    source: str


def _clean(token: str | None) -> str | None:
    value = token.strip() if token else ""
    return value or None


def resolve_token(explicit: str | None = None) -> Credential | None:
    """Resolve a token without copying it into application storage or logs."""
    if token := _clean(explicit):
        return Credential(token, "explicit")
    if token := _clean(os.environ.get("GITHUB_TOKEN")):
        return Credential(token, "environment")
    try:
        token = _clean(keyring.get_password(SERVICE_NAME, ACCOUNT_NAME))
    except keyring.errors.KeyringError as exc:
        raise AuthError(f"system credential store is unavailable: {exc}") from exc
    return Credential(token, "keyring") if token else None


def save_token(token: str) -> None:
    cleaned = _clean(token)
    if not cleaned:
        raise AuthError("token cannot be empty")
    try:
        keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, cleaned)
    except keyring.errors.KeyringError as exc:
        raise AuthError(f"could not save token in the system credential store: {exc}") from exc


def delete_saved_token() -> bool:
    try:
        if keyring.get_password(SERVICE_NAME, ACCOUNT_NAME) is None:
            return False
        keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
        return True
    except keyring.errors.PasswordDeleteError:
        return False
    except keyring.errors.KeyringError as exc:
        raise AuthError(f"could not update the system credential store: {exc}") from exc


def validate_token(token: str, *, timeout: float = 15.0) -> dict[str, Any]:
    cleaned = _clean(token)
    if not cleaned:
        raise AuthError("token cannot be empty")
    request = urllib.request.Request("https://api.github.com/user", headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {cleaned}",
        "X-GitHub-Api-Version": "2026-03-10",
        "User-Agent": f"Muse-shroom/{__version__}",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise AuthError("GitHub rejected the token; create a new fine-grained token and try again") from exc
        raise AuthError(f"GitHub could not validate the token (HTTP {exc.code})") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        reason = exc.reason if hasattr(exc, "reason") else exc
        raise AuthError(f"could not reach GitHub to validate the token: {reason}") from exc
    login = str(payload.get("login", "")).strip()
    if not login:
        raise AuthError("GitHub validation response did not include an account login")
    return {"login": login}
