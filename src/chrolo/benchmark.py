"""Small deterministic benchmark suite for Chrolo's replay and minimization contracts."""
from __future__ import annotations

import json
import importlib
import time
from typing import Any, Callable

from .cassette import CassetteRecorder, ReplayMismatch, ReplaySession
from .core import Event, minimize, parse_events
from .safety import redact_trace
from .trajectory import run_trajectory


def _naive_minimize(
    events: list[Event], run: Callable[[list[Event]], Any], oracle: Callable[[Any], bool]
) -> tuple[list[Event], int]:
    """Single-pass deletion baseline with no dependency cascade."""
    current = list(events)
    checks = 0
    for event in list(current):
        candidate = [item for item in current if item.id != event.id]
        checks += 1
        try:
            reproduced = bool(oracle(run(candidate)))
        except Exception:
            reproduced = False
        if reproduced:
            current = candidate
    return current, checks


def _measure(
    events: list[Event], run: Callable[[list[Event]], Any], oracle: Callable[[Any], bool], *,
    repeat: int, extra_metrics: dict[str, Any] | None = None,
    fixed_run: Callable[[list[Event]], Any] | None = None,
) -> dict[str, Any]:
    repeated = []
    run_started = time.perf_counter()
    for _ in range(repeat):
        try:
            repeated.append(bool(oracle(run(list(events)))))
        except Exception:
            repeated.append(False)
    replay_ms = (time.perf_counter() - run_started) * 1000 / repeat

    started = time.perf_counter()
    result = minimize(events, run, oracle)
    minimize_ms = (time.perf_counter() - started) * 1000
    try:
        preserved = bool(oracle(run(list(result.events))))
    except Exception:
        preserved = False
    original_size = len(events)
    reduced_size = len(result.events)
    naive_events, naive_checks = _naive_minimize(events, run, oracle)
    naive_size = len(naive_events)
    report = {
        "repetitions": repeat,
        "reproduction_rate": sum(repeated) / repeat,
        "failure_signature_preserved": preserved,
        "original_events": original_size,
        "remaining_events": reduced_size,
        "shrink_percent": round(100 * (original_size - reduced_size) / original_size, 2) if original_size else 0.0,
        "candidate_checks": max(0, result.checks - 1),
        "checks_including_baseline": result.checks,
        "one_minimal_under_tested_cascade_deletions": result.one_minimal,
        "accepted_deletion_steps": len(result.accepted_deletions),
        "candidate_rejections": result.candidate_rejections,
        "naive_single_pass_events": naive_size,
        "naive_shrink_percent": round(100 * (original_size - naive_size) / original_size, 2) if original_size else 0.0,
        "naive_candidate_checks": naive_checks,
        "replay_overhead_ms_per_run": round(replay_ms, 4),
        "minimize_elapsed_ms": round(minimize_ms, 4),
        "fixture_size_bytes": len(json.dumps([event.to_dict() for event in events], separators=(",", ":")).encode("utf-8")),
        "reduced_fixture_size_bytes": len(json.dumps([event.to_dict() for event in result.events], separators=(",", ":")).encode("utf-8")),
        "naive_fixture_size_bytes": len(json.dumps([event.to_dict() for event in naive_events], separators=(",", ":")).encode("utf-8")),
    }
    if fixed_run is not None:
        try:
            fixed_failure = bool(oracle(fixed_run(list(result.events))))
        except Exception:
            fixed_failure = None
        report["failure_after_simulated_fix"] = fixed_failure
        report["regression_sensitivity"] = (
            fixed_failure is False if fixed_failure is not None else None
        )
    if extra_metrics:
        report.update(extra_metrics)
    return report


