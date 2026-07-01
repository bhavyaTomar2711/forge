"""Planner Agent.

One job: take the user task, return an ordered list of concrete steps.

Phase 1 = stub. Real LLM call comes in phase 1 task 3. For now we generate
a deterministic skeleton so the graph can be wired and tested end-to-end.
"""
from __future__ import annotations

import re

from graph.state import SessionState

# hard-coded templates for common task patterns so phase 1 has signal
_PATTERNS: list[tuple[re.Pattern[str], list[str]]] = [
    (
        re.compile(r"\b(add|create|implement)\b.*\b(dark\s*mode|dark\s*theme|theme\s*toggle)\b", re.I),
        [
            "Identify the root layout file and global styles entry point.",
            "Add a theme provider/context that reads/writes a 'theme' cookie or localStorage key.",
            "Create a toggle component and mount it in the navbar (or specified location).",
            "Add Tailwind dark-mode config so `dark:` variants activate under the toggle.",
            "Verify the toggle persists across page reloads.",
        ],
    ),
    (
        re.compile(r"\b(add|create)\b.*\b(loading|spinner|skeleton)\b", re.I),
        [
            "Find the target page/component flagged in the task.",
            "Create a reusable Loading/Skeleton component.",
            "Wire it into the target page as a Suspense fallback or async boundary.",
        ],
    ),
    (
        re.compile(r"\b(fix|repair)\b.*\b(bug|error|broken)\b", re.I),
        [
            "Locate the file(s) implicated by the bug description.",
            "Read the current implementation and identify the defect.",
            "Apply a minimal fix scoped to the broken behavior.",
            "Re-read the file to confirm the patch reads correctly.",
        ],
    ),
]


def _template_steps(task: str) -> list[str]:
    for pat, steps in _PATTERNS:
        if pat.search(task):
            return steps
    # default generic skeleton
    return [
        f"Investigate: identify the files relevant to: {task}",
        "Plan: enumerate the changes required to satisfy the request.",
        "Implement: apply the changes in the smallest set of files possible.",
        "Self-review: re-read each edited file to confirm it makes sense in context.",
    ]


def plan_steps(task: str) -> list[str]:
    """Public API. Returns ordered step strings. Pure function."""
    return _template_steps(task)


def planner_node(state: SessionState) -> dict:
    """LangGraph node. Reads task, writes plan + bumps status."""
    task = state.get("task", "")
    plan = plan_steps(task)
    return {
        "plan": plan,
        "status": "planning_done",
    }
