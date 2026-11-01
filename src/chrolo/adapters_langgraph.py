"""Checkpoint-free LangGraph-compatible capture and replay adapter.

By default, this adapter re-executes a developer-provided compiled graph with
recorded input events. Opt-in boundary-aware factories can also record and
replay model/tool calls explicitly routed through BoundaryContext. It does not
intercept arbitrary services, checkpoint history, or other external side effects. Use only safe, deterministic, trusted graph factories.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .core import Event, parse_events
from .cassette import BoundaryContext, CassetteRecorder, ReplaySession

FORMAT = "chrolo.langgraph.v1"


def _ensure_graph(factory: Callable[..., Any], boundaries: BoundaryContext | None = None) -> Any:
    # No-argument factories remain the default M1 contract. Boundary-aware
    # factories opt in and receive an explicit recording/replay context.
    graph = factory() if boundaries is None else factory(boundaries)
    if not callable(getattr(graph, "invoke", None)):
        raise TypeError("Graph factory must return a compiled graph with invoke(state)")
    return graph


def _input(initial_state: Mapping[str, Any], events: Sequence[Event], key: str) -> dict[str, Any]:
    state = copy.deepcopy(dict(initial_state))
    if key in state:
        raise ValueError(f"Initial state may not contain reserved event key {key!r}")
    state[key] = [event.to_dict() for event in events]
    return state


@dataclass
class GraphHarness:
    factory: Callable[..., Any]
    initial_state: Mapping[str, Any]
    event_key: str = "events"
    interaction_trace: Mapping[str, Any] | None = None

    def __call__(self, events: list[Event]) -> Any:
        # Fresh graph and replay cursor per candidate.
        replay = ReplaySession.from_dict(self.interaction_trace) if self.interaction_trace is not None else None
        boundaries = BoundaryContext.replaying(replay) if replay is not None else None
        graph = _ensure_graph(self.factory, boundaries)
        result = graph.invoke(_input(self.initial_state, events, self.event_key))
        if replay is not None:
            replay.finish()
        return result


def capture_graph(
    factory: Callable[[], Any],
    initial_state: Mapping[str, Any],
    events: Sequence[Event],
    *,
    event_key: str = "events",
    graph_target: str,
    record_interactions: bool = False,
) -> dict[str, Any]:
    """Run a graph once and record its per-step state and initial event fixture.

    Stream values is supported by LangGraph's compiled StateGraph. Captured
    snapshots aid debugging; minimization uses original input events, not the
    output snapshots, which are NOT freely removable from arbitrary graphs.
    """
    if not event_key or not event_key.isidentifier():
        raise ValueError("event_key must be an identifier")
    recorder = CassetteRecorder() if record_interactions else None
    boundaries = BoundaryContext.recording(recorder) if recorder is not None else None
    graph = _ensure_graph(factory, boundaries)
    if not callable(getattr(graph, "stream", None)):
        raise TypeError("Graph must implement stream(state, stream_mode='values')")
    snapshots: list[Any] = []
    for snapshot in graph.stream(_input(initial_state, events, event_key), stream_mode="values"):
        # LangGraph's values mode yields complete state for each step.
        snapshots.append(copy.deepcopy(snapshot))
    if not snapshots:
        raise ValueError("Graph returned no state snapshots")
    capture = {
        "format": FORMAT,
        "graph": graph_target,
        "event_key": event_key,
        "initial_state": copy.deepcopy(dict(initial_state)),
        "events": [event.to_dict() for event in events],
        "observations": {"state_snapshots": snapshots, "final_state": snapshots[-1]},
    }
    if recorder is not None:
        capture["interactions"] = recorder.to_dict()
    return capture


def validate_capture(data: Mapping[str, Any]) -> list[Event]:
    if data.get("format") != FORMAT:
        raise ValueError(f"Unsupported capture format; expected {FORMAT}")
    if not isinstance(data.get("initial_state"), dict):
        raise ValueError("Capture initial_state must be a JSON object")
    if not isinstance(data.get("graph"), str) or ":" not in data["graph"]:
        raise ValueError("Capture graph must be module:factory")
    if not isinstance(data.get("event_key"), str) or not data["event_key"].isidentifier():
        raise ValueError("Capture event_key must be an identifier")
    if data["event_key"] in data["initial_state"]:
        raise ValueError("Capture initial_state must not contain the event key")
    if not isinstance(data.get("events"), list):
        raise ValueError("Capture events must be a list")
    return parse_events(data["events"])


def replay_recorded(capture: Mapping[str, Any], events: Sequence[Event] | None = None) -> Any:
    """Re-run a trusted graph factory from its initial state with saved events.

    This executes code from capture['graph']; NEVER run untrusted captures.
    """
    from .cli import resolve

    original = validate_capture(capture)
    return GraphHarness(
        resolve(capture["graph"]),
        capture["initial_state"],
        capture["event_key"],
        capture.get("interactions"),
    )(list(events) if events is not None else original)
