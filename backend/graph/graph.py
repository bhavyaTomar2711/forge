"""LangGraph wiring for the forge pipeline."""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.coding import coding_node
from agents.git import git_node
from agents.planner import planner_node
from agents.qa import qa_node
from agents.repository import repository_node
from agents.terminal import terminal_node
from graph.state import DEFAULT_MAX_RETRIES, SessionState


def _bump_retry(state: SessionState) -> dict:
    """Pass-through node. Increments retry_count so the cap is enforced.
    Doesn't touch `status` (downstream nodes own that) to avoid langgraph's
    concurrent-update error.
    """
    return {"retry_count": state.get("retry_count", 0) + 1}


def _route_after_terminal(state: SessionState) -> str:
    if state.get("build_passed"):
        return "qa"
    if state.get("retry_count", 0) < state.get("max_retries", DEFAULT_MAX_RETRIES):
        return "retry"
    return END


def _route_after_qa(state: SessionState) -> str:
    qa = state.get("qa_result") or {}
    if qa.get("passed"):
        return END
    if state.get("retry_count", 0) < state.get("max_retries", DEFAULT_MAX_RETRIES):
        return "retry"
    return END


def build_graph() -> StateGraph:
    g = StateGraph(SessionState)

    g.add_node("planner", planner_node)
    g.add_node("repository", repository_node)
    g.add_node("coding", coding_node)
    g.add_node("terminal", terminal_node)
    g.add_node("qa", qa_node)
    g.add_node("git", git_node)
    g.add_node("retry", _bump_retry)

    g.add_edge(START, "planner")
    g.add_edge("planner", "repository")
    g.add_edge("repository", "coding")
    g.add_edge("coding", "terminal")
    g.add_edge("terminal", "qa")
    g.add_edge("qa", END)
    g.add_edge("git", END)
    g.add_edge("retry", "coding")

    g.add_conditional_edges(
        "terminal", _route_after_terminal,
        {"qa": "qa", "retry": "retry", END: END},
    )
    g.add_conditional_edges(
        "qa", _route_after_qa,
        {"retry": "retry", END: END},
    )

    return g.compile()


graph = build_graph()
