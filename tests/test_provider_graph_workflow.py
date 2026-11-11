import copy
import json
import runpy
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from chrolo import cli
from chrolo.adapters_langgraph import capture_graph, replay_recorded
from chrolo.cassette import ReplayMismatch
from chrolo.core import parse_events

ROOT = Path(__file__).resolve().parents[1]
TARGET = "examples.openai_langgraph_regression"


@pytest.fixture
def workflow(monkeypatch):
    pytest.importorskip("langgraph")
    pytest.importorskip("openai")
    from examples import openai_langgraph_regression as workflow
    monkeypatch.setenv("LANGSMITH_TRACING", "false")

    def forbid_network(*args, **kwargs):
        raise AssertionError("Network connections are forbidden in this fixture workflow")

    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    workflow.reset_calls()
    return workflow


def invoke_cli(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["chrolo", *map(str, args)])
    cli.main()


def test_sdk_graph_cli_to_generated_regression_has_no_replay_callbacks(tmp_path, monkeypatch, workflow):
    captured = tmp_path / "capture.json"
    minimal = tmp_path / "minimal.json"
    generated = tmp_path / "test_generated.py"
    invoke_cli(monkeypatch, "capture", "--graph", TARGET + ":build_graph", "--input",
               ROOT / "examples/openai_langgraph_input.json", "--record-interactions", "--output", captured)
    saved = json.loads(captured.read_text())
    calls = saved["interactions"]["interactions"]
    assert [call["boundary"] for call in calls] == ["tool", "model", "tool"]
    assert calls[1]["response"]["response"]["output"][0]["type"] == "function_call"
    assert workflow.CALLS == {"record_http": 1, "record_tool": 2, "replay_http": 0, "replay_tool": 0}
    recorded_counts = workflow.CALLS.copy()
    invoke_cli(monkeypatch, "replay", captured, "--oracle", TARGET + ":original_failure", "--require-failure")
    invoke_cli(monkeypatch, "minimize", captured, "--oracle", TARGET + ":original_failure",
               "--output", minimal, "--test", generated)
    reduced = json.loads(minimal.read_text())
    assert [event["id"] for event in reduced["events"]] == ["select"]
    assert [call["boundary"] for call in reduced["interactions"]["interactions"]] == ["model", "tool"]
    assert workflow.original_failure(replay_recorded(reduced))
    regression = runpy.run_path(str(generated))["test_original_failure_does_not_recur"]
    with pytest.raises(AssertionError, match="Original failure recurred"):
        regression()
    broken = subprocess.run([sys.executable, "-m", "pytest", "-q", str(generated)], cwd=ROOT, capture_output=True, text=True)
    assert broken.returncode == 1 and "Original failure recurred" in broken.stdout
    reduced["graph"] = TARGET + ":build_fixed_graph"
    minimal.write_text(json.dumps(reduced), encoding="utf-8")
    regression()
    fixed = subprocess.run([sys.executable, "-m", "pytest", "-q", str(generated)], cwd=ROOT, capture_output=True, text=True)
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert workflow.CALLS == recorded_counts


@pytest.mark.parametrize("failure,exception_type", [("provider", "openai.BadRequestError"), ("tool", "builtins.RuntimeError")])
def test_sdk_graph_recorded_errors_replay_the_same_class_without_callbacks(workflow, monkeypatch, failure, exception_type):
    monkeypatch.setattr(workflow, "FIXTURE_ERROR", failure)
    source = json.loads((ROOT / "examples/openai_langgraph_input.json").read_text())
    capture = capture_graph(workflow.build_graph, source["initial_state"], parse_events(source["events"]),
                            graph_target=TARGET + ":build_graph", record_interactions=True, capture_exceptions=True)
    assert capture["outcome"] == {"format": "chrolo.outcome.v1", "status": "exception", "exception_type": exception_type}
    recorded_counts = workflow.CALLS.copy()
    # Removing the fixture's live failure must not change the recorded result.
    monkeypatch.setattr(workflow, "FIXTURE_ERROR", None)
    assert replay_recorded(capture) == {"status": "exception", "exception_type": exception_type}
    assert workflow.CALLS == recorded_counts


@pytest.mark.parametrize("mutation", ["mismatch", "missing", "unused"])
def test_provider_graph_replay_rejects_corrupt_boundaries_without_fallback(workflow, mutation):
    source = json.loads((ROOT / "examples/openai_langgraph_input.json").read_text())
    capture = capture_graph(workflow.build_graph, source["initial_state"], parse_events(source["events"]),
                            graph_target=TARGET + ":build_graph", record_interactions=True)
    recorded_counts = workflow.CALLS.copy()
    calls = capture["interactions"]["interactions"]
    if mutation == "mismatch":
        calls[1]["request"]["input"] = "different request"
    elif mutation == "missing":
        calls.pop()
    else:
        extra = copy.deepcopy(calls[-1])
        extra.update(id="unused-call", sequence=len(calls))
        calls.append(extra)
    with pytest.raises(ReplayMismatch):
        replay_recorded(capture)
    assert workflow.CALLS == recorded_counts
