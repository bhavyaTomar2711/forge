"""Shared filesystem locations. Kept dependency-free (no graph/agents imports)
so both session_store.py and agents/*.py can use it without circular imports.
"""
from __future__ import annotations

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
SESSIONS_DIR = BACKEND_DIR / "sessions"


def session_dir(session_id: str) -> Path:
    """Per-session scratch dir for attachments/screenshots. Created on demand."""
    d = SESSIONS_DIR / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d
