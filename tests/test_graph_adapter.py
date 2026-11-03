import json
import subprocess
import sys
from pathlib import Path

import pytest

from chrolo.core import minimize, parse_events
from chrolo.adapters_langgraph import (
    capture_graph, validate_capture, replay_recorded, GraphHarness
)
from examples.fake_graph import build_graph, wrong_issue

ROOT = Path(__file__).resolve().parents[1]
INPUT = json.loads((ROOT / "examples" / "langgraph_input.json").read_text())


def test_capture_and_minimize_against_graph_protocol():
    events = parse_events(INPUT["events"])
    captured = capture_graph(
        build_graph, INPUT["initial_state"], events,
        graph_target="examples.fake_graph:build_graph",
        include_snapshots=True,
    )
    assert len(captured["observations"]["state_snapshots"]) == 3
    assert wrong_issue(captured["observations"]["final_state"])
    assert validate_capture(captured) == events
    assert wrong_issue(replay_recorded(captured))
    result = minimize(events, lambda e: replay_recorded(captured, e), wrong_issue)
    assert [e.id for e in result.events] == ["search", "cache"]
    assert wrong_issue(replay_recorded(captured, result.events))


def test_capture_omits_snapshots_by_default_but_can_include_them():
    events = parse_events(INPUT["events"])
    captured = capture_graph(
        build_graph, INPUT["initial_state"], events,
        graph_target="examples.fake_graph:build_graph",
    )
    assert "observations" not in captured
    assert wrong_issue(replay_recorded(captured))

    detailed = capture_graph(
        build_graph, INPUT["initial_state"], events,
        graph_target="examples.fake_graph:build_graph",
        include_snapshots=True,
    )
    assert len(detailed["observations"]["state_snapshots"]) == 3


def test_default_capture_uses_invoke_without_requiring_snapshot_streaming():
    calls = {"invoke": 0, "stream": 0}

    class InvokeOnlyGraph:
        def invoke(self, state):
            calls["invoke"] += 1
            return state

        def stream(self, state, stream_mode="values"):
            calls["stream"] += 1
            raise AssertionError("default capture must not stream state snapshots")

    capture = capture_graph(
        lambda: InvokeOnlyGraph(), {}, parse_events(INPUT["events"]),
        graph_target="tests.fake:build_graph",
    )
    assert calls == {"invoke": 1, "stream": 0}
    assert "observations" not in capture


def test_graph_factory_is_fresh_for_every_candidate():
    calls = []
    def factory():
        calls.append(1)
        return build_graph()
    harness = GraphHarness(factory, INPUT["initial_state"])
    events = parse_events(INPUT["events"])
    assert harness(events) == harness(events)
    assert len(calls) == 2


def test_invalid_capture_rejected():
    with pytest.raises(ValueError, match="Unsupported capture"):
        validate_capture({"format": "junk"})
    with pytest.raises(ValueError, match="may not contain"):
        capture_graph(build_graph, {"events": []}, [], graph_target="examples.fake_graph:build_graph")


def test_cli_end_to_end_and_generated_test(tmp_path):
    capture = tmp_path / "trace.json"
    minimal = tmp_path / "minimal.json"
    generated = tmp_path / "test_chrolo_regression.py"
    run_cli = [sys.executable, "-m", "chrolo"]
    cwd = ROOT
    command = subprocess.run(
        run_cli + ["capture", "--graph", "examples.fake_graph:build_graph",
                   "--input", str(ROOT / "examples" / "langgraph_input.json"),
                   "--output", str(capture)], cwd=cwd, capture_output=True, text=True
    )
    assert command.returncode == 0, command.stderr
    saved_capture = json.loads(capture.read_text(encoding="utf-8"))
    assert "observations" not in saved_capture
    command = subprocess.run(
        run_cli + ["replay", str(capture)], cwd=cwd, capture_output=True, text=True,
    )
    assert command.returncode != 0
    assert "--allow-live-side-effects" in command.stderr
    command = subprocess.run(
        run_cli + ["replay", str(capture), "--allow-live-side-effects",
                   "--oracle", "examples.fake_graph:wrong_issue"],
        cwd=cwd, capture_output=True, text=True,
    )
    assert command.returncode == 0, command.stderr
    assert "graph re-execution" in command.stdout
    assert "Original failure detected: yes" in command.stdout
    command = subprocess.run(
        run_cli + ["minimize", str(capture), "--oracle", "examples.fake_graph:wrong_issue",
                   "--output", str(minimal), "--test", str(generated)],
        cwd=cwd, capture_output=True, text=True,
    )
    assert command.returncode != 0
    assert "--allow-live-side-effects" in command.stderr
    command = subprocess.run(
        run_cli + ["minimize", str(capture), "--oracle", "examples.fake_graph:wrong_issue",
                   "--allow-live-side-effects", "--output", str(minimal), "--test", str(generated)],
        cwd=cwd, capture_output=True, text=True
    )
    assert command.returncode == 0, command.stderr
    fixture = json.loads(minimal.read_text())
    assert [e["id"] for e in fixture["events"]] == ["search", "cache"]
    assert fixture["reduction"]["one_minimal_under_tested_cascade_deletions"] is True
    assert fixture["reduction"]["accepted_deletions"]
    assert "oracle_false" in fixture["reduction"]["candidate_rejections"]
    assert "observations" not in fixture
    test_result = subprocess.run([sys.executable, "-m", "pytest", "-q", str(generated)],
                                 cwd=cwd, capture_output=True, text=True)
    assert test_result.returncode != 0, test_result.stdout
    assert "Original failure recurred" in test_result.stdout


