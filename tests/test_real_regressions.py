import hashlib
import json
import sys

import pytest

from benchmarks.real_regressions import load_case, run_case


def _fixture(tmp_path):
    action = {"id": "action-0000", "kind": "execute_bash", "payload": {"arguments": {"command": "secret-command"}}, "requires": []}
    source = {"row": {"trajectory": [{"tool_calls": [{"function": {"name": "execute_bash", "arguments": json.dumps(action["payload"]["arguments"])}}]}]},
              "task": {"image": "sample@sha256:" + "a" * 64, "fail_to_pass": ["target"], "pass_to_pass": ["control"], "test_command": "pytest"}}
    entry = {"name": "sample", "input": "input.json", "report": "report.json", "expected_events": ["action-0000"], "remove_for_passing_control": ["action-0000"]}
    entry["fixture_sha256"] = hashlib.sha256(json.dumps({"task": source["task"], "events": [action]}, sort_keys=True).encode()).hexdigest()
    (tmp_path / "input.json").write_text(json.dumps(source))
    (tmp_path / "report.json").write_text(json.dumps({"events": [action]}))
    return entry, source, action


def test_fixture_rejects_modified_actions_before_execution(tmp_path):
    entry, source, action = _fixture(tmp_path)
    assert load_case(entry, tmp_path)["events"][0].id == "action-0000"
    action["payload"]["arguments"]["command"] = "changed"
    (tmp_path / "report.json").write_text(json.dumps({"events": [action]}))
    with pytest.raises(ValueError, match="unchanged ordered subset"):
        load_case(entry, tmp_path)


def test_fixture_rejects_unpinned_image_and_unknown_ablation(tmp_path):
    entry, source, action = _fixture(tmp_path)
    entry["remove_for_passing_control"] = ["unknown"]
    with pytest.raises(ValueError, match="known regression events"):
        load_case(entry, tmp_path)
    entry["remove_for_passing_control"] = ["action-0000"]
    source["task"]["image"] = "sample:latest"
    (tmp_path / "input.json").write_text(json.dumps(source))
    with pytest.raises(ValueError, match="pinned"):
        load_case(entry, tmp_path)


def test_fixture_rejects_changed_evaluation_fingerprint(tmp_path):
    entry, source, action = _fixture(tmp_path)
    source["task"]["test_command"] = "different evaluator"
    (tmp_path / "input.json").write_text(json.dumps(source))
    with pytest.raises(ValueError, match="fingerprint"):
        load_case(entry, tmp_path)


@pytest.mark.parametrize("mode", ["valid", "empty_failed", "target_error", "control_failed", "exception"])
def test_regression_requires_reproduction_and_passing_controls(tmp_path, mode):
    entry, source, action = _fixture(tmp_path)
    case = load_case(entry, tmp_path)
    calls = []

    class Runner:
        def __init__(self, **kwargs):
            pass

        def __call__(self, events):
            calls.append(events)
            if mode == "exception":
                raise RuntimeError("secret exception payload")
            failed = bool(events) or mode == "empty_failed"
            status = "ERROR" if events and mode == "target_error" else "FAILED" if failed else "PASSED"
            return {"statuses": {"target": status, "control": "FAILED" if events and mode == "control_failed" else "PASSED"},
                    "failed_to_pass": ["target"] if failed else [], "passed_to_pass": ["control"]}

    result = run_case(case, repeat=2, runner_factory=Runner)
    assert result["passed"] is (mode == "valid")
    assert "secret" not in json.dumps(result)
    if mode in {"empty_failed", "exception"}:
        assert calls == [[]]
    if mode == "valid":
        assert len(result["checks"]) == 6


@pytest.mark.parametrize("passed,exit_code", [(True, 0), (False, 1)])
def test_command_resets_stale_success_and_returns_gate_status(tmp_path, monkeypatch, passed, exit_code):
    from benchmarks import real_regressions as module
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"format": "chrolo.real-regressions.v1", "cases": [{"name": "sample"}]}))
    output = tmp_path / "result.json"
    output.write_text(json.dumps({"passed": True, "complete": True}))
    monkeypatch.setattr(sys, "argv", ["checks", "--manifest", str(manifest), "--output", str(output)])
    monkeypatch.setattr(module, "load_case", lambda entry: {"entry": entry})

    def run(case, **kwargs):
        assert json.loads(output.read_text())["passed"] is False
        return {"name": "sample", "passed": passed, "checks": []}

    monkeypatch.setattr(module, "run_case", run)
    assert module.main() == exit_code
    assert json.loads(output.read_text())["passed"] is passed


def test_missing_fixture_invalidates_old_report(tmp_path, monkeypatch):
    import sys
    from benchmarks import real_regressions as module
    output = tmp_path / "result.json"
    output.write_text(json.dumps({"passed": True}))
    monkeypatch.setattr(sys, "argv", ["checks", "--manifest", str(tmp_path / "missing.json"), "--output", str(output)])
    assert module.main() == 2
    assert json.loads(output.read_text())["passed"] is False
