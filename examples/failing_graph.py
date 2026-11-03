"""A deterministic graph example for capturing a structured failure outcome."""

import json
from pathlib import Path

from chrolo.adapters_langgraph import replay_recorded


class FailureGraph:
    def invoke(self, state):
        if any(event["kind"] == "trigger" for event in state["events"]):
            raise RuntimeError("sensitive provider text must not enter the trace")
        return {"status": "success"}


class FixedGraph:
    def invoke(self, state):
        return {"status": "success"}


def build_graph():
    return FailureGraph()


def build_fixed_graph():
    return FixedGraph()


def original_failure(result):
    return (
        isinstance(result, dict)
        and result.get("status") == "exception"
        and result.get("exception_type") == "builtins.RuntimeError"
    )


def _captured_failure():
    path = Path(__file__).with_name("failing_graph_capture.json")
    return json.loads(path.read_text(encoding="utf-8"))


def run_captured_failure(events):
    return replay_recorded(_captured_failure(), events)


def run_fixed_failure(events):
    capture = _captured_failure()
    capture["graph"] = "examples.failing_graph:build_fixed_graph"
    return replay_recorded(capture, events)
