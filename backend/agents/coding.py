"""Coding Agent stub. Real impl lands in phase 1 task 3."""
from __future__ import annotations

from graph.state import SessionState


def coding_node(state: SessionState) -> dict:
    # phase 1: no edits. phase 1 task 3 fills this in.
    return {
        "edits": state.get("edits", {}),
        "status": "coding_skipped_phase1",
    }
