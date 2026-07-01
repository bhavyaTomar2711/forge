"""LangGraph wiring for the forge pipeline.

Phase 1: planner -> repository. Stubs for coding/terminal/qa/git return a
no-op state update so the graph compiles and can be invoked end-to-end.
Real implementations land in later tasks/phases.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.coding import coding_node
from agents.git import git_node
from agents.planner import planner_node
from agents.qa import qa_node
from agents.repository import repository_node
from agents.terminal import terminal_node
from graph.state import DEFAULT_MAX_RETRIES, SessionState


def _route_after_terminal(state: SessionState) -> str:
    """Build passed -> qa. Build failed + retries left -> coding. Otherwise -> failed."""
    if state.get("build_passed"):
        return "qa"
    if state.get("retry_count", 0) < state.get("max_retries", DEFAULT_MAX_RETRIES):
        return "coding"
    return END


def _route_after_qa(state: SessionState) -> str:
    """QA pass -> end (await user approval). QA fail + retries left -> coding. Else -> failed."""
    qa = state.get("qa_result") or {}
    if qa.get("passed"):
        return END
    if state.get("retry_count", 0) < state.get("max_retries", DEFAULT_MAX_RETRIES):
        return "coding"
    return END


def build_graph() -> StateGraph:
    """Construct the phase-1-compilable graph. Conditional edges for retries
    are stubbed (always END) until phases 2/3 land.
    """
    g = StateGraph(SessionState)

    g.add_node("planner", planner_node)
    g.add_node("repository", repository_node)
    g.add_node("coding", coding_node)
    g.add_node("terminal", terminal_node)
    g.add_node("qa", qa_node)
    g.add_node("git", git_node)

    g.add_edge(START, "planner")
    g.add_edge("planner", "repository")
    g.add_edge("repository", "coding")
    g.add_edge("coding", "terminal")
    g.add_edge("terminal", "qa")
    g.add_edge("qa", END)
    g.add_edge("git", END)

    # conditional edges (rebuilt each run; safe to register once for phase 1)
    g.add_conditional_edges("terminal", _route_after_terminal,
                            {"qa": "qa", "coding": "coding", END: END})
    g.add_conditional_edges("qa", _route_after_qa,
                            {"coding": "coding", END: END})

    return g.compile()


# pre-compiled singleton for callers that just want to invoke()
graph = build_graph()
