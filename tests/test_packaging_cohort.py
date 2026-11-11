import json

import pytest

from benchmarks.run_packaging_cohort import evaluate_case, summarize


def _source():
    return {"row": {"instance_id": "sample", "trajectory": [{"tool_calls": [{"function": {
        "name": "execute_bash", "arguments": json.dumps({"command": "true"})}}]}]},
        "task": {"image": "sample", "fail_to_pass": ["target"], "pass_to_pass": ["control"], "test_command": "pytest"}}


@pytest.mark.parametrize("target,control,outcome", [
    ("FAILED", "PASSED", "action_dependent_failure"),
    ("PASSED", "PASSED", "no_target_failure"),
    ("ERROR", "PASSED", "inconclusive"),
    ("SKIPPED", "PASSED", "inconclusive"),
    ("FAILED", "FAILED", "inconclusive"),
])
def test_cohort_separates_failures_passing_runs_and_environment_problems(target, control, outcome):
    class Runner:
        def __init__(self, **kwargs):
            pass

        def __call__(self, events):
            return {"statuses": {"target": target if events else "PASSED", "control": control if events else "PASSED"}}

    result = evaluate_case(_source(), runner_factory=Runner)
    assert result["outcome"] == outcome


def test_cohort_rejects_broken_empty_control_without_executing_actions():
    calls = []

    class Runner:
        def __init__(self, **kwargs):
            pass

        def __call__(self, events):
            calls.append(events)
            return {"statuses": {"target": "FAILED", "control": "PASSED"}}

    assert evaluate_case(_source(), runner_factory=Runner)["outcome"] == "empty_control_rejected"
    assert calls == [[]]


def test_cohort_keeps_inconclusive_cases_and_omits_exception_payloads():
    class Runner:
        def __init__(self, **kwargs):
            pass

        def __call__(self, events):
            raise RuntimeError("private-trace-value")

    result = evaluate_case(_source(), runner_factory=Runner)
    report = summarize([result, {"outcome": "no_target_failure"}])
    assert report["evaluated"] == 2
    assert report["counts"]["inconclusive"] == 1
    assert report["counts"]["no_target_failure"] == 1
    assert "private-trace-value" not in json.dumps(report)
