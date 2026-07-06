"""Session persistence + multi-turn runner.

Phase 4 part 1: a session is no longer a one-shot script run. State (task
history, current plan/edits/build status) is saved to disk as JSON keyed by
session_id, so a follow-up instruction can pick up exactly where the last
turn left off instead of re-cloning/re-planning from zero.

The repo on disk (repo_path) IS the persisted "current code state" -- each
turn's edits are written straight to those files, so the Repository Agent
naturally sees prior turns' changes on the next run. We only need to persist
the SessionState dict itself (conversation history, last plan/edits/status).

Docker containers are reused across turns too: docker.manager keys sessions
by session_id in-memory, so as long as the same process is running, the
Terminal Agent finds the existing container (and cached node_modules)
instead of starting a fresh one.
"""
from __future__ import annotations

import json

from graph.graph import graph
from graph.state import DEFAULT_MAX_RETRIES, SessionState
from paths import SESSIONS_DIR


def _path(session_id: str):
    return SESSIONS_DIR / f"{session_id}.json"


def save(state: SessionState) -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    _path(state["session_id"]).write_text(
        json.dumps(dict(state), indent=2), encoding="utf-8",
    )


def load(session_id: str) -> SessionState | None:
    p = _path(session_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


# ---------- turn runners ----------

def _reset_turn_fields(state: dict) -> dict:
    """Clear the fields that describe THIS turn's outcome so graph routing
    (which checks build_passed / qa_result / retry_count) re-evaluates fresh,
    while keeping conversation/edits/diff_summary history intact."""
    state["retry_count"] = 0
    state["build_passed"] = False
    state["build_output"] = ""
    state["qa_result"] = {}
    state["failure_message"] = ""
    state["status"] = "starting"
    return state


def start_session(session_id: str, repo_path: str, task: str, *,
                  attachments: list[str] | None = None,
                  max_retries: int = DEFAULT_MAX_RETRIES) -> SessionState:
    """First turn: fresh SessionState for a repo that's already cloned."""
    initial: SessionState = {
        "session_id": session_id,
        "repo_path": repo_path,
        "task": task,
        "attachments": attachments or [],
        "conversation": [{"role": "user", "content": task, "attachments": attachments or []}],
        "plan": [],
        "relevant_files": {},
        "edits": {},
        "diff_summary": [],
        "build_output": "",
        "build_passed": False,
        "qa_result": {},
        "failure_message": "",
        "retry_count": 0,
        "max_retries": max_retries,
        "status": "starting",
    }
    final = graph.invoke(initial)
    save(final)
    return final


def continue_session(session_id: str, task: str, *,
                     attachments: list[str] | None = None) -> SessionState:
    """Follow-up turn: load prior state, apply the new instruction (and any
    new attachments) on top of the same repo/container, run the pipeline
    again."""
    prior = load(session_id)
    if prior is None:
        raise KeyError(f"no saved session: {session_id}")

    prior["task"] = task
    prior["attachments"] = attachments or []
    prior["conversation"] = list(prior.get("conversation") or []) + [
        {"role": "user", "content": task, "attachments": attachments or []},
    ]
    prior = _reset_turn_fields(prior)

    final = graph.invoke(prior)
    save(final)
    return final