def test_actual_langgraph_if_installed():
    pytest.importorskip("langgraph")
    from examples.langgraph_issue_agent import build_graph as real_graph
    from examples.langgraph_issue_agent import wrong_issue as real_oracle
    events = parse_events(INPUT["events"])
    captured = capture_graph(real_graph, INPUT["initial_state"], events,
                             graph_target="examples.langgraph_issue_agent:build_graph")
    assert real_oracle(replay_recorded(captured))
    result = minimize(events, lambda e: replay_recorded(captured, e), real_oracle)
    assert [e.id for e in result.events] == ["search", "cache"]


def test_generated_regression_turns_green_after_fix(tmp_path):
    original = parse_events(INPUT["events"])
    captured = capture_graph(
        build_graph, INPUT["initial_state"], original,
        graph_target="examples.fake_graph:build_graph",
    )
    result = minimize(original, lambda ev: replay_recorded(captured, ev), wrong_issue)
    captured["events"] = [ev.to_dict() for ev in result.events]
    captured["graph"] = "examples.fake_graph:build_fixed_graph"
    assert not wrong_issue(replay_recorded(captured))


def test_generated_regression_detects_real_bug_not_different_failure():
    events = parse_events(INPUT["events"])
    capture = capture_graph(
        build_graph, INPUT["initial_state"], events,
        graph_target="examples.fake_graph:build_graph",
    )
    # Removing required search also removes the dependent stale selection.
    candidate = [ev for ev in events if ev.id not in {"search", "cache"}]
    result = replay_recorded(capture, candidate)
    assert not wrong_issue(result)
    assert result["selected_issue_id"] is None



def test_actual_langgraph_boundary_recording_replay_and_minimization():
    pytest.importorskip("langgraph")
    from examples.langgraph_interaction_agent import (
        LIVE_CALLS, build_graph as interaction_graph, reset_live_calls, wrong_issue as interaction_oracle,
    )

    reset_live_calls()
    events = parse_events(INPUT["events"])
    captured = capture_graph(
        interaction_graph,
        INPUT["initial_state"],
        events,
        graph_target="examples.langgraph_interaction_agent:build_graph",
        record_interactions=True,
    )
    assert [call["boundary"] for call in captured["interactions"]["interactions"]] == ["model", "tool"]
    assert LIVE_CALLS == {"model": 1, "tool": 1}
    assert interaction_oracle(replay_recorded(captured))
    assert LIVE_CALLS == {"model": 1, "tool": 1}

    result = minimize(events, lambda candidate: replay_recorded(captured, candidate), interaction_oracle)
    assert [event.id for event in result.events] == ["search", "cache"]
    assert interaction_oracle(replay_recorded(captured, result.events))
    assert LIVE_CALLS == {"model": 1, "tool": 1}



