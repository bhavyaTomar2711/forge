"""GitHub OAuth Device Flow login.

Device flow needs no running web server or redirect handling -- it's the
right fit for a backend/CLI-only app like Forge (no frontend server exists
until phase 6). Per FORGE_BRAIN.md phase 5 "GitHub OAuth login".

Flow:
  1. request_device_code() -- ask GitHub for a user_code + verification_uri
  2. show the user_code, they open the URL in a browser and approve
  3. poll_for_token() until they approve (or it expires/is denied)
  4. token is persisted to backend/.github_token (gitignored)
"""
from __future__ import annotations

import os
import time

import requests
from dotenv import load_dotenv

from paths import BACKEND_DIR

load_dotenv(BACKEND_DIR / ".env")

CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
SCOPE = "repo"
_TOKEN_FILE = BACKEND_DIR / ".github_token"

_DEVICE_CODE_URL = "https://github.com/login/device/code"
_TOKEN_URL = "https://github.com/login/oauth/access_token"


def _require_client_id() -> str:
    if not CLIENT_ID:
        raise RuntimeError("GITHUB_CLIENT_ID not set. Put it in backend/.env")
    return CLIENT_ID


def request_device_code() -> dict:
    resp = requests.post(
        _DEVICE_CODE_URL,
        data={"client_id": _require_client_id(), "scope": SCOPE},
        headers={"Accept": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if "device_code" not in data:
        raise RuntimeError(f"device code request failed: {data}")
    return data


def poll_for_token(device_code: str) -> str | None:
    """One poll attempt. Returns the token, or None if still pending."""
    resp = requests.post(
        _TOKEN_URL,
        data={
            "client_id": _require_client_id(),
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
        headers={"Accept": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if "access_token" in data:
        return data["access_token"]
    error = data.get("error")
    if error in ("authorization_pending", "slow_down"):
        return None
    raise RuntimeError(f"device flow failed: {data}")


def login(*, on_prompt=print) -> str:
    """Blocking login. Prints the code + URL, polls until approved, persists
    and returns the access token."""
    device = request_device_code()
    on_prompt(f"Go to {device['verification_uri']} and enter code: {device['user_code']}")

    interval = device.get("interval", 5)
    deadline = time.time() + device.get("expires_in", 900)
    while time.time() < deadline:
        time.sleep(interval)
        token = poll_for_token(device["device_code"])
        if token:
            save_token(token)
            return token
    raise TimeoutError("device flow expired before the user authorized")


def save_token(token: str) -> None:
    _TOKEN_FILE.write_text(token, encoding="utf-8")


def load_token() -> str | None:
    """Returns the token in priority order: persisted .github_token file,
    then GITHUB_TOKEN env var (PAT) as a fallback. Returns None if neither."""
    if _TOKEN_FILE.exists():
        return _TOKEN_FILE.read_text(encoding="utf-8").strip()
    pat = os.getenv("GITHUB_TOKEN")
    if pat:
        return pat.strip()
    return None
