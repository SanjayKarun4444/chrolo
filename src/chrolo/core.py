"""Deterministic event reduction with dependency-preserving deletions.

Important: this is not arbitrary agent replay. The caller supplies a deterministic
harness and a narrowly scoped failure predicate. Traces contain *data*, never code.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class Event:
    id: str
    kind: str
    payload: Mapping[str, Any]
    requires: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "payload": dict(self.payload), "requires": list(self.requires)}


def parse_events(source: Sequence[Mapping[str, Any]]) -> list[Event]:
    events = [Event(id=str(e["id"]), kind=str(e["kind"]), payload=e.get("payload", {}), requires=tuple(e.get("requires", []))) for e in source]
    if len({e.id for e in events}) != len(events):
        raise ValueError("Duplicate event IDs")
    seen: set[str] = set()
    for event in events:
        if not isinstance(event.payload, Mapping):
            raise ValueError(f"Non-object payload for event {event.id}")
        for dep in event.requires:
            if dep not in seen:
                raise ValueError(f"Event {event.id} depends on absent or later event {dep}")
        seen.add(event.id)
    return events


def remove_cascade(events: Sequence[Event], excluded: set[str]) -> list[Event]:
    """Drop specified events and any events dependent on them, transitively."""
    dropped = set(excluded)
    for event in events:
        if any(dep in dropped for dep in event.requires):
            dropped.add(event.id)
    return [e for e in events if e.id not in dropped]


@dataclass
class MinimizeResult:
    events: list[Event]
    checks: int
    baseline_failed: bool


def minimize(
    events: Sequence[Event],
    run: Callable[[list[Event]], Any],
    is_failure: Callable[[Any], bool],
    *,
    max_checks: int = 1000,
) -> MinimizeResult:
    """Reduce a failing run using dependency-preserving ddmin-like deletions.

    The failure predicate must match a specific bug signature. A generic
    "anything went wrong" predicate can produce misleading reductions.

    Guarantees: every accepted candidate still satisfies is_failure(run(...));
    dependencies remain present. Not guaranteed globally minimum.
    """
    if max_checks < 1:
        raise ValueError("max_checks must be positive")
    current = list(events)
    checks = 0
    try:
        baseline_failed = bool(is_failure(run(list(current))))
    except Exception as exc:
        raise RuntimeError("Baseline harness raised; encode the expected exception in your oracle") from exc
    checks += 1
    if not baseline_failed:
        raise ValueError("Baseline does not reproduce the specified failure")

    granularity = 2
    while len(current) > 0 and checks < max_checks:
        chunk_size = ceil(len(current) / granularity)
        reduced = False
        for i in range(0, len(current), chunk_size):
            if checks >= max_checks:
                break
            removed = {e.id for e in current[i:i + chunk_size]}
            candidate = remove_cascade(current, removed)
            if len(candidate) == len(current):
                continue
            checks += 1
            try:
                reproduced = bool(is_failure(run(list(candidate))))
            except Exception:
                reproduced = False
            if reproduced:
                current = candidate
                granularity = max(2, granularity - 1)
                reduced = True
                break
        if reduced:
            continue
        if granularity >= len(current):
            break
        granularity = min(len(current), granularity * 2)

    # An additional greedy pass provides 1-minimality under deletion with
    # dependency cascade when there is sufficient evaluation budget.
    changed = True
    while changed and checks < max_checks:
        changed = False
        for event in list(current):
            if checks >= max_checks:
                break
            candidate = remove_cascade(current, {event.id})
            checks += 1
            try:
                reproduced = bool(is_failure(run(list(candidate))))
            except Exception:
                reproduced = False
            if reproduced:
                current = candidate
                changed = True
                break

    return MinimizeResult(current, checks, baseline_failed)