def run_benchmarks(*, repeat: int = 20) -> dict[str, Any]:
    """Run synthetic contract and reduction benchmarks; make no provider calls."""
    if type(repeat) is not int or repeat < 1:
        raise ValueError("repeat must be a positive integer")

    basic_events = parse_events([
        {"id": "noise-a", "kind": "log", "payload": {"note": "irrelevant"}},
        {"id": "trigger", "kind": "bug", "payload": {"signature": "wrong selection"}},
        {"id": "noise-b", "kind": "log", "payload": {"note": "also irrelevant"}},
        {"id": "done", "kind": "end", "payload": {}},
    ])

    def basic_run(events: list[Event]) -> dict[str, Any]:
        return {"selected": 99 if any(event.id == "trigger" for event in events) else 42}

    oracle = lambda result: result["selected"] == 99
    basic = _measure(basic_events, basic_run, oracle, repeat=repeat)

    live_calls = {"model": 0, "tool": 0}
    recorder = CassetteRecorder()
    recorder.call("tool", "benchmark.diagnostic", {"note": "check"}, lambda: {"ok": True})
    recorder.call("model", "benchmark.model", {"query": "issue 42"}, lambda: (live_calls.__setitem__("model", live_calls["model"] + 1) or {"issue_id": 42}))
    recorder.call("tool", "benchmark.lookup", {"issue_id": 99}, lambda: (live_calls.__setitem__("tool", live_calls["tool"] + 1) or {"issue_id": 99}))
    cassette = recorder.to_dict()
    live_counts_after_recording = dict(live_calls)
    consumed_calls = 0
    used_by_events: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    cassette_events = parse_events([
        {"id": "noise-a", "kind": "diagnostic", "payload": {"note": "check"}},
        {"id": "search", "kind": "search", "payload": {"query": "issue 42"}},
        {"id": "cache", "kind": "cache", "payload": {"issue_id": 99}, "requires": ["search"]},
        {"id": "noise-b", "kind": "log", "payload": {}},
    ])

    def cassette_run(events: list[Event]) -> dict[str, Any]:
        nonlocal consumed_calls
        replay = ReplaySession.from_dict(cassette, allow_skips=True)
        try:
            event_ids = {event.id for event in events}
            if "noise-a" in event_ids:
                replay.call("tool", "benchmark.diagnostic", {"note": "check"})
            search = next((event for event in events if event.id == "search"), None)
            model = None
            if search is not None:
                model = replay.call(
                    "model", "benchmark.model", {"query": search.payload["query"]}
                )
            cache = next((event for event in events if event.id == "cache"), None)
            if cache is not None and model is not None:
                tool = replay.call(
                    "tool", "benchmark.lookup", {"issue_id": cache.payload["issue_id"]}
                )
                selected = tool["issue_id"]
            else:
                selected = model["issue_id"] if model is not None else 42
            replay.finish()
            used_by_events[tuple(event.id for event in events)] = replay.used_interactions
            return {"selected": selected}
        finally:
            consumed_calls += len(replay.used_interactions)

    cassette_result = _measure(
        cassette_events, cassette_run, oracle, repeat=repeat,
        extra_metrics={"cassette_calls_consumed": 0},
    )
    cassette_result["cassette_calls_consumed"] = consumed_calls
    minimized_interactions = used_by_events[("search", "cache")]
    minimized_cassette = {
        **cassette,
        "interactions": [
            {**item, "sequence": index}
            for index, item in enumerate(minimized_interactions)
        ],
    }
    strict_replay = ReplaySession.from_dict(minimized_cassette)
    strict_model = strict_replay.call("model", "benchmark.model", {"query": "issue 42"})
    strict_tool = strict_replay.call("tool", "benchmark.lookup", {"issue_id": 99})
    strict_replay.finish()
    cassette_result["original_interactions"] = len(cassette["interactions"])
    cassette_result["remaining_interactions"] = len(minimized_interactions)
    cassette_result["pruned_interaction_ids"] = [
        item["id"] for item in cassette["interactions"]
        if item["id"] not in {used["id"] for used in minimized_interactions}
    ]
    cassette_result["minimized_cassette_strict_replay_preserved_signature"] = (
        bool(oracle({"selected": strict_tool["issue_id"]}))
        and strict_model["issue_id"] == 42
    )
    cassette_result["live_boundary_calls_during_replay"] = {
        key: live_calls[key] - live_counts_after_recording[key] for key in live_calls
    }


    cassette_result["live_boundary_calls_total"] = dict(live_calls)

    # Candidate executions that raise a different exception must not be accepted.
    exception_events = parse_events([
        {"id": "noise", "kind": "log", "payload": {}},
        {"id": "trigger", "kind": "bug", "payload": {}},
    ])

    def exception_run(events: list[Event]) -> dict[str, bool]:
        if not any(event.id == "trigger" for event in events):
            raise LookupError("unrelated failure")
        return {"original_bug": True}

    exception_result = minimize(exception_events, exception_run, lambda result: result["original_bug"])
    unrelated_exception_rejected = [event.id for event in exception_result.events] == ["trigger"]

    # A request mismatch is rejected and a redacted cassette cannot be replayed.
    mismatch_rejected = False
    try:
        ReplaySession.from_dict(cassette).call("model", "benchmark.model", {"query": "changed"})
    except ReplayMismatch:
        mismatch_rejected = True
    redacted_cassette = redact_trace(cassette, ["/interactions/*/request"])
    redacted_refused = False
    try:
        ReplaySession.from_dict(redacted_cassette)
    except ValueError:
        redacted_refused = True

    # Demonstrate a failure that no cassette can stabilize because the source is unwrapped.
    external_calls = 0

    def unwrapped_nondeterministic_run(_: list[Event]) -> dict[str, bool]:
        nonlocal external_calls
        external_calls += 1
        return {"failure": external_calls % 2 == 0}

    nondeterministic_observations = [
        bool(unwrapped_nondeterministic_run(basic_events)["failure"]) for _ in range(repeat)
    ]
    nondeterminism_rate = sum(nondeterministic_observations) / repeat

    fixed_result = {"selected": 42}  # Simulated corrected implementation.

    return {
        "format": "chrolo.benchmark.v1",
        "synthetic_only": True,
        "provider_calls": 0,
        "repeat": repeat,
        "scenarios": {
            "irrelevant_event_minimization": basic,
            "model_tool_cassette_minimization": cassette_result,
            "safety_and_failure_classification": {
                "changed_request_rejected": mismatch_rejected,
                "unrelated_exception_rejected": unrelated_exception_rejected,
                "redacted_cassette_refused": redacted_refused,
                "redacted_field_count": redacted_cassette["redaction"]["field_count"],
            },
            "unwrapped_nondeterminism": {
                "reproduction_rate": nondeterminism_rate,
                "observed_outcomes": repeat,
                "external_call_count": external_calls,
                "warning": "Calls outside BoundaryContext cannot be made deterministic by cassette replay.",
            },
            "simulated_fix": {
                "original_failure_before_fix": oracle({"selected": 99}),
                "original_failure_after_fix": oracle(fixed_result),
                "regression_test_detects_fix": not oracle(fixed_result),
            },
        },
    }


