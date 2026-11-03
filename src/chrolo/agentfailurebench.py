"""Deterministic replay helpers for AgentFailureBench differential traces.

This adapter replays recorded tool observations and preserves an execution-visible
difference from the supplied corrected trajectory. It does not execute the
synthetic tool environment or claim production task-failure reproduction.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .core import Event


def _step_signature(step: dict[str, Any]) -> str:
    execution = {
        "tool_call": step.get("tool_call"),
        "tool_output": step.get("tool_output"),
    }
    encoded = json.dumps(
        execution, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def replay_trace(events: list[Event]) -> dict[str, Any]:
    """Return a candidate trace from recorded steps; never calls external tools."""
    scenarios = [event for event in events if event.kind == "scenario"]
    if len(scenarios) != 1:
        raise ValueError("Candidate must retain exactly one scenario event")
    steps = [event.payload for event in events if event.kind == "agent_step"]
    terminals = [event.payload for event in events if event.kind == "trace_terminal"]
    indexes = [step.get("step_index") for step in steps]
    if any(type(index) is not int for index in indexes) or len(set(indexes)) != len(indexes):
        raise ValueError("Candidate agent step indexes must be unique integers")
    return {
        "scenario": scenarios[0].payload,
        "steps": sorted(steps, key=lambda step: step["step_index"]),
        "terminals": terminals,
    }


def preserves_trace_divergence(result: dict[str, Any]) -> bool:
    """Require every recorded execution-visible divergence witness to remain exact."""
    if not isinstance(result, dict) or not isinstance(result.get("scenario"), dict):
        return False
    scenario = result["scenario"]
    witnesses = scenario.get("witnesses")
    if not isinstance(witnesses, list) or not witnesses:
        return False
    steps = {
        step.get("step_index"): step
        for step in result.get("steps", [])
        if isinstance(step, dict) and type(step.get("step_index")) is int
    }
    terminals = {
        item.get("missing_step_index")
        for item in result.get("terminals", [])
        if isinstance(item, dict)
    }
    for witness in witnesses:
        if not isinstance(witness, dict):
            return False
        index = witness.get("step_index")
        kind = witness.get("kind")
        if type(index) is not int:
            return False
        if kind in {"changed", "extra"}:
            step = steps.get(index)
            if step is None or _step_signature(step) != witness.get("failed_signature"):
                return False
        elif kind == "missing":
            if index not in terminals or index in steps:
                return False
        else:
            return False
    return True


def replay_corrected_trace(events: list[Event]) -> dict[str, Any]:
    """Apply the record's paired corrected-step intervention to a candidate trace."""
    result = replay_trace(events)
    witnesses = result["scenario"].get("witnesses", [])
    steps = {step["step_index"]: step for step in result["steps"]}
    terminals = list(result["terminals"])
    for witness in witnesses:
        index = witness["step_index"]
        kind = witness["kind"]
        if kind == "changed":
            if index not in steps:
                raise ValueError("Cannot apply corrected step: witness step is missing")
            steps[index] = witness["corrected_step"]
        elif kind == "extra":
            steps.pop(index, None)
        elif kind == "missing":
            steps[index] = witness["corrected_step"]
            terminals = [item for item in terminals if item.get("missing_step_index") != index]
    return {
        "scenario": result["scenario"],
        "steps": [steps[index] for index in sorted(steps)],
        "terminals": terminals,
    }
