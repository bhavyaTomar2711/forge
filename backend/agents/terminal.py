"""Terminal Agent stub. Real impl lands in phase 2."""
from __future__ import annotations

from graph.state import SessionState


def terminal_node(state: SessionState) -> dict:
    return {
        "build_output": "",
        "build_passed": True,  # skip verification until phase 2
        "status": "terminal_skipped_phase1",
    }
