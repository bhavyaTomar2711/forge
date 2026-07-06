"""Planner Agent.

One job: take the user task, return an ordered list of concrete steps.

Strategy:
  - try LLM (Groq) for a real, task-specific plan
  - on any failure (no key, network, parse error) fall back to the template
    heuristics so the graph never stalls mid-pipeline

Templates remain as the safety net, NOT the primary path.
"""
from __future__ import annotations

import re

from graph.state import SessionState
from llm import chat_text, chat_vision

# hard-coded templates for common task patterns -- used only as fallback
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
    return [
        f"Investigate: identify the files relevant to: {task}",
        "Plan: enumerate the changes required to satisfy the request.",
        "Implement: apply the changes in the smallest set of files possible.",
        "Self-review: re-read each edited file to confirm it makes sense in context.",
    ]


# ---------- LLM plan generation ----------

_SYSTEM_PROMPT = """\
You are the Planner agent inside Forge, an autonomous AI software engineer.
Given a natural-language feature request, break it into a small ordered list
of CONCRETE engineering steps a coding agent can execute one by one.

Rules:
- Output ONLY a JSON array of strings, nothing else. No prose, no markdown fences.
- 3-7 steps. Each step names the file, component, or concrete action.
- Steps should be ordered: investigation -> design -> implementation -> verification.
- Never reference agents, never mention "the user". Write as engineering tickets.
- Do not include steps that aren't needed (e.g. "run tests" if the task is trivial).
- Keep the entire JSON parseable in one call.
"""

_VISION_SYSTEM_PROMPT = """\
You are the Planner agent inside Forge, an autonomous AI software engineer.
You are given a natural-language task AND one or more attached images (a bug
screenshot or a design reference). Treat the image(s) as the primary evidence
for what's broken or what's wanted -- read text/layout/colors directly off
them, don't guess.

Rules:
- Output ONLY a JSON array of strings, nothing else. No prose, no markdown fences.
- 3-7 steps. Each step names the file, component, or concrete action.
- Steps should be ordered: investigation -> design -> implementation -> verification.
- Reference specific visual details from the image(s) where relevant (e.g.
  "match the button color/spacing shown in the screenshot").
- Never reference agents, never mention "the user". Write as engineering tickets.
- Keep the entire JSON parseable in one call.
"""


def _parse_steps(raw: str) -> list[str] | None:
    """Tolerate ```json fences, leading/trailing junk. Return list or None."""
    if not raw:
        return None
    s = raw.strip()
    # strip ```json / ``` fences if present
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    # find the first [ and last ]
    start = s.find("[")
    end = s.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    import json
    try:
        data = json.loads(s[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        return None
    if not data:
        return None
    return [x.strip() for x in data if x.strip()]


def plan_steps(task: str, attachments: list[str] | None = None) -> list[str]:
    """LLM-first, template fallback. Pure function. If attachments are
    present, uses the vision model so images inform the plan directly."""
    attachments = attachments or []
    if not task.strip() and not attachments:
        return []
    try:
        if attachments:
            raw = chat_vision(_VISION_SYSTEM_PROMPT, f"Task: {task}", attachments, max_tokens=512)
        else:
            raw = chat_text(_SYSTEM_PROMPT, f"Task: {task}", max_tokens=512)
        parsed = _parse_steps(raw)
        if parsed:
            return parsed
    except Exception:
        # network / auth / rate-limit / parse -- fall through to templates
        pass
    return _template_steps(task)


def planner_node(state: SessionState) -> dict:
    """LangGraph node. Reads task (+ attachments), writes plan + bumps status."""
    task = state.get("task", "")
    attachments = state.get("attachments") or []
    plan = plan_steps(task, attachments)
    return {
        "plan": plan,
        "status": "planning_done",
    }
