"""Attachment storage for image inputs (bug screenshots, design references).

No HTTP upload endpoint yet -- that's a frontend/API concern for a later
phase. For now, whatever hands us the raw bytes (a future FastAPI route, a
test script) calls save_attachment() and gets back a path to put straight
into SessionState.attachments, which the Planner Agent's vision call reads.
"""
from __future__ import annotations

import re

from paths import session_dir

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str) -> str:
    name = _SAFE_NAME_RE.sub("_", name.strip()) or "attachment"
    return name


def save_attachment(session_id: str, filename: str, data: bytes) -> str:
    """Writes the image bytes under sessions/<session_id>/attachments/ and
    returns the absolute path as a string."""
    d = session_dir(session_id) / "attachments"
    d.mkdir(parents=True, exist_ok=True)
    target = d / _safe_filename(filename)
    target.write_bytes(data)
    return str(target)


def list_attachments(session_id: str) -> list[str]:
    d = session_dir(session_id) / "attachments"
    if not d.exists():
        return []
    return [str(p) for p in sorted(d.iterdir()) if p.is_file()]
