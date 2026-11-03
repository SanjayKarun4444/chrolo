"""Data-only extraction of environment actions from OpenHands trajectories.

This module does not execute tool calls. Extracted arguments are untrusted and
must only be passed to a separately isolated replay environment.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


ACTION_TOOLS = frozenset({"execute_bash", "str_replace_editor"})


def extract_actions(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return ordered environment-mutating calls from one OpenHands row.

    OpenHands trajectory tool arguments are JSON strings. Non-action calls
    (including ``think`` and ``finish``) and message text are intentionally
    omitted. Malformed action arguments fail closed with a useful ValueError.
    """
    if not isinstance(row, Mapping):
        raise ValueError("OpenHands row must be an object")
    trajectory = row.get("trajectory")
    if not isinstance(trajectory, Sequence) or isinstance(trajectory, (str, bytes)):
        raise ValueError("OpenHands row trajectory must be an array")

    actions: list[dict[str, Any]] = []
    for message_index, message in enumerate(trajectory):
        if not isinstance(message, Mapping):
            raise ValueError(f"Trajectory message {message_index} must be an object")
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list):
            raise ValueError(f"Trajectory message {message_index} tool_calls must be an array")
        for call_index, call in enumerate(calls):
            if not isinstance(call, Mapping):
                raise ValueError(f"Tool call {message_index}:{call_index} must be an object")
            function = call.get("function")
            if not isinstance(function, Mapping):
                raise ValueError(f"Tool call {message_index}:{call_index} function must be an object")
            name = function.get("name")
            if name not in ACTION_TOOLS:
                continue
            raw_arguments = function.get("arguments")
            if not isinstance(raw_arguments, str):
                raise ValueError(f"Action arguments at {message_index}:{call_index} must be JSON text")
            try:
                arguments = json.loads(raw_arguments)
            except (TypeError, json.JSONDecodeError):
                raise ValueError(f"Action arguments at {message_index}:{call_index} are invalid JSON") from None
            if not isinstance(arguments, dict):
                raise ValueError(f"Action arguments at {message_index}:{call_index} must decode to an object")
            actions.append({
                "id": f"action-{len(actions):04d}",
                "kind": name,
                "payload": {"arguments": arguments},
            })
    return actions
