"""Shared state shape for the LangGraph agent pipeline.

Single TypedDict passed through every node. State is explicit, never hidden
in prompts. Mirrors FORGE_BRAIN.md Technical Implementation Details.
"""
from __future__ import annotations

from typing import Any, TypedDict


class SessionState(TypedDict, total=False):
    # --- session identity / context ---
    session_id: str               # unique id, used by docker manager to key the container
    repo_path: str                # absolute path to the cloned repo on disk
    task: str                     # current user instruction (latest message)
    attachments: list[str]        # file paths/URLs for uploaded images, if any

    # --- conversation ---
    conversation: list[dict]      # full message history; each msg may include attachments

    # --- agent outputs (filled as the graph runs) ---
    plan: list[str]               # Planner Agent output: ordered step list
    relevant_files: dict[str, str]  # filename -> file content, from Repository Agent
    edits: dict[str, str]         # filename -> new full content, from Coding Agent
    diff_summary: list[dict]      # per-file diff metadata for UI display (filename, added, removed)

    # --- verification ---
    build_output: str             # last Terminal Agent run stdout+stderr
    build_passed: bool
    qa_result: dict[str, Any]     # pass/fail + details from Playwright checks

    # --- control ---
    retry_count: int
    max_retries: int              # hard cap, default 3 per brain hard rules
    status: str                   # planning | editing | building | testing | awaiting_approval | failed


# default retry cap per FORGE_BRAIN.md hard rules
DEFAULT_MAX_RETRIES = 3
