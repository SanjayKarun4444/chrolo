import json
from types import SimpleNamespace

import pytest

from benchmarks.build_openhands_input import build_input
from benchmarks.build_pyhocon_packaging_input import build_packaging_input
from chrolo import cli


def test_packaging_input_preserves_real_actions_and_separates_issue_from_packaging_oracle():
    action = {"function": {"name": "execute_bash", "arguments": json.dumps({"command": "rm -f *.py"})}}
    trace = {"instance_id": "chimpler__pyhocon-322", "trajectory": [{"tool_calls": [action]}]}
    task = {
        "instance_id": trace["instance_id"], "docker_image": "example/task:latest",
        "FAIL_TO_PASS": ["issue_bug"], "PASS_TO_PASS": ["existing_control"],
        "PASS_TO_FAIL": ["previously_passing"], "patch": "issue fix",
        "test_patch": "official evaluation patch\n",
        "install_config": {"log_parser": "parse_log_pytest", "test_cmd": "pytest -rA"},
    }
    result = build_packaging_input(trace, task)
    assert result["row"]["trajectory"] == trace["trajectory"]
    assert "issue_bug" not in result["task"]["fail_to_pass"]
    assert {"existing_control", "previously_passing"} <= set(result["task"]["pass_to_pass"])
    assert "reference_patch" not in result["task"]
    assert result["task"]["test_patch"].startswith("official evaluation patch\n")
    assert "setup.py" in result["task"]["test_patch"]
    assert result["evaluation"]["original_trajectory_modified"] is False


def test_build_input_joins_matching_task_and_allowlists_sensitive_source_fields():
    trace = {"rows": [{"row": {
        "instance_id": "org__repo-7",
        "repo": "org/repo",
        "trajectory": [{"role": "assistant", "tool_calls": [{
            "function": {"name": "str_replace_editor", "arguments": json.dumps({
                "command": "view", "path": "/workspace/org__repo-7/src/a.py",
            })},
        }]}],
        "model_patch": "must not be copied",
        "problem_statement": "must not be copied",
    }}]}
    task = {
        "instance_id": "org__repo-7",
        "docker_image": "swerebench/example:latest",
        "license_name": "BSD-3-Clause",
        "FAIL_TO_PASS": ["test_a"],
        "PASS_TO_PASS": ["test_b"],
        "patch": "diff --git a/src/a.py b/src/a.py\n",
        "test_patch": "diff --git a/tests/test_a.py b/tests/test_a.py\n",
        "install_config": {"log_parser": "parse_log_pytest", "test_cmd": "pytest -rA --color=no"},
    }

    result = build_input(trace, task)
    assert result["row"].keys() == {"instance_id", "repo", "trajectory"}
    assert result["task"] == {
        "image": "swerebench/example:latest",
        "fail_to_pass": ["test_a"],
        "pass_to_pass": ["test_b"],
        "test_command": "pytest -rA --color=no",
        "workdir": "/testbed",
        "source_workspace": "/workspace/org__repo-7",
        "license": "BSD-3-Clause",
        "reference_patch": "diff --git a/src/a.py b/src/a.py\n",
        "test_patch": "diff --git a/tests/test_a.py b/tests/test_a.py\n",
    }


def test_build_input_rejects_a_wrong_instance_join():
    trace = {"instance_id": "org__repo-7", "trajectory": []}
    task = {
        "instance_id": "org__repo-8", "docker_image": "image",
        "FAIL_TO_PASS": ["a"], "PASS_TO_PASS": ["b"],
        "install_config": {"log_parser": "parse_log_pytest", "test_cmd": "pytest"},
    }
    with pytest.raises(ValueError, match="do not match"):
        build_input(trace, task)


def test_benchmark_openhands_cli_writes_report_without_printing_action_payload(tmp_path, monkeypatch, capsys):
    source = {"row": {"instance_id": "case", "trajectory": []}, "task": {"image": "image"}}
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "report.json"
    input_path.write_text(json.dumps(source), encoding="utf-8")
    report = {
        "instance_id": "case", "original_events": 2, "remaining_events": 1,
        "reproduction_rate": 1.0, "failure_signature_preserved": True,
            "events": [{"payload": {"arguments": {"command": "shhTOP"}}}],
    }
    monkeypatch.setattr(cli, "benchmark_openhands_trace", lambda *args, **kwargs: report)

    cli.benchmark_openhands_cmd(SimpleNamespace(
        input=input_path, output=output_path, repeat=5, max_checks=1000, require_action_dependence=True,
    ))

    assert json.loads(output_path.read_text(encoding="utf-8")) == report
    stdout = capsys.readouterr().out
    assert str(output_path) in stdout
    assert "shhTOP" not in stdout
