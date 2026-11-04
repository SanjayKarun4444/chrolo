import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from chrolo.core import parse_events
from chrolo.openhands_docker import (
    OpenHandsDockerRunner,
    _CONTAINER_PROGRAM,
    _RESULT_PATTERN,
    benchmark_openhands_trace,
    failure_oracle,
)


FAIL = "tests/test_issue.py::test_fixed_behavior"
CONTROL = "tests/test_issue.py::test_unrelated_behavior"


def _runner(**overrides):
    options = {
        "image": "example/task:latest",
        "fail_to_pass": [FAIL],
        "pass_to_pass": [CONTROL],
        "test_command": "pytest --no-header -rA --tb=line",
    }
    options.update(overrides)
    return OpenHandsDockerRunner(**options)


def test_docker_invocation_is_fresh_networkless_and_resource_bounded():
    compile(_CONTAINER_PROGRAM, "<openhands-container-runner>", "exec")
    argv = _runner().docker_argv()
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--network=none" in argv
    assert "--memory=4g" in argv
    assert "--cpus=2" in argv
    assert "--pids-limit=256" in argv
    assert "--cap-drop=ALL" in argv
    assert "--security-opt=no-new-privileges" in argv
    assert not any(part.startswith("--mount") for part in argv)
    assert argv[-3:] == ["python", "-c", argv[-1]]


def test_container_editor_matches_openhands_create_unique_replace_insert_and_undo(tmp_path):
    existing = tmp_path / "existing.txt"
    existing.write_text("alpha\nbeta\n", encoding="utf-8")
    duplicate = tmp_path / "duplicate.txt"
    duplicate.write_text("alpha\nalpha\n", encoding="utf-8")
    created = tmp_path / "created.txt"
    mapped = tmp_path / "mapped.txt"
    mapped.write_text("mapped old", encoding="utf-8")
    patch_target = tmp_path / "patchme.txt"
    patch_target.write_text("unfixed\n", encoding="utf-8")
    actions = [
        {"id": "create-existing", "kind": "str_replace_editor", "payload": {"arguments": {
            "command": "create", "path": str(existing), "file_text": "overwrite",
        }}},
        {"id": "replace", "kind": "str_replace_editor", "payload": {"arguments": {
            "command": "str_replace", "path": str(existing), "old_str": "beta", "new_str": "BETA",
        }}},
        {"id": "insert", "kind": "str_replace_editor", "payload": {"arguments": {
            "command": "insert", "path": str(existing), "insert_line": 1, "new_str": "middle",
        }}},
        {"id": "undo-insert", "kind": "str_replace_editor", "payload": {"arguments": {
            "command": "undo_edit", "path": str(existing),
        }}},
        {"id": "undo-replace", "kind": "str_replace_editor", "payload": {"arguments": {
            "command": "undo_edit", "path": str(existing),
        }}},
        {"id": "replace-ambiguous", "kind": "str_replace_editor", "payload": {"arguments": {
            "command": "str_replace", "path": str(duplicate), "old_str": "alpha", "new_str": "changed",
        }}},
        {"id": "create-new", "kind": "str_replace_editor", "payload": {"arguments": {
            "command": "create", "path": str(created), "file_text": "new file",
        }}},
        {"id": "map-workspace", "kind": "str_replace_editor", "payload": {"arguments": {
            "command": "str_replace", "path": "/workspace/repo/mapped.txt", "old_str": "mapped old", "new_str": "mapped new",
        }}},
    ]
    test_command = [sys.executable, "-c", "import sys; print('FAILED '+sys.argv[1]+' - expected'); print('PASSED '+sys.argv[2])"]
    request = {
        "actions": actions,
        "workdir": str(tmp_path),
        "action_timeout": 5,
        "test_timeout": 5,
        "test_command": test_command,
        "fail_to_pass": [FAIL],
        "pass_to_pass": [CONTROL],
        "result_pattern": _RESULT_PATTERN.pattern,
        "source_workspace": "/workspace/repo",
        "reference_patch": (
            "diff --git a/patchme.txt b/patchme.txt\n"
            "--- a/patchme.txt\n"
            "+++ b/patchme.txt\n"
            "@@ -1 +1 @@\n"
            "-unfixed\n"
            "+fixed\n"
        ),
    }
    completed = subprocess.run(
        [sys.executable, "-c", _CONTAINER_PROGRAM],
        input=json.dumps(request),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=True,
    )

    assert existing.read_text(encoding="utf-8") == "alpha\nbeta\n"
    assert duplicate.read_text(encoding="utf-8") == "alpha\nalpha\n"
    assert created.read_text(encoding="utf-8") == "new file"
    assert mapped.read_text(encoding="utf-8") == "mapped new"
    assert patch_target.read_text(encoding="utf-8") == "fixed\n"
    assert json.loads(completed.stdout)["statuses"] == {FAIL: "FAILED", CONTROL: "PASSED"}


