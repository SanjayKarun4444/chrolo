"""Versioned, JSON-only recording and strict offline replay of boundary calls.

This module records explicit calls made through a wrapper. It does not intercept
arbitrary network traffic, and trace payloads may contain sensitive data.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Callable

FORMAT = "chrolo.interactions.v2"
LEGACY_FORMAT = "chrolo.interactions.v1"
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
        self._created_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        self._execution_id = str(uuid.uuid4())
        try:
            chrolo_version = version("chrolo-starter")
        except PackageNotFoundError:
            chrolo_version = "unknown"
        self._chrolo_version = chrolo_version
        self._adapters: dict[str, str] = {"chrolo.boundary": "1"}

    def register_adapter(self, name: str, adapter_version: str) -> None:
        name = _validate_name(name, "adapter name")
        adapter_version = _validate_name(adapter_version, "adapter version")
        self._adapters[name] = adapter_version

    def call(
        self,
        boundary: str,
        target: str,
        request: Any,
        invoke: Callable[[], Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> Any:
        """Invoke a live boundary once and record its JSON request and outcome."""
        boundary = _validate_boundary(boundary)
        target = _validate_name(target, "target")
        if not callable(invoke):
            raise TypeError("invoke must be callable")
        request_copy = _json_copy(request, "request")
        context_copy = None
        if context is not None:
            if not isinstance(context, Mapping):
                raise ValueError("context must be a JSON object")
            context_copy = _json_copy(context, "context")
        sequence = len(self._interactions)
        common = {
            "id": f"call-{sequence + 1:06d}",
            "sequence": sequence,
            "boundary": boundary,
            "target": target,
            "request": request_copy,
        }
        if context_copy is not None:
            common["context"] = context_copy
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
        return {
            "format": FORMAT,
            "header": {
                "schema_version": 2,
                "chrolo_version": self._chrolo_version,
                "captured_at": self._created_at,
                "execution_id": self._execution_id,
                "adapters": dict(sorted(self._adapters.items())),
            },
            "interactions": _json_copy(self._interactions, "interactions"),
        }


class ReplaySession:
    """Replay a cassette in order. This class has no live invocation fallback."""

    def __init__(
        self, interactions: list[dict[str, Any]], *, allow_skips: bool = False
    ) -> None:
        self._interactions = interactions
        self._cursor = 0
        self._allow_skips = allow_skips
        self._used_interactions: list[dict[str, Any]] = []

    @classmethod
    def from_dict(
        cls, trace: Mapping[str, Any], *, allow_skips: bool = False
    ) -> "ReplaySession":
        if not isinstance(trace, Mapping) or trace.get("format") not in {FORMAT, LEGACY_FORMAT}:
            raise ValueError(f"Unsupported cassette format; expected {FORMAT} or {LEGACY_FORMAT}")
        if trace["format"] == FORMAT:
            header = trace.get("header")
            if not isinstance(header, Mapping):
                raise ValueError("Cassette v2 header must be an object")
            if header.get("schema_version") != 2:
                raise ValueError("Cassette header schema_version must be 2")
            for field in ("chrolo_version", "captured_at", "execution_id"):
                _validate_name(header.get(field), f"header {field}")
            if set(header) != {"schema_version", "chrolo_version", "captured_at", "execution_id", "adapters"}:
                raise ValueError("Cassette v2 header contains unsupported fields")
            try:
                captured_at = datetime.fromisoformat(header["captured_at"].replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("Cassette header captured_at must be an ISO-8601 timestamp") from exc
            if captured_at.tzinfo is None:
                raise ValueError("Cassette header captured_at must include a timezone")
            try:
                uuid.UUID(header["execution_id"])
            except (ValueError, AttributeError) as exc:
                raise ValueError("Cassette header execution_id must be a UUID") from exc
            adapters = header.get("adapters")
            if not isinstance(adapters, Mapping) or not adapters:
                raise ValueError("Cassette header adapters must be a non-empty object")
            for name, adapter_version in adapters.items():
                _validate_name(name, "adapter name")
                _validate_name(adapter_version, "adapter version")
        if "redaction" in trace:
            redaction = trace["redaction"]
            if not isinstance(redaction, Mapping):
                raise ValueError("Cassette redaction report is invalid")
            raise ValueError("Redacted cassette cannot be replayed safely")
        if "filtering" in trace:
            filtering = trace["filtering"]
            if not isinstance(filtering, Mapping):
                raise ValueError("Cassette filtering report is invalid")
            raise ValueError("Filtered cassette cannot be replayed safely")
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
            has_context = "context" in raw
            if has_context and trace["format"] == LEGACY_FORMAT:
                raise ValueError(f"Interaction context at index {index} is not supported by cassette v1")
            if has_context and not isinstance(raw["context"], Mapping):
                raise ValueError(f"Interaction context at index {index} must be an object")
            context = _json_copy(raw["context"], "context") if has_context else None
            has_response = "response" in raw
            has_error = "error" in raw
            if has_response == has_error:
                raise ValueError(f"Interaction at index {index} must contain exactly one of response or error")
            if has_response:
                if set(raw) != expected_common | ({"context"} if has_context else set()) | {"response"}:
                    raise ValueError(f"Interaction at index {index} contains unsupported fields")
                parsed.append({
                    "id": interaction_id,
                    "sequence": index,
                    "boundary": boundary,
                    "target": target,
                    "request": request,
                    **({"context": context} if has_context else {}),
                    "response": _json_copy(raw["response"], "response"),
                })
            else:
                if set(raw) != expected_common | ({"context"} if has_context else set()) | {"error"}:
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
                    **({"context": context} if has_context else {}),
                    "error": {
                        "type": _validate_name(error["type"], "recorded error type"),
                        "message": str(error["message"]),
                    },
                })
        return cls(parsed, allow_skips=allow_skips)

    @property
    def consumed(self) -> int:
        return self._cursor

    @property
    def used_interactions(self) -> list[dict[str, Any]]:
        """Return the exact records used by this replay, in call order."""
        return _json_copy(self._used_interactions, "used interactions")

    def call(
        self,
        boundary: str,
        target: str,
        request: Any,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> Any:
        """Return the next recorded response or raise its recorded error.

        A mismatch does not consume the expected interaction. Calls never reach
        a live model or tool; no invoke callback is accepted by this API.
        """
        boundary = _validate_boundary(boundary)
        target = _validate_name(target, "target")
        request_copy = _json_copy(request, "request")
        context_copy = None
        if context is not None:
            if not isinstance(context, Mapping):
                raise ValueError("context must be a JSON object")
            context_copy = _json_copy(context, "context")
        def matches(record: Mapping[str, Any]) -> bool:
            return (
                boundary == record["boundary"]
                and target == record["target"]
                and _canonical(request_copy) == _canonical(record["request"])
                and _canonical(context_copy) == _canonical(record.get("context"))
            )

        if self._cursor >= len(self._interactions):
            raise ReplayMismatch(self._cursor, "unexpected call; cassette is exhausted")

        expected = self._interactions[self._cursor]
        if self._allow_skips and not matches(expected):
            later_matches = [
                index for index in range(self._cursor + 1, len(self._interactions))
                if matches(self._interactions[index])
            ]
            if not later_matches:
                raise ReplayMismatch(self._cursor, "no matching recorded interaction remains in cassette")
            if len(later_matches) > 1:
                raise ReplayMismatch(self._cursor, "ambiguous repeated interaction while minimizing cassette")
            self._cursor = later_matches[0]
            expected = self._interactions[self._cursor]
        elif not matches(expected):
            if boundary != expected["boundary"]:
                raise ReplayMismatch(self._cursor, f"expected {expected['boundary']} boundary")
            if target != expected["target"]:
                raise ReplayMismatch(self._cursor, f"expected target {expected['target']!r}")
            if _canonical(request_copy) != _canonical(expected["request"]):
                raise ReplayMismatch(self._cursor, "request differs from the recorded request")
            raise ReplayMismatch(self._cursor, "context differs from the recorded context")

        self._used_interactions.append(expected)
        self._cursor += 1
        if "error" in expected:
            error = expected["error"]
            raise RecordedCallError(
                expected["sequence"], boundary, target, error["type"], error["message"]
            )
        return _json_copy(expected["response"], "response")

    def finish(self) -> None:
        """Require complete consumption; reject traces with unused interactions."""
        if self._allow_skips:
            return
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

    def register_adapter(self, name: str, adapter_version: str) -> None:
        """Add adapter provenance to a recording header; replay is a no-op."""
        if self._recorder is not None:
            self._recorder.register_adapter(name, adapter_version)

    def call(
        self,
        boundary: str,
        target: str,
        request: Any,
        invoke: Callable[[], Any],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> Any:
        """Route an instrumented node call through recording or strict replay."""
        if not callable(invoke):
            raise TypeError("invoke must be callable")
        if self._recorder is not None:
            return self._recorder.call(boundary, target, request, invoke, context=context)
        # Deliberately do not pass invoke through to ReplaySession.
        assert self._replay is not None
        return self._replay.call(boundary, target, request, context=context)


class BoundaryTool:
    """Wrap a JSON-argument tool so its result is recorded or replayed offline.

    The wrapped function receives the argument object as its sole parameter.
    Replay never calls that function. Tool results and arguments must be JSON
    compatible so the cassette can reproduce them faithfully.
    """

    def __init__(
        self,
        function: Callable[[Any], Any],
        boundaries: BoundaryContext,
        target: str,
        *,
        context: Mapping[str, Any] | None = None,
    ):
        if not callable(function):
            raise TypeError("function must be callable")
        if not isinstance(boundaries, BoundaryContext):
            raise TypeError("boundaries must be a BoundaryContext")
        self.function = function
        self.boundaries = boundaries
        self.target = _validate_name(target, "target")
        self.context = context
        boundaries.register_adapter("chrolo.boundary_tool", "1")

    def invoke(self, arguments: Any) -> Any:
        return self.boundaries.call(
            "tool", self.target, arguments, lambda: self.function(arguments), context=self.context
        )

    __call__ = invoke
