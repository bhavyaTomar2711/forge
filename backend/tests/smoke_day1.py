"""Phase 1 day-1 smoke test.

Runs the graph end-to-end on the current forge repo (c:/Users/morga/Desktop/forge)
with a dark-mode task. Prints planner output, relevant files surfaced by the
Repository Agent, and final status. No LLM call yet -- planner is template-based.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# add backend/ to sys.path so `from agents...` works without install
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from graph.graph import graph  # noqa: E402
from graph.state import DEFAULT_MAX_RETRIES, SessionState  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent  # forge/ root
TASK = "Add dark mode with a toggle in the navbar"


def main() -> int:
    initial: SessionState = {
        "repo_path": str(REPO),
        "task": TASK,
        "attachments": [],
        "conversation": [{"role": "user", "content": TASK}],
        "plan": [],
        "relevant_files": {},
        "edits": {},
        "build_output": "",
        "build_passed": False,
        "qa_result": {},
        "retry_count": 0,
        "max_retries": DEFAULT_MAX_RETRIES,
        "status": "starting",
    }

    print(f"== forge smoke test ==")
    print(f"repo: {REPO}")
    print(f"task: {TASK}\n")

    final = graph.invoke(initial)

    print("== planner output ==")
    for i, step in enumerate(final.get("plan", []), 1):
        print(f"  {i}. {step}")

    print("\n== repository agent: relevant files ==")
    relevant = final.get("relevant_files", {})
    print(f"  {len(relevant)} files surfaced:")
    for path in relevant.keys():
        print(f"    - {path}")

    print(f"\n== final status: {final.get('status')} ==")
    print(f"build_passed: {final.get('build_passed')}")
    print(f"qa_result: {final.get('qa_result')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