def _resolve(target: Any, label: str) -> Callable[..., Any]:
    if not isinstance(target, str) or ":" not in target:
        raise ValueError(f"{label} must be a trusted module:function target")
    module_name, function_name = target.split(":", 1)
    if not module_name or not function_name:
        raise ValueError(f"{label} must be a trusted module:function target")
    function = getattr(importlib.import_module(module_name), function_name)
    if not callable(function):
        raise ValueError(f"{label} target is not callable: {target}")
    return function


def run_dataset(dataset: Any, *, repeat: int = 20) -> dict[str, Any]:
    """Evaluate a user-supplied labeled dataset against Chrolo and a naïve baseline.

    Dataset references import trusted Python runner/step and oracle functions.
    They must be reviewed and safe to execute; this function does not sandbox code.
    """
    if type(repeat) is not int or repeat < 1:
        raise ValueError("repeat must be a positive integer")
    if not isinstance(dataset, dict) or dataset.get("format") != "chrolo.benchmark.dataset.v1":
        raise ValueError("Unsupported benchmark dataset; expected chrolo.benchmark.dataset.v1")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Benchmark dataset cases must be a non-empty array")
    metadata = dataset.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("Benchmark dataset metadata must be an object")
    provenance_fields = (
        "source", "source_commit", "python_version", "source_records",
        "included_reproducible_fault_cases", "skipped_controls_or_undetected_faults",
        "comparison_scope", "runner_requires_environment", "detector_evaluation",
        "source_revision", "split", "license", "generator_version", "schema_version",
        "included_execution_divergence_cases", "skipped_clean_records",
        "skipped_failures_without_execution_witness", "included_failure_categories",
        "limitations", "synthetic_only",
    )
    reported_metadata = {key: metadata[key] for key in provenance_fields if key in metadata}

    results = {}
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"Benchmark case at index {index} must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in results:
            raise ValueError(f"Benchmark case at index {index} must have a unique non-empty id")
        events = parse_events(case.get("events"))
        runner_target = case.get("runner")
        step_target = case.get("step")
        if (runner_target is None) == (step_target is None):
            raise ValueError(f"Benchmark case {case_id!r} must specify exactly one of runner or step")
        fixed_runner_target = case.get("fixed_runner")
        fixed_step_target = case.get("fixed_step")
        if fixed_runner_target is not None and fixed_step_target is not None:
            raise ValueError(f"Benchmark case {case_id!r} cannot specify both fixed_runner and fixed_step")
        oracle = _resolve(case.get("oracle"), f"case {case_id} oracle")
        if runner_target is not None:
            run = _resolve(runner_target, f"case {case_id} runner")
        else:
            initial_state = case.get("initial_state", {})
            if not isinstance(initial_state, dict):
                raise ValueError(f"Benchmark case {case_id!r} initial_state must be an object")
            step = _resolve(step_target, f"case {case_id} step")
            run = lambda candidate, initial=initial_state, transition=step: run_trajectory(initial, candidate, transition)
        fixed_run = None
        if fixed_runner_target is not None:
            fixed_run = _resolve(fixed_runner_target, f"case {case_id} fixed_runner")
        elif fixed_step_target is not None:
            if runner_target is not None:
                raise ValueError(f"Benchmark case {case_id!r} with a runner must pair it with fixed_runner")
            fixed_step = _resolve(fixed_step_target, f"case {case_id} fixed_step")
            fixed_initial = case.get("fixed_initial_state", initial_state)
            if not isinstance(fixed_initial, dict):
                raise ValueError(f"Benchmark case {case_id!r} fixed_initial_state must be an object")
            fixed_run = lambda candidate, initial=fixed_initial, transition=fixed_step: run_trajectory(initial, candidate, transition)
        try:
            results[case_id] = _measure(events, run, oracle, repeat=repeat, fixed_run=fixed_run)
        except Exception as exc:
            raise RuntimeError(f"Benchmark case {case_id!r} failed: {type(exc).__name__}") from exc

    return {
        "format": "chrolo.benchmark.results.v1",
        "dataset_format": "chrolo.benchmark.dataset.v1",
        "synthetic_only": metadata.get("synthetic_only") is True,
        "provider_calls": None,
        "dataset_code_is_sandboxed": False,
        "dataset_metadata": reported_metadata,
        "repeat": repeat,
        "aggregate": {
            "case_count": len(results),
            "mean_reproduction_rate": round(sum(item["reproduction_rate"] for item in results.values()) / len(results), 4),
            "signature_preservation_rate": round(sum(bool(item["failure_signature_preserved"]) for item in results.values()) / len(results), 4),
            "mean_shrink_percent": round(sum(item["shrink_percent"] for item in results.values()) / len(results), 2),
            "mean_naive_shrink_percent": round(sum(item["naive_shrink_percent"] for item in results.values()) / len(results), 2),
            "mean_candidate_checks": round(sum(item["candidate_checks"] for item in results.values()) / len(results), 2),
            "mean_naive_candidate_checks": round(sum(item["naive_candidate_checks"] for item in results.values()) / len(results), 2),
            "fix_sensitivity_coverage": sum(
                item.get("regression_sensitivity") is not None for item in results.values()
            ),
            "fix_sensitivity_rate": round(
                sum(bool(item.get("regression_sensitivity")) for item in results.values())
                / max(1, sum(item.get("regression_sensitivity") is not None for item in results.values())), 4
            ),
        },
        "scenarios": results,
    }
