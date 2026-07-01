"""QA Agent stub. Real impl lands in phase 3."""
from __future__ import annotations

from graph.state import SessionState


def qa_node(state: SessionState) -> dict:
    return {
        "qa_result": {"passed": True, "skipped": True},
        "status": "qa_skipped_phase1",
    }
