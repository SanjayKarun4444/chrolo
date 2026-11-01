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
    )
    assert len(captured["observations"]["state_snapshots"]) == 3
    assert wrong_issue(captured["observations"]["final_state"])
    assert validate_capture(captured) == events
    assert wrong_issue(replay_recorded(captured))
    result = minimize(events, lambda e: replay_recorded(captured, e), wrong_issue)
    assert [e.id for e in result.events] == ["search", "cache"]
    assert wrong_issue(replay_recorded(captured, result.events))


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
    command = subprocess.run(
        run_cli + ["minimize", str(capture), "--oracle", "examples.fake_graph:wrong_issue",
                   "--output", str(minimal), "--test", str(generated)],
        cwd=cwd, capture_output=True, text=True
    )
    assert command.returncode == 0, command.stderr
    fixture = json.loads(minimal.read_text())
    assert [e["id"] for e in fixture["events"]] == ["search", "cache"]
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
