"""Deterministic example where deleting an input also prunes its tool call."""

from chrolo.cassette import BoundaryContext

LIVE_CALLS = {"tool": 0, "model": 0}


def reset_live_calls():
    LIVE_CALLS.update(tool=0, model=0)


def _live_tool(arguments):
    LIVE_CALLS["tool"] += 1
    return {"diagnostic": arguments["query"]}


def _live_model(request):
    LIVE_CALLS["model"] += 1
    return {"failure": True}


class FailureGraph:
    def __init__(self, boundaries: BoundaryContext):
        self.boundaries = boundaries

    def invoke(self, state):
        failure = False
        for event in state["events"]:
            if event["kind"] == "probe":
                self.boundaries.call(
                    "tool", "example.diagnostic", event["payload"],
                    lambda: _live_tool(event["payload"]),
                )
            elif event["kind"] == "trigger":
                response = self.boundaries.call(
                    "model", "example.failure-model", {"question": "reproduce"},
                    lambda: _live_model({"question": "reproduce"}),
                )
                failure = bool(response["failure"])
        return {"failure": failure}


class FixedGraph(FailureGraph):
    def invoke(self, state):
        for event in state["events"]:
            if event["kind"] == "probe":
                self.boundaries.call(
                    "tool", "example.diagnostic", event["payload"],
                    lambda: _live_tool(event["payload"]),
                )
            elif event["kind"] == "trigger":
                self.boundaries.call(
                    "model", "example.failure-model", {"question": "reproduce"},
                    lambda: _live_model({"question": "reproduce"}),
                )
        return {"failure": False}


def build_graph(boundaries):
    return FailureGraph(boundaries)


def build_fixed_graph(boundaries):
    return FixedGraph(boundaries)


def original_failure(result):
    return isinstance(result, dict) and result.get("failure") is True
