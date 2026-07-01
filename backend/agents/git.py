"""Git Agent stub. Real impl lands in phase 5."""
from __future__ import annotations

from graph.state import SessionState


def git_node(state: SessionState) -> dict:
    return {
        "status": "git_skipped_phase1",
    }
