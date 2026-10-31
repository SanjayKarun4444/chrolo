"""A tiny REAL LangGraph graph; no external LLMs or network calls.

Install with pip install -e '.[langgraph]' to run this example.
The flaw is intentional: stale cached issue ID overrides fresh lookup.
"""
from __future__ import annotations
from typing import Any, TypedDict


class State(TypedDict, total=False):
    events: list[dict[str, Any]]
    expected_issue_id: int
    selected_issue_id: int | None


def build_graph():
    from langgraph.graph import StateGraph, START, END

    def search(state: State) -> dict[str, Any]:
        selected = None
        for event in state["events"]:
            if event["kind"] == "issue_search":
                selected = event["payload"]["correct_issue_id"]
        return {"selected_issue_id": selected}

    def choose(state: State) -> dict[str, Any]:
        selected = state["selected_issue_id"]
        for event in state["events"]:
            if event["kind"] == "cached_selection":
                selected = event["payload"]["issue_id"]  # deliberate bug
        return {"selected_issue_id": selected}

    graph = StateGraph(State)
    graph.add_node("search", search)
    graph.add_node("choose", choose)
    graph.add_edge(START, "search")
    graph.add_edge("search", "choose")
    graph.add_edge("choose", END)
    return graph.compile()


def wrong_issue(result: dict[str, Any]) -> bool:
    return result["selected_issue_id"] == 99 and result["expected_issue_id"] == 42
