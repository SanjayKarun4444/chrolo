import json
import subprocess
import sys
from pathlib import Path

import pytest

from chrolo.adapters_langgraph import capture_graph
from chrolo.adapters_langgraph import OUTCOME_CAPTURE_FORMAT, validate_capture
from chrolo.adapters_langgraph import PROVENANCE_CAPTURE_FORMAT
from chrolo.core import parse_events
from chrolo.safety import summarize_trace
from examples.failing_graph import build_graph


ROOT = Path(__file__).resolve().parents[1]


def test_exception_capture_is_opt_in_and_preserves_default_behavior():
    events = parse_events([
        {"id": "trigger", "kind": "trigger", "payload": {}},
    ])
    with pytest.raises(RuntimeError, match="sensitive provider text"):
        capture_graph(
            build_graph, {}, events, graph_target="examples.failing_graph:build_graph"
        )


def test_exception_capture_handles_snapshot_stream_failing_before_first_state():
    class FailingStreamGraph:
        def invoke(self, state):
            raise RuntimeError("private stream error")

        def stream(self, state, *, stream_mode):
            raise RuntimeError("private stream error")

    def build_graph():
        return FailingStreamGraph()

    captured = capture_graph(
        build_graph,
        {},
        parse_events([{"id": "trigger", "kind": "trigger", "payload": {}}]),
        graph_target="tests.failing_stream:build_graph",
        include_snapshots=True,
        capture_exceptions=True,
    )

    assert captured["outcome"]["status"] == "exception"
    assert captured["observations"]["state_snapshots"] == []
    assert "final_state" not in captured["observations"]
    assert "private stream error" not in json.dumps(captured)
    validate_capture(captured)


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "chrolo", *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_capture_replay_and_minimize_exception_failure_without_serializing_message(tmp_path):
    capture = tmp_path / "failure.json"
    minimal = tmp_path / "minimal.json"
    generated = tmp_path / "test_failure_regression.py"
    source = ROOT / "examples" / "failing_graph_input.json"

    captured = _cli(
        "capture", "--graph", "examples.failing_graph:build_graph",
        "--input", source, "--capture-exceptions", "--output", capture,
    )
    assert captured.returncode == 0, captured.stderr
    assert "builtins.RuntimeError" in captured.stdout
    trace_text = capture.read_text(encoding="utf-8")
    assert "sensitive provider text" not in trace_text
    saved_trace = json.loads(trace_text)
    assert saved_trace["format"] == OUTCOME_CAPTURE_FORMAT
    assert saved_trace["outcome"] == {
        "format": "chrolo.outcome.v1",
        "status": "exception",
        "exception_type": "builtins.RuntimeError",
    }
    summary = summarize_trace(saved_trace)
    assert summary["outcome"] == {
        "status": "exception",
        "exception_type": "builtins.RuntimeError",
    }
    assert "sensitive provider text" not in json.dumps(summary)
    validate_capture(saved_trace)

    legacy_with_outcome = dict(saved_trace, format="chrolo.langgraph.v1")
    with pytest.raises(ValueError, match="does not support an outcome"):
        validate_capture(legacy_with_outcome)
    missing_outcome = {key: value for key, value in saved_trace.items() if key != "outcome"}
    with pytest.raises(ValueError, match="requires an outcome"):
        validate_capture(missing_outcome)
    message_leak = json.loads(trace_text)
    message_leak["outcome"]["message"] = "private"
    with pytest.raises(ValueError, match="unsupported fields"):
        validate_capture(message_leak)

    blocked_replay = _cli("replay", capture, "--oracle", "examples.failing_graph:original_failure")
    assert blocked_replay.returncode == 2
    assert "--allow-live-side-effects" in blocked_replay.stderr

    replay = _cli(
        "replay", capture, "--allow-live-side-effects",
        "--oracle", "examples.failing_graph:original_failure",
    )
    assert replay.returncode == 0, replay.stderr
    assert "Captured exception observed (details omitted" in replay.stdout
    assert "builtins.RuntimeError" not in replay.stdout
    assert "Original failure detected: yes" in replay.stdout

    minimized = _cli(
        "minimize", capture, "--allow-live-side-effects",
        "--oracle", "examples.failing_graph:original_failure",
        "--output", minimal, "--test", generated,
    )
    assert minimized.returncode == 0, minimized.stderr
    reduced = json.loads(minimal.read_text(encoding="utf-8"))
    assert [event["id"] for event in reduced["events"]] == ["trigger"]
    assert reduced["outcome"]["exception_type"] == "builtins.RuntimeError"
    assert reduced["reduction"]["one_minimal_under_tested_cascade_deletions"] is True

    still_buggy = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(generated)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert still_buggy.returncode != 0
    assert "Original failure recurred" in still_buggy.stdout

    reduced["graph"] = "examples.failing_graph:build_fixed_graph"
    minimal.write_text(json.dumps(reduced), encoding="utf-8")
    fixed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(generated)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr


def test_capture_records_explicit_provenance_and_inspect_hides_fingerprint_values(tmp_path):
    capture = tmp_path / "provenance.json"
    minimal = tmp_path / "provenance-minimal.json"
    source = ROOT / "examples" / "failing_graph_input.json"
    result = _cli(
        "capture", "--graph", "examples.failing_graph:build_graph",
        "--input", source, "--capture-exceptions", "--project-name", "demo",
        "--project-commit", "abc123", "--fingerprint", "python=3.12.1",
        "--fingerprint", "region=test-region", "--output", capture,
    )
    assert result.returncode == 0, result.stderr
    saved = json.loads(capture.read_text(encoding="utf-8"))
    assert saved["format"] == PROVENANCE_CAPTURE_FORMAT
    assert saved["provenance"]["project"] == {"name": "demo", "commit": "abc123"}
    assert saved["provenance"]["environment_fingerprints"] == {
        "python": "3.12.1", "region": "test-region"
    }
    validate_capture(saved)
    inspected = _cli("inspect", capture)
    assert inspected.returncode == 0, inspected.stderr
    summary = json.loads(inspected.stdout)
    assert summary["capture_provenance"]["environment_fingerprints"] == ["python", "region"]
    assert "test-region" not in inspected.stdout

    replay = _cli(
        "replay", capture, "--allow-live-side-effects",
        "--oracle", "examples.failing_graph:original_failure",
    )
    assert replay.returncode == 0, replay.stderr
    minimized = _cli(
        "minimize", capture, "--allow-live-side-effects",
        "--oracle", "examples.failing_graph:original_failure",
        "--output", minimal, "--test", tmp_path / "test_provenance_regression.py",
    )
    assert minimized.returncode == 0, minimized.stderr
    reduced = json.loads(minimal.read_text(encoding="utf-8"))
    assert reduced["format"] == PROVENANCE_CAPTURE_FORMAT
    assert reduced["provenance"] == saved["provenance"]


def test_capture_provenance_validation_rejects_malformed_values():
    from examples.failing_graph import build_graph

    events = parse_events([{"id": "trigger", "kind": "trigger", "payload": {}}])
    captured = capture_graph(
        build_graph, {}, events, graph_target="examples.failing_graph:build_graph",
        capture_exceptions=True, project={"name": "demo"},
    )
    validate_capture(captured)
    malformed = json.loads(json.dumps(captured))
    malformed["provenance"]["environment_fingerprints"] = {"token": 123}
    with pytest.raises(ValueError, match="environment_fingerprints"):
        validate_capture(malformed)
