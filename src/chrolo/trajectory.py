"""Experimental stateful event dependency inference with intervention witnesses.

The instrumented transition function must read/write state through TrackedState.
Inference covers top-level state keys and keeps only dependencies with a
consumer-output or consumer-error witness when the producer is removed.
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .core import Event, parse_events


def _json_copy(value: Any, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must contain JSON-compatible values") from exc


class TrackedState(MutableMapping[str, Any]):
    """Mapping that records which event last wrote each top-level key."""

    def __init__(self, initial_state: Mapping[str, Any]) -> None:
        if not isinstance(initial_state, Mapping):
            raise ValueError("initial_state must be an object")
        self._data = _json_copy(dict(initial_state), "initial_state")
        if any(not isinstance(key, str) for key in self._data):
            raise ValueError("state keys must be strings")
        self._last_writer: dict[str, str] = {}
        self._current_event: str | None = None
        self._reads: dict[str, set[tuple[str, str]]] = defaultdict(set)
        self._writes: dict[str, set[str]] = defaultdict(set)

    def begin_event(self, event_id: str) -> None:
        self._current_event = event_id

    def end_event(self) -> None:
        self._current_event = None

    def __getitem__(self, key: str) -> Any:
        writer = self._last_writer.get(key)
        if self._current_event is not None and writer is not None and writer != self._current_event:
            self._reads[self._current_event].add((writer, key))
        # Copies prevent untracked nested mutation. Replace the top-level value
        # after editing a nested object so its write is visible to the tracker.
        return _json_copy(self._data[key], f"state[{key!r}]")

    def __setitem__(self, key: str, value: Any) -> None:
        if not isinstance(key, str):
            raise TypeError("state keys must be strings")
        self._data[key] = _json_copy(value, f"state[{key!r}]")
        if self._current_event is not None:
            self._last_writer[key] = self._current_event
            self._writes[self._current_event].add(key)
        else:
            self._last_writer.pop(key, None)

    def __delitem__(self, key: str) -> None:
        writer = self._last_writer.get(key)
        if self._current_event is not None and writer is not None and writer != self._current_event:
            self._reads[self._current_event].add((writer, key))
        del self._data[key]
        if self._current_event is not None:
            self._last_writer[key] = self._current_event
            self._writes[self._current_event].add(key)
        else:
            self._last_writer.pop(key, None)

    def __iter__(self) -> Iterator[str]:
        # Iterating exposes the values' keys; conservatively count it as a read
        # of every currently tracked key.
        if self._current_event is not None:
            for key, writer in self._last_writer.items():
                if key in self._data and writer != self._current_event:
                    self._reads[self._current_event].add((writer, key))
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def snapshot(self) -> dict[str, Any]:
        return _json_copy(self._data, "state")

    @property
    def dependencies(self) -> dict[str, dict[str, set[str]]]:
        result: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for consumer, reads in self._reads.items():
            for producer, key in reads:
                result[consumer][producer].add(key)
        return result

    @property
    def writes(self) -> dict[str, set[str]]:
        return {event_id: set(keys) for event_id, keys in self._writes.items()}


@dataclass(frozen=True)
class DependencyInference:
    events: list[Event]
    verified_edges: list[dict[str, Any]]
    baseline_result: Any


_MISSING = object()


class _StepError(RuntimeError):
    def __init__(self, event_id: str, original: Exception):
        self.event_id = event_id
        super().__init__(str(original))


def run_trajectory(
    initial_state: Mapping[str, Any],
    events: Sequence[Event | Mapping[str, Any]],
    step: Callable[[TrackedState, Event], Any],
) -> dict[str, Any]:
    """Run a stateful event sequence from fresh JSON state and return final state."""
    if not callable(step):
        raise TypeError("step must be callable")
    parsed = parse_events([event.to_dict() if isinstance(event, Event) else event for event in events])
    state = TrackedState(initial_state)
    for event in parsed:
        state.begin_event(event.id)
        try:
            step(state, event)
        finally:
            state.end_event()
    return state.snapshot()


def infer_dependencies(
    initial_state: Mapping[str, Any],
    events: Sequence[Event | Mapping[str, Any]],
    step: Callable[[TrackedState, Event], Any],
    is_failure: Callable[[Any], bool],
) -> DependencyInference:
    """Infer top-level read-after-write edges and verify each by deletion.

    The transition function is rerun from a fresh state for the baseline and
    every intervention. It must be deterministic and safe to execute. An
    inferred edge ``consumer requires producer`` is kept only when deleting
    that producer changes a value written by the consumer or makes that
    consumer fail. This checks the relationship itself, rather than attributing
    any later failure caused by deleting the producer to every reader. The
    supplied oracle verifies the baseline signature; minimization still applies
    that oracle to every candidate.
    """
    if not callable(step) or not callable(is_failure):
        raise TypeError("step and is_failure must be callable")
    parsed = parse_events([event.to_dict() if isinstance(event, Event) else event for event in events])
    if not parsed:
        raise ValueError("At least one event is required for dependency inference")

    def run(candidate: Sequence[Event]) -> tuple[dict[str, Any], TrackedState, dict[str, dict[str, Any]]]:
        state = TrackedState(initial_state)
        snapshots: dict[str, dict[str, Any]] = {}
        for event in candidate:
            state.begin_event(event.id)
            try:
                step(state, event)
            except Exception as exc:
                raise _StepError(event.id, exc) from exc
            finally:
                state.end_event()
            snapshots[event.id] = state.snapshot()
        return state.snapshot(), state, snapshots

    try:
        baseline_result, tracked, baseline_snapshots = run(parsed)
    except Exception as exc:
        raise RuntimeError("Baseline trajectory runner raised before producing a result") from exc
    try:
        baseline_failed = bool(is_failure(baseline_result))
    except Exception as exc:
        raise RuntimeError("Baseline failure oracle raised while evaluating the trajectory") from exc
    if not baseline_failed:
        raise ValueError("Baseline does not reproduce the specified failure")

    positions = {event.id: index for index, event in enumerate(parsed)}
    observed = tracked.dependencies
    edge_keys: dict[tuple[str, str], set[str]] = defaultdict(set)
    for consumer, producers in observed.items():
        for producer, keys in producers.items():
            if positions[producer] < positions[consumer]:
                edge_keys[(consumer, producer)].update(keys)

    verified: list[dict[str, Any]] = []
    verified_by_consumer: dict[str, set[str]] = defaultdict(set)
    for consumer, producer in sorted(edge_keys, key=lambda pair: (positions[pair[1]], positions[pair[0]])):
        candidate = [
            event for event in parsed
            if event.id != producer and positions[event.id] <= positions[consumer]
        ]
        witness: dict[str, Any] | None = None
        try:
            _, _, candidate_snapshots = run(candidate)
            before = baseline_snapshots[consumer]
            after = candidate_snapshots[consumer]
            changed_outputs = sorted(
                key for key in tracked.writes.get(consumer, set())
                if before.get(key, _MISSING) != after.get(key, _MISSING)
            )
            if changed_outputs:
                witness = {"kind": "consumer_state_changed", "output_keys": changed_outputs}
        except _StepError as exc:
            if exc.event_id == consumer:
                witness = {"kind": "consumer_failed_without_producer", "output_keys": []}
        except Exception:
            pass
        if witness is not None:
            verified_by_consumer[consumer].add(producer)
            verified.append({
                "consumer": consumer,
                "requires": producer,
                "state_keys": sorted(edge_keys[(consumer, producer)]),
                "verified_by_intervention": witness,
            })

    result_events: list[Event] = []
    for event in parsed:
        dependencies = set(event.requires) | verified_by_consumer[event.id]
        requires = tuple(sorted(dependencies, key=positions.__getitem__))
        result_events.append(Event(event.id, event.kind, event.payload, requires))
    return DependencyInference(result_events, verified, baseline_result)
