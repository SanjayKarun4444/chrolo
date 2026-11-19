import ast
import copy
import json
import subprocess
from types import ModuleType, SimpleNamespace
import sys

import pytest

from benchmarks.build_pyfilesystem_entrypoint_input import INSTANCE, ORACLE_PATH, ORACLE_SOURCE, build_entrypoint_input


def fixture_sources():
    trace = {"instance_id": INSTANCE, "repo": "PyFilesystem/pyfilesystem2", "trajectory": [
        {"tool_calls": [{"function": {"name": "execute_bash", "arguments": json.dumps({"command": "true"})}}]}]}
    task = {"instance_id": INSTANCE, "docker_image": "example/pyfilesystem-task",
            "FAIL_TO_PASS": ["original_issue"], "PASS_TO_PASS": ["existing_control"],
            "PASS_TO_FAIL": ["another_previously_passing", "existing_control"],
            "patch": "official issue patch", "test_patch": "original evaluation patch\n",
            "install_config": {"log_parser": "parse_log_pytest", "test_cmd": "pytest -rA"}}
    return trace, task


def test_builder_preserves_trace_and_existing_tests_but_uses_independent_signature():
    trace, task = fixture_sources()
    before = copy.deepcopy((trace, task))
    result = build_entrypoint_input(trace, task, image=task["docker_image"] + "@sha256:" + "a" * 64)
    assert (trace, task) == before
    assert result["row"]["trajectory"] == trace["trajectory"]
    assert result["task"]["test_patch"].startswith(task["test_patch"])
    assert len(result["task"]["fail_to_pass"]) == 2
    assert result["task"]["pass_to_pass"] == ["existing_control", "another_previously_passing", ORACLE_PATH + "::test_healthy_installed_opener_control"]
    assert "reference_patch" not in result["task"]
    assert result["evaluation"]["validation_status"] == "pending_task_image_and_full_action_replay"
    assert result["evaluation"]["requires_empty_action_control"]
    assert "original_issue" not in result["task"]["test_command"]
    # OpenHandsDockerRunner appends the selected targets/controls itself.
    assert result["task"]["test_command"] == task["install_config"]["test_cmd"]
    assert result["task"]["environment"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert result["task"]["environment"]["PYTHONHASHSEED"] == "0"
    # Task metadata requests Python 3.6; retain syntax compatibility even when
    # the downloaded image's actual interpreter is newer.
    ast.parse(ORACLE_SOURCE, feature_version=(3, 6))


@pytest.mark.parametrize("mutation", ["wrong_task", "wrong_repo", "no_trace", "tag", "wrong_image", "existing_oracle"])
def test_builder_rejects_inputs_that_cannot_establish_task_or_trace_identity(mutation):
    trace, task = fixture_sources()
    image = task["docker_image"] + "@sha256:" + "a" * 64
    if mutation == "wrong_task":
        task["instance_id"] = "other__repo-1"
    elif mutation == "wrong_repo":
        trace["repo"] = "other/repo"
    elif mutation == "no_trace":
        trace["trajectory"] = []
    elif mutation == "tag":
        image = task["docker_image"] + ":latest"
    elif mutation == "wrong_image":
        image = "other/image@sha256:" + "a" * 64
    else:
        task["test_patch"] += ORACLE_PATH
    with pytest.raises(ValueError):
        build_entrypoint_input(trace, task, image=image)


def oracle_namespace(monkeypatch, mode):
    """Synthetic contract validation only; this is not a PyFilesystem checkout."""
    module = ModuleType("fs.opener.registry")

    class Opener:
        pass

    class EntryPointError(Exception):
        pass

    class BuiltinOpener(Opener):
        pass

    class Registry:
        def get_opener(self, protocol):
            if mode == "no_lookup":
                return BuiltinOpener()
            entry = next(module.pkg_resources.iter_entry_points("fs.opener", protocol))
            try:
                return entry.load()()
            except RuntimeError as marker:
                if mode == "suppressed":
                    return BuiltinOpener()
                if mode == "invalid_result":
                    return object()
                if mode == "different_exception":
                    raise NameError("unrelated")
                if mode == "wrong_cause":
                    raise EntryPointError("wrapped") from RuntimeError("other")
                raise EntryPointError("wrapped") from marker

    module.Opener = Opener
    module.EntryPointError = EntryPointError
    module.Registry = Registry
    module.pkg_resources = SimpleNamespace(iter_entry_points=lambda *args: iter([]))
    monkeypatch.setitem(sys.modules, module.__name__, module)
    namespace = {}
    exec(compile(ORACLE_SOURCE, "<entrypoint-oracle>", "exec"), namespace)
    return namespace


@pytest.mark.parametrize("stage", ["load", "construct"])
@pytest.mark.parametrize("mode", ["preserved", "suppressed", "no_lookup", "invalid_result", "different_exception", "wrong_cause"])
def test_oracle_only_accepts_the_specific_error_suppression(monkeypatch, stage, mode):
    namespace = oracle_namespace(monkeypatch, mode)
    test = namespace["test_installed_opener_error_is_preserved"]
    if mode == "preserved":
        test(monkeypatch, stage)
    elif mode == "suppressed":
        with pytest.raises(pytest.fail.Exception, match="CHROLO_INSTALLED_OPENER_ERROR_SUPPRESSED"):
            test(monkeypatch, stage)
    else:
        with pytest.raises(pytest.skip.Exception, match="inconclusive"):
            test(monkeypatch, stage)


def test_healthy_fixture_uses_installed_plugin_and_creates_fresh_instances(monkeypatch):
    namespace = oracle_namespace(monkeypatch, "preserved")
    namespace["test_healthy_installed_opener_control"](monkeypatch)


def test_generated_oracle_patch_applies_as_a_new_test_file(tmp_path):
    trace, task = fixture_sources()
    task["test_patch"] = ""
    result = build_entrypoint_input(trace, task, image=task["docker_image"] + "@sha256:" + "a" * 64)
    completed = subprocess.run(["git", "apply", "-"], input=result["task"]["test_patch"],
                               cwd=tmp_path, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / ORACLE_PATH).read_text() == ORACLE_SOURCE
