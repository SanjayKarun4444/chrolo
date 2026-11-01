"""Synthetic LangGraph demonstrating deterministic boundary recording/replay.

The model and tool are local fakes. Replay must not execute either function.
"""
from __future__ import annotations

from typing import Any, TypedDict

LIVE_CALLS = {"model": 0, "tool": 0}


class State(TypedDict, total=False):
    events: list[dict[str, Any]]
    expected_issue_id: int
    fresh_issue_id: int
    selected_issue_id: int | None


def reset_live_calls() -> None:
    LIVE_CALLS["model"] = 0
    LIVE_CALLS["tool"] = 0


def _fake_model(query: str) -> dict[str, Any]:
    LIVE_CALLS["model"] += 1
    return {"issue_id": 42 if query == "issue:42" else None}


def _fake_tool(issue_id: int | None) -> dict[str, Any]:
    LIVE_CALLS["tool"] += 1
    return {"issue_id": issue_id}


def build_graph(boundaries):
    from langgraph.graph import StateGraph, START, END

    def search(state: State) -> dict[str, Any]:
        issue_id = next(
            (event["payload"].get("correct_issue_id") for event in state["events"] if event["kind"] == "issue_search"),
            None,
        )
        query = f"issue:{issue_id}" if issue_id is not None else ""
        response = boundaries.call(
            "model", "demo.issue_search", {"query": query},
            lambda: _fake_model(query),
        )
        return {"fresh_issue_id": response["issue_id"]}

    def choose_and_fetch(state: State) -> dict[str, Any]:
        cached_id = next(
            (event["payload"]["issue_id"] for event in state["events"] if event["kind"] == "cached_selection"),
            state.get("fresh_issue_id"),
        )
        response = boundaries.call(
            "tool", "demo.fetch_issue", {"issue_id": cached_id},
            lambda: _fake_tool(cached_id),
        )
        return {"selected_issue_id": response["issue_id"]}

    graph = StateGraph(State)
    graph.add_node("search", search)
    graph.add_node("choose_and_fetch", choose_and_fetch)
    graph.add_edge(START, "search")
    graph.add_edge("search", "choose_and_fetch")
    graph.add_edge("choose_and_fetch", END)
    return graph.compile()


def wrong_issue(result: dict[str, Any]) -> bool:
    return result.get("selected_issue_id") == 99 and result.get("expected_issue_id") == 42