def test_runner_sends_only_candidate_actions_and_returns_task_test_statuses(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["request"] = json.loads(kwargs["input"])
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"statuses": {FAIL: "FAILED", CONTROL: "PASSED"}, "test_exit_code": 1}),
            stderr="",
        )

    monkeypatch.setattr("chrolo.openhands_docker.subprocess.run", fake_run)
    events = parse_events([
        {"id": "shell", "kind": "execute_bash", "payload": {"arguments": {"command": "echo hi"}}},
        {"id": "editor", "kind": "str_replace_editor", "payload": {"arguments": {"command": "view", "path": "src/a.py"}}},
    ])

    result = _runner()(events)
    assert [action["id"] for action in seen["request"]["actions"]] == ["shell", "editor"]
    assert seen["request"]["test_command"] == ["pytest", "--no-header", "-rA", "--tb=line"]
    assert result["failed_to_pass"] == [FAIL]
    assert result["passed_to_pass"] == [CONTROL]
    assert "--network=none" in seen["argv"]
    assert "--name" in seen["argv"]


def test_exact_failure_oracle_requires_same_failures_and_all_controls():
    oracle = failure_oracle([FAIL], [CONTROL])
    assert oracle({"failed_to_pass": [FAIL], "passed_to_pass": [CONTROL]})
    assert not oracle({"failed_to_pass": [], "passed_to_pass": [CONTROL]})
    assert not oracle({"failed_to_pass": [FAIL, "extra"], "passed_to_pass": [CONTROL]})
    assert not oracle({"failed_to_pass": [FAIL], "passed_to_pass": []})


def test_runner_removes_container_when_outer_timeout_fires(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "run":
            raise __import__("subprocess").TimeoutExpired(argv, timeout=2)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("chrolo.openhands_docker.subprocess.run", fake_run)
    runner = _runner(total_timeout=2)
    with pytest.raises(RuntimeError, match="exceeded its time limit"):
        runner([])
    assert calls[0][1] == "run"
    assert calls[1][:3] == ["docker", "rm", "--force"]
    assert calls[0][calls[0].index("--name") + 1] in calls[1]


def test_unsupported_editor_operations_fail_before_starting_docker(monkeypatch):
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("Docker must not start for unsupported actions")

    monkeypatch.setattr("chrolo.openhands_docker.subprocess.run", fake_run)
    events = parse_events([{"id": "unknown", "kind": "str_replace_editor", "payload": {
        "arguments": {"command": "unknown", "path": "src/a.py"},
    }}])
    with pytest.raises(ValueError, match="unsupported editor command"):
        _runner()(events)
    assert called is False


def test_benchmark_orchestrates_repetition_exact_oracle_and_minimization():
    class FakeRunner:
        def __init__(self, **kwargs):
            assert kwargs["image"] == "example/task:latest"
            self.fixed = kwargs.get("reference_patch") is not None

        def __call__(self, events):
            broken = not self.fixed and any(event.payload["arguments"].get("marker") == "bug" for event in events)
            return {
                "failed_to_pass": [FAIL] if broken else [],
                "passed_to_pass": [CONTROL],
                "statuses": {FAIL: "FAILED" if broken else "PASSED", CONTROL: "PASSED"},
            }

    def tool_call(name, args):
        return {"function": {"name": name, "arguments": json.dumps(args)}}

    row = {
        "instance_id": "sample__repo-1",
        "repo": "sample/repo",
        "trajectory": [{"tool_calls": [
            tool_call("execute_bash", {"command": "echo noise"}),
            tool_call("execute_bash", {"command": "echo bug", "marker": "bug"}),
            tool_call("execute_bash", {"command": "echo tail"}),
        ]}],
    }
    task = {
        "image": "example/task:latest",
        "fail_to_pass": [FAIL],
        "pass_to_pass": [CONTROL],
        "test_command": "pytest -rA --color=no",
        "reference_patch": "diff --git a/src/a.py b/src/a.py\n",
    }

    report = benchmark_openhands_trace(row, task, repeat=3, runner_factory=FakeRunner)
    assert report["reproduction_rate"] == 1
    assert report["failure_signature_preserved"] is True
    assert report["original_events"] == 3
    assert report["remaining_events"] == 1
    assert report["one_minimal_under_tested_cascade_deletions"] is True
    assert [event["id"] for event in report["events"]] == ["action-0001"]
    assert report["fix_sensitivity_rate"] == 1
    assert report["failure_after_reference_patch"] is False


def test_benchmark_requires_a_baseline_failure_and_passing_controls():
    class PassingRunner:
        def __init__(self, **kwargs):
            pass

        def __call__(self, events):
            return {"failed_to_pass": [], "passed_to_pass": [CONTROL]}

    row = {"trajectory": [{"tool_calls": [{
        "function": {"name": "execute_bash", "arguments": '{"command":"true"}'},
    }]}]}
    task = {
        "image": "example/task:latest",
        "fail_to_pass": [FAIL],
        "pass_to_pass": [CONTROL],
        "test_command": "pytest",
    }
    with pytest.raises(ValueError, match="did not reproduce"):
        benchmark_openhands_trace(row, task, runner_factory=PassingRunner)


@pytest.mark.parametrize("options", [
    {"workdir": "relative"},
    {"fail_to_pass": []},
    {"pass_to_pass": [FAIL]},
    {"action_timeout": 0},
    {"memory": "unlimited"},
])
def test_runner_rejects_unsafe_or_invalid_configuration(options):
    with pytest.raises(ValueError):
        _runner(**options)
