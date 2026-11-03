"""Deterministic in-memory task environment for Chrolo's benchmark example.

These tool handlers mutate only a fresh copy of the task fixture state. They
make no network, filesystem, or provider calls; they demonstrate a genuine
runner/oracle loop for synthetic tasks rather than static trace comparison.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from chrolo.core import Event


def _resolve(value: Any, outputs: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$event", "path"}:
            event_id = value["$event"]
            path = value["path"]
            if not isinstance(event_id, str) or not isinstance(path, list):
                raise ValueError("Event references require a string event ID and array path")
            resolved = outputs[event_id]
            for key in path:
                resolved = resolved[key]
            return resolved
        return {key: _resolve(child, outputs) for key, child in value.items()}
    if isinstance(value, list):
        return [_resolve(child, outputs) for child in value]
    return value


def _execute_tool(name: str, arguments: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    if name == "lookup_ticket":
        ticket_id = arguments["ticket_id"]
        ticket = state["tickets"][ticket_id]
        return {"ticket_id": ticket_id, "assigned_team": ticket["assigned_team"]}
    if name == "assign_ticket":
        ticket_id = arguments["ticket_id"]
        ticket = state["tickets"][ticket_id]
        ticket["assigned_team"] = arguments["team"]
        return {"ticket_id": ticket_id, "assigned_team": arguments["team"]}
    if name == "lookup_invoice":
        invoice_id = arguments["invoice_id"]
        return {"invoice_id": invoice_id, **state["invoices"][invoice_id]}
    if name == "charge_invoice":
        invoice_id = arguments["invoice_id"]
        invoice = state["invoices"][invoice_id]
        invoice["payment_count"] += 1
        invoice["paid_cents"] += arguments["amount_cents"]
        return {"invoice_id": invoice_id, **invoice}
    if name == "list_files":
        return {"paths": sorted(state["files"])}
    if name == "delete_file":
        path = arguments["path"]
        existed = path in state["files"]
        state["files"].pop(path, None)
        return {"path": path, "deleted": existed}
    if name == "add_note":
        return {"note_added": True}
    raise ValueError(f"Unknown task tool: {name}")


def _failure_signature(spec: dict[str, Any], state: dict[str, Any]) -> str | None:
    oracle = spec["oracle"]
    kind = oracle["kind"]
    if kind == "wrong_assignment":
        ticket = state["tickets"][oracle["ticket_id"]]
        if ticket["assigned_team"] != oracle["expected_team"]:
            return f"wrong_assignment:{oracle['ticket_id']}:{ticket['assigned_team']}"
    elif kind == "duplicate_payment":
        invoice = state["invoices"][oracle["invoice_id"]]
        if invoice["payment_count"] > 1:
            return f"duplicate_payment:{oracle['invoice_id']}:{invoice['payment_count']}"
    elif kind == "protected_file_missing":
        if oracle["path"] not in state["files"]:
            return f"protected_file_missing:{oracle['path']}"
    else:
        raise ValueError(f"Unknown oracle kind: {kind}")
    return None


def run_task(events: list[Event], *, apply_fix: bool = False) -> dict[str, Any]:
    """Execute retained tool calls in a fresh deterministic task state."""
    specs = [event.payload for event in events if event.kind == "task_spec"]
    if len(specs) != 1:
        raise ValueError("Candidate must retain exactly one task_spec event")
    spec = specs[0]
    state = deepcopy(spec["initial_state"])
    outputs: dict[str, Any] = {}
    executed_tools = []
    fixes = spec.get("fixes", {}) if apply_fix else {}

    for event in events:
        if event.kind != "agent_step":
            continue
        fix = fixes.get(event.id)
        if isinstance(fix, dict) and fix.get("skip") is True:
            continue
        call = event.payload.get("tool_call")
        if not isinstance(call, dict) or not isinstance(call.get("name"), str):
            raise ValueError(f"Agent step {event.id} has no valid tool_call")
        arguments = call.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError(f"Agent step {event.id} arguments must be an object")
        arguments = _resolve(arguments, outputs)
        if isinstance(fix, dict):
            arguments.update(fix.get("arguments", {}))
        output = _execute_tool(call["name"], arguments, state)
        outputs[event.id] = output
        executed_tools.append(call["name"])

    return {
        "task_id": spec["task_id"],
        "state": state,
        "executed_tools": executed_tools,
        "failure_signature": _failure_signature(spec, state),
        "expected_failure_signature": spec["expected_failure_signature"],
    }


def run_task_fixed(events: list[Event]) -> dict[str, Any]:
    """Apply the task fixture's explicit corrective intervention."""
    return run_task(events, apply_fix=True)


def is_original_task_failure(result: dict[str, Any]) -> bool:
    """Match the exact task-state failure signature recorded in the fixture."""
    return (
        isinstance(result, dict)
        and isinstance(result.get("expected_failure_signature"), str)
        and result.get("failure_signature") == result.get("expected_failure_signature")
    )
