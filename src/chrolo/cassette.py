"""Versioned, JSON-only recording and strict offline replay of boundary calls.

This module records explicit calls made through a wrapper. It does not intercept
arbitrary network traffic, and trace payloads may contain sensitive data.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Callable

FORMAT = "chrolo.interactions.v1"
_BOUNDARIES = {"model", "tool"}


def _validate_name(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _json_copy(value: Any, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must contain only JSON-compatible data") from exc


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _validate_boundary(boundary: str) -> str:
    if boundary not in _BOUNDARIES:
        raise ValueError("boundary must be 'model' or 'tool'")
    return boundary


class ReplayMismatch(RuntimeError):
    """A call did not match the next recorded interaction."""

    def __init__(self, sequence: int, reason: str):
        self.sequence = sequence
        self.reason = reason
        super().__init__(f"Replay mismatch at interaction {sequence}: {reason}")


class RecordedCallError(RuntimeError):
    """A boundary call raised during recording and is replayed as data."""

    def __init__(self, sequence: int, boundary: str, target: str, error_type: str, message: str):
        self.sequence = sequence
        self.boundary = boundary
        self.target = target
        self.recorded_type = error_type
        self.recorded_message = message
        super().__init__(f"Recorded {boundary} error from {target} ({error_type}): {message}")


class CassetteRecorder:
    """Record explicit model/tool wrapper calls and their JSON results."""

    def __init__(self) -> None:
        self._interactions: list[dict[str, Any]] = []
        self._complete = True

    def call(self, boundary: str, target: str, request: Any, invoke: Callable[[], Any]) -> Any:
        """Invoke a live boundary once and record its JSON request and outcome."""
        boundary = _validate_boundary(boundary)
        target = _validate_name(target, "target")
        if not callable(invoke):
            raise TypeError("invoke must be callable")
        request_copy = _json_copy(request, "request")
        sequence = len(self._interactions)
        common = {
            "id": f"call-{sequence + 1:06d}",
            "sequence": sequence,
            "boundary": boundary,
            "target": target,
            "request": request_copy,
        }
        try:
            response = invoke()
        except Exception as exc:
            self._interactions.append({
                **common,
                "error": {
                    "type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                    "message": str(exc),
                },
            })
            raise
        try:
            response_copy = _json_copy(response, "response")
        except ValueError:
            # A live call already happened but cannot be represented faithfully.
            # Prevent callers from saving this partial cassette as complete.
            self._complete = False
            raise
        self._interactions.append({**common, "response": response_copy})
        return response

    def to_dict(self) -> dict[str, Any]:
        """Return an independent, serializable trace document."""
        if not self._complete:
            raise ValueError("Cassette is incomplete because a response was not JSON-compatible")
        return {"format": FORMAT, "interactions": _json_copy(self._interactions, "interactions")}


class ReplaySession:
    """Replay a cassette in order. This class has no live invocation fallback."""

    def __init__(self, interactions: list[dict[str, Any]]) -> None:
        self._interactions = interactions
        self._cursor = 0

    @classmethod
    def from_dict(cls, trace: Mapping[str, Any]) -> "ReplaySession":
        if not isinstance(trace, Mapping) or trace.get("format") != FORMAT:
            raise ValueError(f"Unsupported cassette format; expected {FORMAT}")
        interactions = trace.get("interactions")
        if not isinstance(interactions, list):
            raise ValueError("Cassette interactions must be an array")

        parsed: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for index, raw in enumerate(interactions):
            if not isinstance(raw, Mapping):
                raise ValueError(f"Interaction at index {index} must be an object")
            expected_common = {"id", "sequence", "boundary", "target", "request"}
            if not expected_common.issubset(raw):
                raise ValueError(f"Interaction at index {index} is missing required fields")
            interaction_id = _validate_name(raw["id"], "interaction id")
            if interaction_id in seen_ids:
                raise ValueError(f"Duplicate interaction ID: {interaction_id}")
            seen_ids.add(interaction_id)
            if type(raw["sequence"]) is not int or raw["sequence"] != index:
                raise ValueError(f"Interaction sequence at index {index} must equal {index}")
            boundary = _validate_boundary(raw["boundary"])
            target = _validate_name(raw["target"], "target")
            request = _json_copy(raw["request"], "request")
            has_response = "response" in raw
            has_error = "error" in raw
            if has_response == has_error:
                raise ValueError(f"Interaction at index {index} must contain exactly one of response or error")
            if has_response:
                if set(raw) != expected_common | {"response"}:
                    raise ValueError(f"Interaction at index {index} contains unsupported fields")
                parsed.append({
                    "id": interaction_id,
                    "sequence": index,
                    "boundary": boundary,
                    "target": target,
                    "request": request,
                    "response": _json_copy(raw["response"], "response"),
                })
            else:
                if set(raw) != expected_common | {"error"}:
                    raise ValueError(f"Interaction at index {index} contains unsupported fields")
                error = raw["error"]
                if not isinstance(error, Mapping) or set(error) != {"type", "message"}:
                    raise ValueError(f"Interaction error at index {index} must contain type and message")
                parsed.append({
                    "id": interaction_id,
                    "sequence": index,
                    "boundary": boundary,
                    "target": target,
                    "request": request,
                    "error": {
                        "type": _validate_name(error["type"], "recorded error type"),
                        "message": str(error["message"]),
                    },
                })
        return cls(parsed)

    @property
    def consumed(self) -> int:
        return self._cursor

    def call(self, boundary: str, target: str, request: Any) -> Any:
        """Return the next recorded response or raise its recorded error.

        A mismatch does not consume the expected interaction. Calls never reach
        a live model or tool; no invoke callback is accepted by this API.
        """
        boundary = _validate_boundary(boundary)
        target = _validate_name(target, "target")
        request_copy = _json_copy(request, "request")
        if self._cursor >= len(self._interactions):
            raise ReplayMismatch(self._cursor, "unexpected call; cassette is exhausted")

        expected = self._interactions[self._cursor]
        if boundary != expected["boundary"]:
            raise ReplayMismatch(self._cursor, f"expected {expected['boundary']} boundary")
        if target != expected["target"]:
            raise ReplayMismatch(self._cursor, f"expected target {expected['target']!r}")
        if _canonical(request_copy) != _canonical(expected["request"]):
            raise ReplayMismatch(self._cursor, "request differs from the recorded request")

        self._cursor += 1
        if "error" in expected:
            error = expected["error"]
            raise RecordedCallError(
                expected["sequence"], boundary, target, error["type"], error["message"]
            )
        return _json_copy(expected["response"], "response")

    def finish(self) -> None:
        """Require complete consumption; reject traces with unused interactions."""
        remaining = len(self._interactions) - self._cursor
        if remaining:
            raise ReplayMismatch(self._cursor, f"{remaining} recorded interaction(s) were not consumed")


class BoundaryContext:
    """Uniform call hook supplied to an instrumented graph factory.

    In recording mode ``call`` invokes the supplied live implementation once
    and records its outcome. In replay mode it delegates only to ReplaySession;
    the live implementation is required for a shared graph-node API but is
    never called. ReplaySession itself has no callback parameter or fallback.
    """

    def __init__(
        self,
        *,
        recorder: CassetteRecorder | None = None,
        replay: ReplaySession | None = None,
    ) -> None:
        if (recorder is None) == (replay is None):
            raise ValueError("provide exactly one of recorder or replay")
        self._recorder = recorder
        self._replay = replay

    @classmethod
    def recording(cls, recorder: CassetteRecorder) -> "BoundaryContext":
        return cls(recorder=recorder)

    @classmethod
    def replaying(cls, replay: ReplaySession) -> "BoundaryContext":
        return cls(replay=replay)

    @property
    def mode(self) -> str:
        return "record" if self._recorder is not None else "replay"

    def call(
        self,
        boundary: str,
        target: str,
        request: Any,
        invoke: Callable[[], Any],
    ) -> Any:
        """Route an instrumented node call through recording or strict replay."""
        if not callable(invoke):
            raise TypeError("invoke must be callable")
        if self._recorder is not None:
            return self._recorder.call(boundary, target, request, invoke)
        # Deliberately do not pass invoke through to ReplaySession.
        assert self._replay is not None
        return self._replay.call(boundary, target, request)
