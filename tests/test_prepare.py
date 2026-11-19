import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from chrolo import cli
from chrolo.adapters_langgraph import capture_graph
from chrolo.core import parse_events
from examples.pilot_ticket_agent import build_graph

ROOT = Path(__file__).resolve().parents[1]


def test_own_project_capture_to_regression_needs_no_trace_editing(tmp_path):
    project = tmp_path / "developer_project"
    project.mkdir()
    agent = project / "ticket_agent.py"
    agent.write_text((ROOT / "examples/pilot_ticket_agent.py").read_text(), encoding="utf-8")
    (project / "input.json").write_text((ROOT / "examples/pilot_ticket_input.json").read_text(), encoding="utf-8")
    environment = dict(os.environ, PYTHONPATH=str(project), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")

    def run(*arguments):
        return subprocess.run([sys.executable, *arguments], cwd=project, env=environment,
                              capture_output=True, text=True, timeout=60)

    capture = run("-m", "chrolo", "capture", "--graph", "ticket_agent:build_graph", "--input", "input.json",
                  "--record-interactions", "--output", "capture.json")
    assert capture.returncode == 0, capture.stderr
    original_bytes = (project / "capture.json").read_bytes()
    prepared = run("-m", "chrolo", "prepare", "capture.json", "--oracle", "ticket_agent:original_failure", "--output-dir", "repro")
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    summary = json.loads((project / "repro/summary.json").read_text())
    assert summary["complete"] and summary["strict_replay_verified"] and summary["failure_preserved"]
    assert (summary["original_events"], summary["remaining_events"]) == (3, 1)
    assert (summary["original_interactions"], summary["remaining_interactions"]) == (3, 2)
    assert (project / "repro/.gitignore").read_text() == "*\n"
    minimal_bytes = (project / "repro/minimal.json").read_bytes()
    broken = run("-m", "pytest", "-q", "repro/test_regression.py")
    assert broken.returncode == 1 and "Original failure recurred" in broken.stdout
    agent.write_text(agent.read_text().replace("return bool(ticket)", 'return ticket["status"] == "open"'), encoding="utf-8")
    fixed = run("-m", "pytest", "-q", "repro/test_regression.py")
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert (project / "capture.json").read_bytes() == original_bytes
    assert (project / "repro/minimal.json").read_bytes() == minimal_bytes
    rerun = run("-m", "chrolo", "prepare", "capture.json", "--oracle", "ticket_agent:original_failure", "--output-dir", "repro")
    assert rerun.returncode == 2 and "not overwritten" in rerun.stderr
    assert (project / "repro/minimal.json").read_bytes() == minimal_bytes


@pytest.mark.parametrize("invalid", ["no_cassette", "redacted", "duplicate", "unused", "not_failure", "budget"])
def test_prepare_rejects_invalid_inputs_without_a_ready_bundle(tmp_path, invalid):
    source = json.loads((ROOT / "examples/pilot_ticket_input.json").read_text())
    capture = capture_graph(build_graph, {}, parse_events(source["events"]),
                            graph_target="examples.pilot_ticket_agent:build_graph", record_interactions=True)
    budget = 100
    if invalid == "no_cassette":
        capture.pop("interactions")
    elif invalid == "redacted":
        capture["redaction"] = {}
    elif invalid == "duplicate":
        calls = capture["interactions"]["interactions"]
        calls[1]["id"] = calls[0]["id"]
    elif invalid == "unused":
        calls = capture["interactions"]["interactions"]
        calls.append(dict(calls[-1], id="extra", sequence=len(calls)))
    elif invalid == "not_failure":
        capture["graph"] = "examples.pilot_ticket_agent:build_graph"
        capture["interactions"]["interactions"][-1]["response"]["status"] = "open"
    else:
        budget = 1
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(capture), encoding="utf-8")
    output = tmp_path / "bundle"
    with pytest.raises((ValueError, RuntimeError)):
        cli.prepare_cmd(SimpleNamespace(trace=path, output_dir=output,
                                        oracle="examples.pilot_ticket_agent:original_failure", max_checks=budget))
    assert not output.exists()


def test_prepare_leaves_incomplete_status_when_minimization_fails(tmp_path, monkeypatch):
    source = json.loads((ROOT / "examples/pilot_ticket_input.json").read_text())
    capture = capture_graph(build_graph, {}, parse_events(source["events"]),
                            graph_target="examples.pilot_ticket_agent:build_graph", record_interactions=True)
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(capture), encoding="utf-8")
    def fail(*args):
        raise RuntimeError("minimization failed")
    monkeypatch.setattr(cli, "minimize_cmd", fail)
    with pytest.raises(RuntimeError):
        cli.prepare_cmd(SimpleNamespace(trace=path, output_dir=tmp_path / "bundle",
                                        oracle="examples.pilot_ticket_agent:original_failure", max_checks=100))
    assert json.loads((tmp_path / "bundle/summary.json").read_text())["complete"] is False
