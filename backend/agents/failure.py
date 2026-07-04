"""Compose the user-facing failure message when the pipeline gives up.

Per FORGE_BRAIN.md Hard Rules:
  "On exceeding [the retry cap], the agent must explicitly tell the user
   'I couldn't fix this after N attempts, here's what I tried and where
   it's stuck' -- never loop silently or claim success without QA passing."

This is a pure function (no LLM, no I/O) so it's deterministic + testable.
Phase 2 part 2: wired into the graph as a final node on retry-exhausted
paths. Phase 3 will append QA-specific diagnostics on top.
"""
from __future__ import annotations

from graph.state import SessionState


def build_failure_message(state: SessionState) -> str:
    task = state.get("task", "(no task)")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 3)
    edits = state.get("edits") or {}
    diffs = state.get("diff_summary") or []
    build_output = state.get("build_output") or ""
    build_passed = state.get("build_passed", False)
    qa_result = state.get("qa_result") or {}
    qa_passed = bool(qa_result.get("passed"))

    lines: list[str] = []
    lines.append(f"I couldn't finish this task after {retry_count} attempt"
                 f"{'s' if retry_count != 1 else ''} (max {max_retries}).")
    lines.append("")
    lines.append(f"Task: {task}")
    lines.append("")
    lines.append("What I tried:")

    if not diffs:
        lines.append("  - No file edits were applied.")
    else:
        # dedupe by path (a single file may be edited across retries)
        seen: dict[str, dict] = {}
        for d in diffs:
            path = d.get("path", "<unknown>")
            if path in seen:
                # bump the attempt counter on the merged entry
                seen[path]["attempts"] = seen[path].get("attempts", 1) + 1
            else:
                entry = dict(d)
                entry["attempts"] = 1
                seen[path] = entry
        for path, d in seen.items():
            attempts = d.get("attempts", 1)
            if d.get("error"):
                lines.append(f"  - {path}: ERROR ({d['error']})")
            elif d.get("created"):
                lines.append(f"  - {path}: created (+{d.get('added', 0)} lines)")
            else:
                lines.append(
                    f"  - {path}: edited +{d.get('added', 0)} / -{d.get('removed', 0)} lines"
                )
            if attempts > 1:
                lines.append(f"      (edited across {attempts} retry attempts)")

    lines.append("")
    lines.append("Where it's stuck:")
    if not build_passed:
        lines.append("  - The build did not pass inside the sandbox container.")
        lines.append("  - Last build output (tail):")
        lines.extend(f"      {ln}" for ln in build_output.splitlines()[-15:])
    if not qa_passed and qa_result:
        lines.append("  - QA reported failure:")
        for k, v in qa_result.items():
            if k == "passed":
                continue
            lines.append(f"      {k}: {v}")

    if build_passed and qa_passed:
        # unusual: the failure message shouldn't really be reached if both
        # checks passed. include a note in case it ever is.
        lines.append("  - Build and QA both reported pass, but retry budget was exhausted anyway.")

    lines.append("")
    lines.append("Next step: review the files I edited and the build output above,")
    lines.append("then either refine the task description or push the changes")
    lines.append("manually if they look close.")
    return "\n".join(lines)


def failure_summary_node(state: SessionState) -> dict:
    """LangGraph node. Stores the user-facing failure message on state.

    Writes: state.failure_message (new key), state.status = 'failed'
    """
    msg = build_failure_message(state)
    return {
        "failure_message": msg,
        "status": "failed",
    }