def test_cli_can_capture_and_minimize_recorded_langgraph_boundaries(tmp_path):
    pytest.importorskip("langgraph")
    capture = tmp_path / "interaction_trace.json"
    minimal = tmp_path / "minimal_interaction_trace.json"
    generated = tmp_path / "test_interaction_regression.py"
    run_cli = [sys.executable, "-m", "chrolo"]

    command = subprocess.run(
        run_cli + [
            "capture", "--graph", "examples.langgraph_interaction_agent:build_graph",
            "--input", str(ROOT / "examples" / "langgraph_input.json"),
            "--record-interactions", "--output", str(capture),
        ], cwd=ROOT, capture_output=True, text=True,
    )
    assert command.returncode == 0, command.stderr
    saved = json.loads(capture.read_text(encoding="utf-8"))
    assert [item["boundary"] for item in saved["interactions"]["interactions"]] == ["model", "tool"]

    replay = subprocess.run(
        run_cli + [
            "replay", str(capture),
            "--oracle", "examples.langgraph_interaction_agent:wrong_issue",
        ], cwd=ROOT, capture_output=True, text=True,
    )
    assert replay.returncode == 0, replay.stderr
    assert "cassette-backed replay" in replay.stdout
    assert "Original failure detected: yes" in replay.stdout

    command = subprocess.run(
        run_cli + [
            "minimize", str(capture),
            "--oracle", "examples.langgraph_interaction_agent:wrong_issue",
            "--output", str(minimal), "--test", str(generated),
        ], cwd=ROOT, capture_output=True, text=True,
    )
    assert command.returncode == 0, command.stderr
    reduced = json.loads(minimal.read_text(encoding="utf-8"))
    assert [event["id"] for event in reduced["events"]] == ["search", "cache"]
    assert reduced["interactions"] == saved["interactions"]


def test_minimize_prunes_calls_skipped_by_reduced_events_and_keeps_strict_replay(tmp_path):
    from examples.interaction_minimization import (
        LIVE_CALLS, build_graph as interaction_graph, original_failure, reset_live_calls,
    )

    capture_path = tmp_path / "interaction_trace.json"
    minimal_path = tmp_path / "interaction_minimal.json"
    generated = tmp_path / "test_interaction_minimal.py"
    source = json.loads(
        (ROOT / "examples" / "interaction_minimization_input.json").read_text(encoding="utf-8")
    )
    events = parse_events(source["events"])
    reset_live_calls()
    captured = capture_graph(
        interaction_graph,
        source["initial_state"], events,
        graph_target="examples.interaction_minimization:build_graph",
        record_interactions=True,
    )
    assert [call["boundary"] for call in captured["interactions"]["interactions"]] == ["tool", "model"]
    assert LIVE_CALLS == {"tool": 1, "model": 1}
    capture_path.write_text(json.dumps(captured), encoding="utf-8")

    too_small = subprocess.run(
        [
            sys.executable, "-m", "chrolo", "minimize", str(capture_path),
            "--oracle", "examples.interaction_minimization:original_failure",
            "--max-checks", "1", "--output", str(tmp_path / "too_small.json"),
        ],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert too_small.returncode == 2
    assert "at least two checks" in too_small.stderr

    minimized = subprocess.run(
        [
            sys.executable, "-m", "chrolo", "minimize", str(capture_path),
            "--oracle", "examples.interaction_minimization:original_failure",
            "--output", str(minimal_path), "--test", str(generated),
        ],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert minimized.returncode == 0, minimized.stderr
    assert "Pruned 2 -> 1 recorded interactions" in minimized.stdout
    reduced = json.loads(minimal_path.read_text(encoding="utf-8"))
    assert [event["id"] for event in reduced["events"]] == ["trigger"]
    assert [call["id"] for call in reduced["interactions"]["interactions"]] == ["call-000002"]
    assert reduced["interactions"]["interactions"][0]["sequence"] == 0
    assert reduced["interaction_reduction"]["removed_interactions"] == [
        {"id": "call-000001", "original_sequence": 0}
    ]
    assert original_failure(replay_recorded(reduced))

    failing_test = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(generated)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert failing_test.returncode != 0
    assert "Original failure recurred" in failing_test.stdout

    reduced["graph"] = "examples.interaction_minimization:build_fixed_graph"
    minimal_path.write_text(json.dumps(reduced), encoding="utf-8")
    fixed_test = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(generated)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert fixed_test.returncode == 0, fixed_test.stdout + fixed_test.stderr
    assert LIVE_CALLS == {"tool": 1, "model": 1}
