"""Path-based trace redaction and payload-free summaries."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .cassette import FORMAT as CASSETTE_FORMAT, LEGACY_FORMAT as LEGACY_CASSETTE_FORMAT

REDACTED = "[REDACTED]"
REDACTION_FORMAT = "chrolo.redaction.v1"
FILTER_FORMAT = "chrolo.filtering.v1"
SECRET_SCAN_FORMAT = "chrolo.secret-scan.v1"
_SECRET_KEY = re.compile(
    r"(?i)(?:token|secret|password|passwd|api[_-]?key|authorization|credential|private[_-]?key|access[_-]?key)"
)
_SECRET_VALUE_PATTERNS = (
    ("bearer-token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{12,}", re.IGNORECASE)),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("openai-key-like", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("jwt-like", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("private-key-header", re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")),
)


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Trace must contain only JSON-compatible data") from exc


def _decode_pointer_token(token: str) -> str:
    if re.search(r"~(?![01])", token):
        raise ValueError(f"Invalid JSON Pointer escape in token {token!r}")
    return token.replace("~1", "/").replace("~0", "~")


def _encode_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _tokens(pointer: str) -> list[str]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("Redaction paths must be absolute JSON Pointers starting with '/'")
    return [_decode_pointer_token(token) for token in pointer[1:].split("/")]


def _apply(node: Any, tokens: list[str], position: int, concrete: str) -> tuple[Any, list[str]]:
    if position == len(tokens):
        return REDACTED, [concrete or "/"]

    token = tokens[position]
    matched: list[str] = []
    if isinstance(node, Mapping):
        result = dict(node)
        keys = sorted(node) if token == "*" else ([token] if token in node else [])
        for key in keys:
            child_path = f"{concrete}/{_encode_pointer_token(str(key))}"
            result[key], paths = _apply(node[key], tokens, position + 1, child_path)
            matched.extend(paths)
        return result, matched

    if isinstance(node, list):
        result = list(node)
        if token == "*":
            indexes = range(len(node))
        elif re.fullmatch(r"0|[1-9][0-9]*", token) and int(token) < len(node):
            indexes = [int(token)]
        else:
            indexes = []
        for index in indexes:
            child_path = f"{concrete}/{index}"
            result[index], paths = _apply(node[index], tokens, position + 1, child_path)
            matched.extend(paths)
        return result, matched

    return node, matched


def redact_trace(trace: Mapping[str, Any], paths: Sequence[str]) -> dict[str, Any]:
    """Return a copy with requested JSON Pointer fields replaced.

    A ``*`` path token matches each key or list element at that level. Every
    configured path must match at least one field. Any redaction marks the
    resulting trace non-replayable because inputs, requests, or responses may
    have changed in ways strict replay cannot safely infer.
    """
    if not isinstance(trace, Mapping):
        raise ValueError("Trace must be a JSON object")
    if not isinstance(paths, Sequence) or isinstance(paths, (str, bytes, bytearray)) or not paths:
        raise ValueError("Provide one or more redaction paths")

    redacted = _json_copy(trace)
    if not isinstance(redacted, dict):
        raise ValueError("Trace must be a JSON object")
    normalized: list[tuple[str, list[str]]] = []
    for path in paths:
        _tokens(path)  # validate syntax before applying any edits
        normalized.append((path, _tokens(path)))

    matched_fields: set[str] = set()
    for path, path_tokens in normalized:
        redacted, matches = _apply(redacted, path_tokens, 0, "")
        if not matches:
            raise ValueError(f"Redaction path matched no fields: {path}")
        matched_fields.update(matches)

    report = {
        "format": REDACTION_FORMAT,
        "paths": sorted(set(path for path, _ in normalized)),
        "field_count": len(matched_fields),
        "fields": sorted(matched_fields),
        "replayable": False,
        "reason": "Redacted data may affect inputs, requests, or responses required for deterministic replay.",
    }
    redacted["redaction"] = report
    cassette = redacted.get("interactions")
    cassette_fields = sorted(
        "/" + path.split("/", 2)[2]
        for path in matched_fields
        if path.startswith("/interactions/")
    )
    if isinstance(cassette, dict) and cassette.get("format") in {CASSETTE_FORMAT, LEGACY_CASSETTE_FORMAT} and cassette_fields:
        cassette["redaction"] = {
            **report,
            "paths": sorted(
                "/" + path.split("/", 2)[2]
                for path in report["paths"]
                if path.startswith("/interactions/")
            ),
            "fields": cassette_fields,
            "field_count": len(cassette_fields),
        }
    return redacted


def _collect_paths(node: Any, tokens: list[str], pos: int = 0, path: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    if pos == len(tokens):
        return {path}
    token = tokens[pos]
    found: set[tuple[str, ...]] = set()
    if isinstance(node, Mapping):
        keys = sorted(node) if token == "*" else ([token] if token in node else [])
        for key in keys:
            found.update(_collect_paths(node[key], tokens, pos + 1, path + (str(key),)))
    elif isinstance(node, list):
        if token == "*":
            indexes = range(len(node))
        elif re.fullmatch(r"0|[1-9][0-9]*", token) and int(token) < len(node):
            indexes = [int(token)]
        else:
            indexes = []
        for index in indexes:
            found.update(_collect_paths(node[index], tokens, pos + 1, path + (str(index),)))
    return found


def _filter_node(
    node: Any,
    path: tuple[str, ...],
    included: set[tuple[str, ...]] | None,
    excluded: set[tuple[str, ...]],
) -> Any:
    if any(path[: len(target)] == target for target in excluded):
        return None
    if included is not None and any(path[: len(target)] == target for target in included) and not any(
        target[: len(path)] == path for target in excluded
    ):
        return node
    if isinstance(node, Mapping):
        result = {}
        for key, value in node.items():
            child = path + (str(key),)
            keep = included is None or any(target[: len(child)] == child for target in included) or any(
                child[: len(target)] == target for target in included
            )
            drop = any(child[: len(target)] == target for target in excluded)
            if keep and not drop:
                result[key] = _filter_node(value, child, included, excluded)
        return result
    if isinstance(node, list):
        result = []
        for index, value in enumerate(node):
            child = path + (str(index),)
            keep = included is None or any(target[: len(child)] == child for target in included) or any(
                child[: len(target)] == target for target in included
            )
            drop = any(child[: len(target)] == target for target in excluded)
            if keep and not drop:
                result.append(_filter_node(value, child, included, excluded))
        return result
    return node


def filter_trace(
    trace: Mapping[str, Any],
    *,
    include_paths: Sequence[str] = (),
    exclude_paths: Sequence[str] = (),
) -> dict[str, Any]:
    """Project a trace onto selected JSON Pointer paths.

    Wildcards match object keys or list indexes. Filtered traces are marked
    non-replayable because removing arbitrary values can alter execution.
    """
    if not isinstance(trace, Mapping):
        raise ValueError("Trace must be a JSON object")
    if "redaction" in trace or "filtering" in trace:
        raise ValueError("Trace has already been sanitized")
    if not include_paths and not exclude_paths:
        raise ValueError("Provide at least one include or exclude path")

    original = _json_copy(trace)
    if not isinstance(original, dict):
        raise ValueError("Trace must be a JSON object")

    def targets(paths: Sequence[str]) -> set[tuple[str, ...]]:
        if isinstance(paths, (str, bytes, bytearray)):
            raise ValueError("Filter paths must be a sequence of JSON Pointers")
        result: set[tuple[str, ...]] = set()
        for pointer in paths:
            tokens = _tokens(pointer)
            matches = _collect_paths(original, tokens)
            if not matches:
                raise ValueError(f"Filter path matched no fields: {pointer}")
            result.update(matches)
        return result

    included = targets(include_paths) if include_paths else None
    excluded = targets(exclude_paths)
    filtered = _filter_node(original, (), included, excluded)
    if not isinstance(filtered, dict):
        raise ValueError("Filtering must preserve a JSON object")
    report = {
        "format": FILTER_FORMAT,
        "include_paths": sorted(set(include_paths)),
        "exclude_paths": sorted(set(exclude_paths)),
        "replayable": False,
        "reason": "Filtered fields may affect replay inputs or interaction matching.",
    }
    filtered["filtering"] = report
    interactions = filtered.get("interactions")
    if isinstance(interactions, dict) and interactions.get("format") in {CASSETTE_FORMAT, LEGACY_CASSETTE_FORMAT}:
        interactions["filtering"] = dict(report)
    return filtered


def scan_trace(trace: Mapping[str, Any]) -> dict[str, Any]:
    """Return likely secret paths without returning any matched values.

    This conservative scan checks sensitive-looking field names and several
    high-signal credential formats. It is advisory, not a guarantee that a
    trace is safe to share.
    """
    if not isinstance(trace, Mapping):
        raise ValueError("Trace must be a JSON object")
    data = _json_copy(trace)
    if not isinstance(data, dict):
        raise ValueError("Trace must be a JSON object")

    findings: dict[str, set[str]] = {}

    def mark(path: str, reason: str) -> None:
        findings.setdefault(path or "/", set()).add(reason)

    def has_content(value: Any) -> bool:
        if value is None or value == "":
            return False
        if isinstance(value, (Mapping, list)):
            return bool(value)
        return True

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for key in sorted(node):
                child_path = f"{path}/{_encode_pointer_token(str(key))}"
                value = node[key]
                if _SECRET_KEY.search(str(key)) and has_content(value):
                    mark(child_path, "sensitive-field-name")
                walk(value, child_path)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}/{index}")
        elif isinstance(node, str):
            for reason, pattern in _SECRET_VALUE_PATTERNS:
                if pattern.search(node):
                    mark(path, reason)

    walk(data, "")
    return {
        "format": SECRET_SCAN_FORMAT,
        "candidate_count": len(findings),
        "findings": [
            {"path": path, "reasons": sorted(reasons)}
            for path, reasons in sorted(findings.items())
        ],
        "note": "Heuristic only. Review candidates and redact as needed; free-text or unsupported secret formats may be missed.",
    }


def summarize_trace(
    trace: Mapping[str, Any], *, trace_size_bytes: int | None = None
) -> dict[str, Any]:
    """Summarize a trace without exposing event, state, request, or response data."""
    if not isinstance(trace, Mapping):
        raise ValueError("Trace must be a JSON object")
    if trace_size_bytes is None:
        try:
            trace_size_bytes = len(json.dumps(trace, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Trace must contain only JSON-compatible data") from exc
    if type(trace_size_bytes) is not int or trace_size_bytes < 0:
        raise ValueError("trace_size_bytes must be a non-negative integer")
    events = trace.get("events", [])
    observations = trace.get("observations", {})
    snapshots = observations.get("state_snapshots", []) if isinstance(observations, Mapping) else []
    container = trace.get("interactions", trace)
    if isinstance(container, Mapping):
        interactions = container.get("interactions", [])
        interaction_format = container.get("format")
        header = container.get("header")
    elif trace.get("format") in {CASSETTE_FORMAT, LEGACY_CASSETTE_FORMAT}:
        interactions = trace.get("interactions", [])
        interaction_format = trace.get("format")
        header = trace.get("header")
    else:
        interactions = []
        interaction_format = None
        header = None
    by_boundary = {"model": 0, "tool": 0}
    if isinstance(interactions, list):
        for item in interactions:
            if isinstance(item, Mapping) and item.get("boundary") in by_boundary:
                by_boundary[item["boundary"]] += 1
    redaction = trace.get("redaction")
    filtering = trace.get("filtering")
    raw_outcome = trace.get("outcome")
    outcome = None
    if isinstance(raw_outcome, Mapping):
        outcome = {
            "status": raw_outcome.get("status") if isinstance(raw_outcome.get("status"), str) else None,
            "exception_type": raw_outcome.get("exception_type")
            if isinstance(raw_outcome.get("exception_type"), str) else None,
        }
    provenance = None
    if isinstance(header, Mapping):
        adapters = header.get("adapters")
        provenance = {
            "chrolo_version": header.get("chrolo_version") if isinstance(header.get("chrolo_version"), str) else None,
            "captured_at": header.get("captured_at") if isinstance(header.get("captured_at"), str) else None,
            "adapters": {
                str(name): str(adapter_version)
                for name, adapter_version in adapters.items()
            } if isinstance(adapters, Mapping) else None,
        }
    return {
        "format": trace.get("format", "unknown"),
        "interaction_format": interaction_format,
        "interaction_provenance": provenance,
        "outcome": outcome,
        "trace_size_bytes": trace_size_bytes,
        "event_count": len(events) if isinstance(events, list) else None,
        "interaction_count": len(interactions) if isinstance(interactions, list) else None,
        "interactions_by_boundary": by_boundary,
        "state_snapshot_count": len(snapshots) if isinstance(snapshots, list) else None,
        "redaction": {
            "active": isinstance(redaction, Mapping),
            "field_count": redaction.get("field_count", 0) if isinstance(redaction, Mapping) else 0,
            "replayable": redaction.get("replayable", True) if isinstance(redaction, Mapping) else True,
        },
        "filtering": {
            "active": isinstance(filtering, Mapping),
            "replayable": filtering.get("replayable", True) if isinstance(filtering, Mapping) else True,
        },
    }
