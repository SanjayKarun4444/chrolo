"""Chrolo CLI for trusted, local, deterministic failure reproduction."""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
from typing import Any

from .core import Event, minimize, parse_events
from .adapters_langgraph import (
    capture_graph,
    replay_recorded,
    replay_recorded_candidate,
    validate_capture,
    FORMAT,
    OUTCOME_CAPTURE_FORMAT,
    PROVENANCE_CAPTURE_FORMAT,
)
from .cassette import RecordedCallError, ReplayMismatch
from .benchmark import run_benchmarks, run_dataset
from .openhands_docker import benchmark_openhands_trace
from .web import serve as serve_web
from .safety import filter_trace, redact_trace, scan_trace, summarize_trace
from .trajectory import infer_dependencies, run_trajectory


def resolve(target: str):
    if ":" not in target:
        raise ValueError("Provide a Python callable as module:function")
    module, name = target.split(":", 1)
    fn = getattr(importlib.import_module(module), name)
    if not callable(fn):
        raise TypeError(f"Not callable: {target}")
    return fn


def _write_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    """Read one JSON object and turn parse/type errors into useful CLI errors."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path}: file is not valid UTF-8 (byte {exc.start})") from None
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from None
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object at the top level")
    return value


def _read_events(source: dict[str, Any], source_name: str) -> list[Event]:
    if "events" not in source:
        raise ValueError(f"{source_name}: required field 'events' is missing")
    return parse_events(source["events"])


def _parse_fingerprints(entries: list[str]) -> dict[str, str]:
    fingerprints = {}
    for entry in entries:
        name, separator, value = entry.partition("=")
        if not separator or not name.strip() or not value:
            raise ValueError("--fingerprint must use NAME=VALUE with a non-empty name and value")
        name = name.strip()
        if name in fingerprints:
            raise ValueError(f"Duplicate environment fingerprint: {name}")
        fingerprints[name] = value
    return fingerprints


def capture_cmd(args):
    source = _read_json(args.input)
    initial_state = source.get("initial_state", {})
    if not isinstance(initial_state, dict):
        raise ValueError("initial_state must be a JSON object")
    events = _read_events(source, args.input)
    recorded = capture_graph(
        resolve(args.graph), initial_state, events,
        graph_target=args.graph, event_key=args.event_key,
        record_interactions=args.record_interactions,
        include_snapshots=args.include_snapshots,
        capture_exceptions=args.capture_exceptions,
        project={k: v for k, v in (("name", args.project_name), ("commit", args.project_commit)) if v is not None},
        fingerprints=_parse_fingerprints(args.fingerprint),
    )
    if args.include_path or args.exclude_path:
        recorded = filter_trace(
            recorded, include_paths=args.include_path or (), exclude_paths=args.exclude_path or ()
        )
    _write_json(args.output, recorded)
    snapshot_count = len(recorded.get("observations", {}).get("state_snapshots", []))
    print(f"Captured {len(events)} input events and {snapshot_count} graph snapshots")
    interactions = recorded.get("interactions", {}).get("interactions", [])
    if interactions:
        print(f"Recorded {len(interactions)} model/tool interactions")
    outcome = recorded.get("outcome")
    if outcome and outcome.get("status") == "exception":
        print(f"Captured exception outcome: {outcome['exception_type']} (message and stack omitted)")
    if "filtering" in recorded:
        print("Filtered trace is non-replayable")
    print(f"Saved: {args.output}")


def inspect_cmd(args):
    raw = args.trace.read_bytes()
    trace = _read_json(args.trace)
    print(json.dumps(summarize_trace(trace, trace_size_bytes=len(raw)), indent=2, ensure_ascii=False))


def scan_cmd(args):
    report = scan_trace(_read_json(args.trace))
    print(json.dumps(report, indent=2, ensure_ascii=False))


def redact_cmd(args):
    trace = _read_json(args.trace)
    redacted = redact_trace(trace, args.path)
    _write_json(args.output, redacted)
    print(f"Redacted {redacted['redaction']['field_count']} field(s)")
    print(f"Saved non-replayable sanitized trace: {args.output}")


def replay_cmd(args):
    capture = _read_json(args.trace)
    events = validate_capture(capture)
    if getattr(args, "require_failure", False) and not args.oracle:
        raise ValueError("--require-failure needs --oracle")
    cassette = capture.get("interactions")
    if cassette is None and not args.allow_live_side_effects:
        raise ValueError(
            "This capture has no interaction cassette. Replaying it re-executes graph code and may call live services; "
            "pass --allow-live-side-effects to proceed."
        )
    if cassette is None:
        mode = "graph re-execution; live services or side effects may run"
    else:
        mode = "cassette-backed replay; only calls routed through BoundaryContext use saved responses"
    print(f"Replay mode: {mode}")
    try:
        result = replay_recorded(capture, events)
    except (RecordedCallError, ReplayMismatch) as exc:
        if isinstance(exc, RecordedCallError):
            print(
                f"Replay failed at interaction {exc.sequence}: recorded {exc.boundary} error "
                "(details omitted; inspect the local trace if needed)"
            )
        else:
            print(f"Replay failed at interaction {exc.sequence}: {_safe_replay_mismatch_reason(exc.reason)}")
        raise ValueError("Replay did not complete") from None
    print("Replay completed")
    if capture.get("outcome", {}).get("status") == "exception":
        if isinstance(result, dict) and result.get("status") == "exception":
            print("Captured exception observed (details omitted; inspect the local trace if needed)")
        else:
            print("Captured exception did not recur")
    if args.oracle:
        try:
            failed = bool(resolve(args.oracle)(result))
        except Exception:
            raise ValueError("Failure oracle raised while checking replay result") from None
        print(f"Original failure detected: {'yes' if failed else 'no'}")
        if getattr(args, "require_failure", False) and not failed:
            raise ValueError("Original failure did not recur during replay")


def _safe_replay_mismatch_reason(reason: str) -> str:
    """Return a useful mismatch category without echoing trace identity values."""
    if reason.startswith("expected ") and reason.endswith(" boundary"):
        return "boundary differs from the recorded interaction"
    if reason.startswith("expected target "):
        return "target differs from the recorded interaction"
    if reason == "request differs from the recorded request":
        return "request differs from the recorded interaction"
    if reason == "context differs from the recorded context":
        return "context differs from the recorded interaction"
    if reason == "unexpected call; cassette is exhausted":
        return "unexpected call after the cassette was exhausted"
    if reason == "no matching recorded interaction remains in cassette":
        return "no matching recorded interaction remains in the cassette"
    if reason == "ambiguous repeated interaction while minimizing cassette":
        return "repeated interaction is ambiguous during minimization"
    if reason.endswith(" recorded interaction(s) were not consumed"):
        return reason
    return "interaction did not match the recorded cassette"


def benchmark_cmd(args):
    if args.dataset:
        dataset = _read_json(args.dataset)
        report = run_dataset(dataset, repeat=args.repeat)
    else:
        report = run_benchmarks(repeat=args.repeat)
    print(json.dumps(report, indent=2, ensure_ascii=False))


def benchmark_openhands_cmd(args):
    source = _read_json(args.input)
    row = source.get("row")
    task = source.get("task")
    if row is None:
        raise ValueError("input must contain a 'row' OpenHands trajectory object")
    if not isinstance(task, dict):
        raise ValueError("input must contain a 'task' object with image and test metadata")
    report = benchmark_openhands_trace(
        row, task, repeat=args.repeat, max_checks=args.max_checks,
        require_action_dependence=args.require_action_dependence,
    )
    _write_json(args.output, report)
    print(
        f"OpenHands {report.get('instance_id')}: "
        f"{report['original_events']} -> {report['remaining_events']} actions; "
        f"reproduction {report['reproduction_rate']:.0%}; "
        f"failure preserved: {'yes' if report['failure_signature_preserved'] else 'no'}"
    )
    if report.get("fix_sensitivity_rate") is not None:
        print(f"Reference-fix sensitivity: {report['fix_sensitivity_rate']:.0%}")
    print(f"Report and reduced action payloads (may contain secrets): {args.output}")


def serve_cmd(args):
    serve_web(
        args.host, args.port, args.password, args.tls_certfile, args.tls_keyfile,
        advertise_ip=args.advertise_ip,
    )


def infer_dependencies_cmd(args):
    capture = _read_json(args.scenario)
    if capture.get("format") in (FORMAT, OUTCOME_CAPTURE_FORMAT, PROVENANCE_CAPTURE_FORMAT):
        raise ValueError("Dependency inference currently accepts generic event trajectories, not LangGraph captures")
    initial_state = capture.get("initial_state", {})
    if not isinstance(initial_state, dict):
        raise ValueError("initial_state must be a JSON object")
    events = _read_events(capture, args.scenario)
    result = infer_dependencies(
        initial_state, events, resolve(args.step), resolve(args.oracle)
    )
    output = {key: value for key, value in capture.items() if key != "observations"}
    output["events"] = [event.to_dict() for event in result.events]
    output["dependency_inference"] = {
        "format": "chrolo.dependencies.v1",
        "method": "tracked top-level reads/writes with per-edge intervention witnesses",
        "verified_edge_count": len(result.verified_edges),
        "verified_edges": result.verified_edges,
    }
    _write_json(args.output, output)
    print(f"Verified {len(result.verified_edges)} dependency edge(s)")
    print(f"Saved dependency-annotated fixture: {args.output}")


def _generate_test(args, path: Path, *, graph_mode: bool, fixture_path: Path):
    relative = os.path.relpath(fixture_path.resolve(), path.resolve().parent)
    if graph_mode:
        import_code = "from chrolo.adapters_langgraph import replay_recorded\nfrom chrolo.cli import resolve\n"
        execute = "    actual = replay_recorded(capture)\n"
    elif args.trajectory_step:
        import_code = "from chrolo.core import parse_events\nfrom chrolo.trajectory import run_trajectory\nfrom chrolo.cli import resolve\n"
        execute = (
            f"    actual = run_trajectory(capture.get('initial_state', {{}}), "
            f"parse_events(capture['events']), resolve({args.trajectory_step!r}))\n"
        )
    else:
        import_code = "from chrolo.core import parse_events\nfrom chrolo.cli import resolve\n"
        execute = f"    actual = resolve({args.run!r})(parse_events(capture['events']))\n"
    content = (
        '"""Generated by Chrolo. Expected to fail while the original bug remains."""\n'
        "import json\nfrom pathlib import Path\n"
        + import_code
        + "\ndef test_original_failure_does_not_recur():\n"
        + f"    fixture = (Path(__file__).parent / {relative!r}).resolve()\n"
        + "    capture = json.loads(fixture.read_text(encoding='utf-8'))\n"
        + execute
        + f"    assert not resolve({args.oracle!r})(actual), 'Original failure recurred'\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def minimize_cmd(args):
    capture = _read_json(args.scenario)
    graph_mode = capture.get("format") in (FORMAT, OUTCOME_CAPTURE_FORMAT, PROVENANCE_CAPTURE_FORMAT)
    cassette_mode = graph_mode and capture.get("interactions") is not None
    last_candidate_replay: tuple[tuple[str, ...], list[dict[str, Any]]] | None = None
    last_successful_replay: tuple[tuple[str, ...], list[dict[str, Any]]] | None = None
    if graph_mode:
        events = validate_capture(capture)
        if args.run:
            raise ValueError("--run is not used for LangGraph captures; the capture provides --graph")
        if capture.get("interactions") is None and not args.allow_live_side_effects:
            raise ValueError(
                "This input-only capture re-executes graph code for each candidate and may call live services; "
                "pass --allow-live-side-effects to proceed."
            )
        if capture.get("interactions") is not None:
            def run(evts):
                nonlocal last_candidate_replay
                result, used = replay_recorded_candidate(capture, evts)
                last_candidate_replay = (tuple(event.id for event in evts), used)
                return result
        else:
            run = lambda evts: replay_recorded(capture, evts)
    else:
        if args.run and args.trajectory_step:
            raise ValueError("Use either --run or --trajectory-step, not both")
        events = _read_events(capture, args.scenario)
        if args.trajectory_step:
            step = resolve(args.trajectory_step)
            run = lambda evts: run_trajectory(capture.get("initial_state", {}), evts, step)
        elif args.run:
            run = resolve(args.run)
        else:
            raise ValueError("--run or --trajectory-step module:function is required for generic scenarios")
    candidate_budget = args.max_checks
    if cassette_mode:
        if args.max_checks < 2:
            raise ValueError("Cassette-aware minimization needs at least two checks for search and strict verification")
        candidate_budget -= 1  # reserve one check for strict replay of the final minimized trace
    oracle = resolve(args.oracle)
    if cassette_mode:
        user_oracle = oracle

        def oracle(result):
            nonlocal last_successful_replay
            reproduced = bool(user_oracle(result))
            if reproduced and last_candidate_replay is not None:
                last_successful_replay = last_candidate_replay
            return reproduced

    result = minimize(events, run, oracle, max_checks=candidate_budget)
    reduced = {k: v for k, v in capture.items() if k != "observations"}
    reduced["events"] = [e.to_dict() for e in result.events]
    interaction_report = None
    verification_checks = 0
    if graph_mode and capture.get("interactions") is not None:
        final_event_key = tuple(event.id for event in result.events)
        if last_successful_replay is None or last_successful_replay[0] != final_event_key:
            raise RuntimeError("Could not recover the replayed interactions for the minimized fixture")
        cassette = capture["interactions"]
        original_interactions = cassette["interactions"]
        used_interactions = last_successful_replay[1]
        used_ids = {item["id"] for item in used_interactions}
        removed_interactions = [
            {"id": item["id"], "original_sequence": item["sequence"]}
            for item in original_interactions if item["id"] not in used_ids
        ]
        minimized_cassette = dict(cassette)
        minimized_cassette["interactions"] = [
            {**item, "sequence": index}
            for index, item in enumerate(used_interactions)
        ]
        reduced["interactions"] = minimized_cassette
        interaction_report = {
            "original_interactions": len(original_interactions),
            "remaining_interactions": len(used_interactions),
            "removed_interactions": removed_interactions,
            "pruning_rule": "Only exact recorded calls consumed in order by the minimized graph are retained.",
            "final_replay_remains_strict": True,
        }
        try:
            strict_result = replay_recorded(reduced, result.events)
            strict_reproduced = bool(user_oracle(strict_result))
        except Exception:
            strict_reproduced = False
        verification_checks = 1
        if not strict_reproduced:
            raise RuntimeError("Minimized cassette failed strict replay or the original failure oracle")
        interaction_report["strict_verification_passed"] = True
    total_checks = result.checks + verification_checks
    reduced["reduction"] = {
        "original_events": len(events),
        "remaining_events": len(result.events),
        "checks": total_checks,
        "candidate_checks": result.checks,
        "strict_verification_checks": verification_checks,
        "max_checks": args.max_checks,
        "accepted_deletions": result.accepted_deletions,
        "candidate_rejections": result.candidate_rejections,
        "one_minimal_under_tested_cascade_deletions": result.one_minimal,
        "check_budget_exhausted_before_minimality_verification": result.checks >= candidate_budget and not result.one_minimal,
        "guarantee": "failure preserved by supplied oracle; not globally minimum",
    }
    if interaction_report is not None:
        reduced["interaction_reduction"] = interaction_report
    _write_json(args.output, reduced)
    _generate_test(args, args.test, graph_mode=graph_mode, fixture_path=args.output)
    print(f"Reduced {len(events)} -> {len(result.events)} events in {total_checks} checks")
    if interaction_report is not None:
        print(
            "Pruned "
            f"{interaction_report['original_interactions']} -> "
            f"{interaction_report['remaining_interactions']} recorded interactions"
        )
    print(f"1-minimal under tested cascade deletions: {'yes' if result.one_minimal else 'not verified within budget'}")
    print(f"Fixture: {args.output}")
    print(f"Regression test: {args.test} (fails while bug remains)")


def main():
    parser = argparse.ArgumentParser(description="Chrolo: reduce agent failures to regression tests")
    command = parser.add_subparsers(dest="command", required=True)
    cap = command.add_parser("capture", help="Capture a safe, deterministic LangGraph-compatible run")
    cap.add_argument("--graph", required=True, help="Trusted graph factory module:function")
    cap.add_argument("--input", required=True, type=Path, help="JSON with initial_state and events")
    cap.add_argument("--output", default=Path("trace.json"), type=Path)
    cap.add_argument("--event-key", default="events")
    cap.add_argument("--project-name", help="Optional project name to record in provenance")
    cap.add_argument("--project-commit", help="Optional source revision to record in provenance")
    cap.add_argument("--fingerprint", action="append", default=[], metavar="NAME=VALUE", help="Explicit environment fingerprint (repeatable; values are stored verbatim)")
    cap.add_argument(
        "--include-snapshots", action="store_true",
        help="Store full per-step graph state snapshots (may contain sensitive data)",
    )
    cap.add_argument(
        "--capture-exceptions", action="store_true",
        help="Save a failing graph run with its exception class; message and stack are omitted",
    )
    cap.add_argument(
        "--include-path", action="append", default=[],
        help="Keep only fields matching this JSON Pointer (repeatable; filtered output is non-replayable)",
    )
    cap.add_argument(
        "--exclude-path", action="append", default=[],
        help="Remove fields matching this JSON Pointer (repeatable; filtered output is non-replayable)",
    )
    cap.add_argument(
        "--record-interactions", action="store_true",
        help="Pass a BoundaryContext to the graph factory and record wrapped model/tool calls",
    )
    cap.set_defaults(handler=capture_cmd)
    inspect = command.add_parser("inspect", help="Show safe trace metadata without payloads")
    inspect.add_argument("trace", type=Path)
    inspect.set_defaults(handler=inspect_cmd)
    scan = command.add_parser("scan", help="Find likely secret paths without printing their values")
    scan.add_argument("trace", type=Path)
    scan.set_defaults(handler=scan_cmd)
    redact = command.add_parser("redact", help="Redact selected JSON Pointer paths from a trace")
    redact.add_argument("trace", type=Path)
    redact.add_argument("--path", action="append", required=True, help="JSON Pointer; use * to match keys or list entries")
    redact.add_argument("--output", required=True, type=Path)
    redact.set_defaults(handler=redact_cmd)
    replay = command.add_parser("replay", help="Replay a graph capture with explicit execution mode reporting")
    replay.add_argument("trace", type=Path)
    replay.add_argument("--oracle", help="Optional module:function failure predicate")
    replay.add_argument(
        "--allow-live-side-effects", action="store_true",
        help="Allow graph re-execution when no interaction cassette is present",
    )
    replay.add_argument(
        "--require-failure", action="store_true",
        help="Exit with an error when --oracle says the original failure did not recur",
    )
    replay.set_defaults(handler=replay_cmd)
    benchmark = command.add_parser("benchmark", help="Run synthetic replay and minimization reliability benchmarks")
    benchmark.add_argument("--repeat", type=int, default=20, help="Repeated runs per reliability scenario")
    benchmark.add_argument("--dataset", type=Path, help="Evaluate a reviewed chrolo.benchmark.dataset.v1 JSON dataset")
    benchmark.set_defaults(handler=benchmark_cmd)
    openhands_benchmark = command.add_parser(
        "benchmark-openhands",
        help="Replay and minimize one OpenHands trace in fresh isolated Docker task containers",
    )
    openhands_benchmark.add_argument("input", type=Path, help="JSON object with 'row' trajectory and 'task' oracle metadata")
    openhands_benchmark.add_argument("--repeat", type=int, default=5, help="Additional full-trace reproduction runs")
    openhands_benchmark.add_argument("--max-checks", type=int, default=1000, help="Maximum minimizer run/oracle checks")
    openhands_benchmark.add_argument("--require-action-dependence", action="store_true", help="Require every target and control test to pass without agent actions")
    openhands_benchmark.add_argument("--output", type=Path, default=Path("openhands-benchmark.json"))
    openhands_benchmark.set_defaults(handler=benchmark_openhands_cmd)
    serve = command.add_parser("serve", help="Open the responsive local trace inspection and sanitization UI")
    serve.add_argument("--host", default="127.0.0.1", help="Bind address; use 0.0.0.0 for remote network access")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--password", help="Optional Basic auth password; remote binds generate one if omitted")
    serve.add_argument("--tls-certfile", help="PEM TLS certificate chain for HTTPS")
    serve.add_argument("--tls-keyfile", help="PEM TLS private key for HTTPS; provide with --tls-certfile")
    serve.add_argument(
        "--advertise-ip",
        help="Also print this private or VPN IPv4 URL for phone access when auto-discovery misses it",
    )
    serve.set_defaults(handler=serve_cmd)
    infer = command.add_parser("infer-dependencies", help="Infer and verify stateful event dependencies")
    infer.add_argument("scenario", type=Path)
    infer.add_argument("--step", required=True, help="Trusted module:function applying one event to TrackedState")
    infer.add_argument("--oracle", required=True, help="Trusted module:function failure predicate over final state")
    infer.add_argument("--output", default=Path("dependencies.json"), type=Path)
    infer.set_defaults(handler=infer_dependencies_cmd)
    mini = command.add_parser(
        "minimize",
        help="Minimize a reproducible failure and prune recorded calls the reduced graph skips",
    )
    mini.add_argument("scenario", type=Path)
    mini.add_argument("--run", help="module:function runner for generic event fixtures")
    mini.add_argument("--trajectory-step", help="module:function state transition for stateful fixtures")
    mini.add_argument(
        "--allow-live-side-effects", action="store_true",
        help="Allow repeated input-only graph execution when no interaction cassette is present",
    )
    mini.add_argument("--oracle", required=True, help="module:function specific failure predicate")
    mini.add_argument("--output", default=Path("minimal-failure.json"), type=Path)
    mini.add_argument("--test", default=Path("test_chrolo_regression.py"), type=Path)
    mini.add_argument(
        "--max-checks", type=int, default=1000,
        help="Maximum run/oracle checks; cassette mode reserves one for strict final replay",
    )
    mini.set_defaults(handler=minimize_cmd)
    args = parser.parse_args()
    try:
        args.handler(args)
    except (ValueError, TypeError, RuntimeError, ModuleNotFoundError, AttributeError, FileNotFoundError) as exc:
        parser.exit(2, f"chrolo: {exc}\n")


if __name__ == "__main__":
    main()
