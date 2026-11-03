"""Synthetic stateful transition function used by M3 examples and tests."""
from __future__ import annotations

from chrolo.trajectory import TrackedState
from chrolo.core import Event


def step(state: TrackedState, event: Event) -> None:
    if event.kind == "search":
        state["fresh_issue_id"] = event.payload["issue_id"]
    elif event.kind == "select":
        fresh = state.get("fresh_issue_id")
        state["selected_issue_id"] = event.payload["cached_issue_id"]
        state["wrong_issue"] = fresh is not None and fresh != event.payload["cached_issue_id"]


def fixed_step(state: TrackedState, event: Event) -> None:
    if event.kind == "search":
        state["fresh_issue_id"] = event.payload["issue_id"]
    elif event.kind == "select":
        fresh = state.get("fresh_issue_id")
        state["selected_issue_id"] = fresh
        state["wrong_issue"] = False


def wrong_issue(state: dict) -> bool:
    return state.get("wrong_issue") is True
