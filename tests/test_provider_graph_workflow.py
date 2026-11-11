import copy
import json
import importlib
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


@pytest.fixture(params=["openai", "openai_chat", "anthropic"])
def workflow(monkeypatch, request):
    pytest.importorskip("langgraph")
    pytest.importorskip("openai" if request.param.startswith("openai") else request.param)
    workflow = importlib.import_module("examples." + request.param + "_langgraph_regression")
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
    target = workflow.__name__
    captured = tmp_path / "capture.json"
    minimal = tmp_path / "minimal.json"
    generated = tmp_path / "test_generated.py"
    invoke_cli(monkeypatch, "capture", "--graph", target + ":build_graph", "--input",
               ROOT / "examples/openai_langgraph_input.json", "--record-interactions", "--output", captured)
    saved = json.loads(captured.read_text())
    calls = saved["interactions"]["interactions"]
    assert [call["boundary"] for call in calls] == ["tool", "model", "tool"]
    model_result = calls[1]["response"]
    if "anthropic" in target:
        block = model_result["response"]["content"][1]
        assert block == {"type": "tool_use", "id": "toolu_fixture", "name": "fetch_ticket", "input": {"ticket_id": 42}}
        assert model_result["output_text"] == "Fetching ticket."
    elif "openai_chat" in target:
        block = model_result["response"]["choices"][0]["message"]["tool_calls"][0]
        assert block["type"] == "function" and block["id"] == "lookup_42"
        assert block["function"]["name"] == "fetch_ticket"
        assert json.loads(block["function"]["arguments"]) == {"ticket_id": 42}
        assert model_result["output_text"] is None
    else:
        block = model_result["response"]["output"][0]
        assert block["type"] == "function_call" and block["call_id"] == "lookup_42"
        assert json.loads(block["arguments"]) == {"ticket_id": 42}
    assert workflow.CALLS == {"record_http": 1, "record_tool": 2, "replay_http": 0, "replay_tool": 0}
    recorded_counts = workflow.CALLS.copy()
    invoke_cli(monkeypatch, "replay", captured, "--oracle", target + ":original_failure", "--require-failure")
    invoke_cli(monkeypatch, "minimize", captured, "--oracle", target + ":original_failure",
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
    reduced["graph"] = target + ":build_fixed_graph"
    minimal.write_text(json.dumps(reduced), encoding="utf-8")
    regression()
    fixed = subprocess.run([sys.executable, "-m", "pytest", "-q", str(generated)], cwd=ROOT, capture_output=True, text=True)
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert workflow.CALLS == recorded_counts


@pytest.mark.parametrize("failure", ["provider", "tool"])
def test_sdk_graph_recorded_errors_replay_the_same_class_without_callbacks(workflow, monkeypatch, failure):
    target = workflow.__name__
    sdk = "anthropic" if "anthropic" in target else "openai"
    exception_type = sdk + ".BadRequestError" if failure == "provider" else "builtins.RuntimeError"
    monkeypatch.setattr(workflow, "FIXTURE_ERROR", failure)
    source = json.loads((ROOT / "examples/openai_langgraph_input.json").read_text())
    capture = capture_graph(workflow.build_graph, source["initial_state"], parse_events(source["events"]),
                            graph_target=target + ":build_graph", record_interactions=True, capture_exceptions=True)
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
                            graph_target=workflow.__name__ + ":build_graph", record_interactions=True)
    recorded_counts = workflow.CALLS.copy()
    calls = capture["interactions"]["interactions"]
    if mutation == "mismatch":
        if "anthropic" in workflow.__name__ or "openai_chat" in workflow.__name__:
            calls[1]["request"]["messages"][0]["content"] = "different request"
        else:
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


def test_provider_recording_cannot_be_replayed_through_a_different_provider(workflow):
    source = json.loads((ROOT / "examples/openai_langgraph_input.json").read_text())
    capture = capture_graph(workflow.build_graph, source["initial_state"], parse_events(source["events"]),
                            graph_target=workflow.__name__ + ":build_graph", record_interactions=True)
    recorded_counts = workflow.CALLS.copy()
    for other_name in ["openai", "openai_chat", "anthropic"]:
        if workflow.__name__ == "examples." + other_name + "_langgraph_regression":
            continue
        pytest.importorskip("openai" if other_name.startswith("openai") else other_name)
        other = importlib.import_module("examples." + other_name + "_langgraph_regression")
        other.reset_calls()
        capture["graph"] = other.__name__ + ":build_graph"
        with pytest.raises(ReplayMismatch):
            replay_recorded(capture)
        assert workflow.CALLS == recorded_counts
        assert not any(other.CALLS.values())
