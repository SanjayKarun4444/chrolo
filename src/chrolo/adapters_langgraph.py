"""Checkpoint-free LangGraph-compatible capture and replay adapter.

By default, this adapter re-executes a developer-provided compiled graph with
recorded input events. Opt-in boundary-aware factories can also record and
replay model/tool calls explicitly routed through BoundaryContext. It does not
intercept arbitrary services, checkpoint history, or other external side effects. Use only safe, deterministic, trusted graph factories.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Callable, Mapping, Sequence

from .core import Event, parse_events
from .cassette import (
    BoundaryContext,
    CassetteRecorder,
    RecordedCallError,
    ReplayMismatch,
    ReplaySession,
)

FORMAT = "chrolo.langgraph.v1"
OUTCOME_CAPTURE_FORMAT = "chrolo.langgraph.v2"
OUTCOME_FORMAT = "chrolo.outcome.v1"


def _exception_type(exc: Exception) -> str:
    """Return a stable exception type without serializing sensitive messages."""
    if isinstance(exc, RecordedCallError):
        return exc.recorded_type
    return f"{type(exc).__module__}.{type(exc).__qualname__}"


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
    expected_exception: bool = False
    allow_interaction_skips: bool = False
    last_used_interactions: list[dict[str, Any]] = field(default_factory=list, init=False)

    def __call__(self, events: list[Event]) -> Any:
        # Fresh graph and replay cursor per candidate.
        self.last_used_interactions = []
        replay = (
            ReplaySession.from_dict(
                self.interaction_trace, allow_skips=self.allow_interaction_skips
            )
            if self.interaction_trace is not None else None
        )
        boundaries = BoundaryContext.replaying(replay) if replay is not None else None
        graph = _ensure_graph(self.factory, boundaries)
        try:
            result = graph.invoke(_input(self.initial_state, events, self.event_key))
        except ReplayMismatch:
            raise
        except Exception as exc:
            if not self.expected_exception:
                raise
            if replay is not None:
                replay.finish()
                self.last_used_interactions = replay.used_interactions
            return {"status": "exception", "exception_type": _exception_type(exc)}
        if replay is not None:
            replay.finish()
            self.last_used_interactions = replay.used_interactions
        if self.expected_exception:
            return {"status": "success"}
        return result


def capture_graph(
    factory: Callable[[], Any],
    initial_state: Mapping[str, Any],
    events: Sequence[Event],
    *,
    event_key: str = "events",
    graph_target: str,
    record_interactions: bool = False,
    include_snapshots: bool = False,
    capture_exceptions: bool = False,
) -> dict[str, Any]:
    """Run a graph once and record its initial event fixture.

    Full state snapshots are omitted by default because they may contain
    sensitive data. Set ``include_snapshots`` to stream and retain them for
    debugging. Minimization always uses original input events, not snapshots.
    By default, graph exceptions propagate. ``capture_exceptions`` writes a
    versioned outcome containing the exception class only, without its message
    or stack trace.
    """
    if not event_key or not event_key.isidentifier():
        raise ValueError("event_key must be an identifier")
    recorder = CassetteRecorder() if record_interactions else None
    boundaries = BoundaryContext.recording(recorder) if recorder is not None else None
    if boundaries is not None:
        boundaries.register_adapter("chrolo.langgraph", "1")
        try:
            boundaries.register_adapter("langgraph", version("langgraph"))
        except PackageNotFoundError:
            pass
    graph = _ensure_graph(factory, boundaries)
    graph_input = _input(initial_state, events, event_key)
    snapshots: list[Any] | None = None
    if include_snapshots and not callable(getattr(graph, "stream", None)):
        raise TypeError("Graph must implement stream(state, stream_mode='values') to capture snapshots")
    outcome: dict[str, str] | None = None
    try:
        if include_snapshots:
            snapshots = []
            for snapshot in graph.stream(graph_input, stream_mode="values"):
                snapshots.append(copy.deepcopy(snapshot))
        else:
            graph.invoke(graph_input)
    except Exception as exc:
        if not capture_exceptions:
            raise
        outcome = {
            "format": OUTCOME_FORMAT,
            "status": "exception",
            "exception_type": _exception_type(exc),
        }
    else:
        if include_snapshots and not snapshots:
            raise ValueError("Graph returned no state snapshots")
        if capture_exceptions:
            outcome = {"format": OUTCOME_FORMAT, "status": "success"}
    capture = {
        "format": OUTCOME_CAPTURE_FORMAT if capture_exceptions else FORMAT,
        "graph": graph_target,
        "event_key": event_key,
        "initial_state": copy.deepcopy(dict(initial_state)),
        "events": [event.to_dict() for event in events],
    }
    if snapshots is not None:
        observations = {"state_snapshots": snapshots}
        if snapshots:
            observations["final_state"] = snapshots[-1]
        capture["observations"] = observations
    if outcome is not None:
        capture["outcome"] = outcome
    if recorder is not None:
        capture["interactions"] = recorder.to_dict()
    return capture


def validate_capture(data: Mapping[str, Any]) -> list[Event]:
    capture_format = data.get("format")
    if not isinstance(capture_format, str) or capture_format not in (FORMAT, OUTCOME_CAPTURE_FORMAT):
        raise ValueError(f"Unsupported capture format; expected {FORMAT} or {OUTCOME_CAPTURE_FORMAT}")
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
    if "redaction" in data:
        report = data["redaction"]
        if not isinstance(report, Mapping) or report.get("format") != "chrolo.redaction.v1":
            raise ValueError("Capture redaction report is invalid")
        raise ValueError("Capture contains redaction metadata and cannot be replayed safely")
    if "filtering" in data:
        report = data["filtering"]
        if not isinstance(report, Mapping) or report.get("format") != "chrolo.filtering.v1":
            raise ValueError("Capture filtering report is invalid")
        raise ValueError("Capture contains filtered data and cannot be replayed safely")
    if capture_format == FORMAT and "outcome" in data:
        raise ValueError(f"Capture format {FORMAT} does not support an outcome; use {OUTCOME_CAPTURE_FORMAT}")
    if capture_format == OUTCOME_CAPTURE_FORMAT and "outcome" not in data:
        raise ValueError(f"Capture format {OUTCOME_CAPTURE_FORMAT} requires an outcome")
    if "outcome" in data:
        outcome = data["outcome"]
        if not isinstance(outcome, Mapping) or outcome.get("format") != OUTCOME_FORMAT:
            raise ValueError("Capture outcome format is invalid")
        status = outcome.get("status")
        if status == "success":
            if set(outcome) != {"format", "status"}:
                raise ValueError("Successful capture outcome contains unsupported fields")
        elif status == "exception":
            exception_type = outcome.get("exception_type")
            if not isinstance(exception_type, str) or not exception_type.strip():
                raise ValueError("Capture exception outcome requires an exception_type")
            if set(outcome) != {"format", "status", "exception_type"}:
                raise ValueError("Exception capture outcome contains unsupported fields")
        else:
            raise ValueError("Capture outcome status must be 'success' or 'exception'")
    if "interactions" in data:
        ReplaySession.from_dict(data["interactions"])
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
        isinstance(capture.get("outcome"), Mapping)
        and capture["outcome"].get("status") == "exception",
    )(list(events) if events is not None else original)


def replay_recorded_candidate(
    capture: Mapping[str, Any], events: Sequence[Event]
) -> tuple[Any, list[dict[str, Any]]]:
    """Replay one minimization candidate against an ordered cassette subsequence.

    Only exact, in-order recorded calls may be consumed. Calls skipped by the
    candidate are returned as unused so the accepted minimized capture can drop
    them. Normal ``replay_recorded`` remains strict and rejects unused records.
    """
    from .cli import resolve

    validate_capture(capture)
    harness = GraphHarness(
        resolve(capture["graph"]),
        capture["initial_state"],
        capture["event_key"],
        capture.get("interactions"),
        isinstance(capture.get("outcome"), Mapping)
        and capture["outcome"].get("status") == "exception",
        allow_interaction_skips=True,
    )
    result = harness(list(events))
    return result, harness.last_used_interactions
