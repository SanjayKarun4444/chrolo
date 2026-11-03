import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from chrolo.core import minimize, parse_events
from chrolo.trajectory import infer_dependencies, run_trajectory
from examples.stateful_agent import step, wrong_issue

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = json.loads((ROOT / "examples" / "stateful_scenario.json").read_text(encoding="utf-8"))


def test_inference_keeps_only_directly_verified_read_after_write_edges():
    events = parse_events(SCENARIO["events"])
    inferred = infer_dependencies(SCENARIO["initial_state"], events, step, wrong_issue)

    select = next(event for event in inferred.events if event.id == "select")
    assert select.requires == ("search",)
    assert inferred.verified_edges == [{
        "consumer": "select",
        "requires": "search",
        "state_keys": ["fresh_issue_id"],
        "verified_by_intervention": {
            "kind": "consumer_state_changed",
            "output_keys": ["wrong_issue"],
        },
    }]
    assert wrong_issue(inferred.baseline_result)


def test_inferred_dependencies_preserve_stateful_minimization():
    events = infer_dependencies(
        SCENARIO["initial_state"], parse_events(SCENARIO["events"]), step, wrong_issue
    ).events
    result = minimize(events, lambda candidate: run_trajectory({}, candidate, step), wrong_issue)
    assert [event.id for event in result.events] == ["search", "select"]
    assert wrong_issue(run_trajectory({}, result.events, step))


def test_unrelated_failure_does_not_create_false_consumer_edge():
    events = parse_events([
        {"id": "producer", "kind": "produce", "payload": {}},
        {"id": "reader", "kind": "read", "payload": {}},
    ])

    def transition(state, event):
        if event.id == "producer":
            state["value"] = 1
            state["failure"] = True
        else:
            state.get("value")

    inferred = infer_dependencies({}, events, transition, lambda state: state.get("failure") is True)
    assert inferred.verified_edges == []
    assert all(not event.requires for event in inferred.events)


def test_nested_state_changes_must_be_assigned_back_to_be_tracked():
    state = run_trajectory(
        {"record": {"count": 0}},
        [{"id": "edit", "kind": "edit", "payload": {}}],
        lambda current, _: current["record"].update(count=1),
    )
    assert state == {"record": {"count": 0}}


def test_baseline_oracle_error_is_distinguished():
    with pytest.raises(RuntimeError, match="oracle raised"):
        infer_dependencies(
            {}, parse_events(SCENARIO["events"]), step,
            lambda _: (_ for _ in ()).throw(ValueError("oracle issue")),
        )


def test_cli_infer_then_minimize_stateful_trajectory(tmp_path):
    inferred_path = tmp_path / "inferred.json"
    minimal_path = tmp_path / "minimal.json"
    generated_test = tmp_path / "test_stateful_regression.py"
    cli = [sys.executable, "-m", "chrolo"]
    inferred = subprocess.run(
        cli + [
            "infer-dependencies", str(ROOT / "examples" / "stateful_scenario.json"),
            "--step", "examples.stateful_agent:step",
            "--oracle", "examples.stateful_agent:wrong_issue",
            "--output", str(inferred_path),
        ], cwd=ROOT, capture_output=True, text=True,
    )
    assert inferred.returncode == 0, inferred.stderr
    dependency_fixture = json.loads(inferred_path.read_text(encoding="utf-8"))
    assert dependency_fixture["dependency_inference"]["verified_edge_count"] == 1

    minimized = subprocess.run(
        cli + [
            "minimize", str(inferred_path),
            "--trajectory-step", "examples.stateful_agent:step",
            "--oracle", "examples.stateful_agent:wrong_issue",
            "--output", str(minimal_path), "--test", str(generated_test),
        ], cwd=ROOT, capture_output=True, text=True,
    )
    assert minimized.returncode == 0, minimized.stderr
    output = json.loads(minimal_path.read_text(encoding="utf-8"))
    assert [event["id"] for event in output["events"]] == ["search", "select"]
    generated_source = generated_test.read_text(encoding="utf-8")
    assert "run_trajectory" in generated_source

    vulnerable_test = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(generated_test)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert vulnerable_test.returncode != 0
    assert "Original failure recurred" in vulnerable_test.stdout

    fixed_module = tmp_path / "fixed_stateful_agent.py"
    fixed_module.write_text(
        "from chrolo.core import Event\n"
        "from chrolo.trajectory import TrackedState\n"
        "def step(state: TrackedState, event: Event) -> None:\n"
        "    if event.kind == 'search': state['fresh_issue_id'] = event.payload['issue_id']\n"
        "    elif event.kind == 'select':\n"
        "        fresh = state.get('fresh_issue_id')\n"
        "        state['selected_issue_id'] = fresh\n"
        "        state['wrong_issue'] = False\n",
        encoding="utf-8",
    )
    fixed_test = tmp_path / "test_fixed_stateful_regression.py"
    fixed_test.write_text(
        generated_source.replace("examples.stateful_agent:step", "fixed_stateful_agent:step"),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), str(ROOT), env.get("PYTHONPATH", "")])
    fixed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(fixed_test)],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
