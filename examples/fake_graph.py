"""Test-only local graph shim with the same invoke/stream contract as LangGraph.

This lets tests validate Chrolo's adapter without requiring optional LangGraph.
It is NOT a substitute for the real LangGraph integration test.
"""
from __future__ import annotations


class FakeGraph:
    def invoke(self, state):
        snapshots = list(self.stream(state, stream_mode="values"))
        return snapshots[-1]

    def stream(self, state, *, stream_mode):
        if stream_mode != "values":
            raise ValueError("unsupported mode")
        state = dict(state)
        yield dict(state)
        state["selected_issue_id"] = next(
            (e["payload"]["correct_issue_id"] for e in state["events"] if e["kind"] == "issue_search"),
            None,
        )
        yield dict(state)
        state["selected_issue_id"] = next(
            (e["payload"]["issue_id"] for e in state["events"] if e["kind"] == "cached_selection"),
            state["selected_issue_id"],
        )
        yield dict(state)


def build_graph():
    return FakeGraph()


def wrong_issue(result):
    return result.get("expected_issue_id") == 42 and result.get("selected_issue_id") == 99


class FixedGraph(FakeGraph):
    def stream(self, state, *, stream_mode):
        if stream_mode != "values":
            raise ValueError("unsupported mode")
        state = dict(state)
        yield dict(state)
        state["selected_issue_id"] = next(
            (e["payload"]["correct_issue_id"] for e in state["events"] if e["kind"] == "issue_search"),
            None,
        )
        yield dict(state)
        # Fixed: do not override fresh search results with stale cached selection.
        yield dict(state)


def build_fixed_graph():
    return FixedGraph()
