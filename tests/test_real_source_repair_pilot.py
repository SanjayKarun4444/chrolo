import json

import pytest

from benchmarks.real_source_repair_pilot import RUNNER


@pytest.mark.parametrize("statuses,exit_code,expected", [
    ({"target1": "FAILED", "target2": "FAILED", "control": "PASSED"}, 1, True),
    ({"target1": "PASSED", "target2": "PASSED", "control": "PASSED"}, 0, False),
    ({"target1": "ERROR", "target2": "ERROR", "control": "PASSED"}, 1, None),
    ({"target1": "PASSED", "target2": "FAILED", "control": "PASSED"}, 1, None),
    ({"target1": "PASSED", "target2": "PASSED", "control": "FAILED"}, 1, None),
    ({"target1": "PASSED", "target2": "PASSED", "control": "PASSED"}, 1, None),
    ({"target1": "PASSED", "control": "PASSED"}, 0, None),
])
def test_generated_real_regression_oracle_rejects_false_fixes(tmp_path, statuses, exit_code, expected):
    (tmp_path / "task.json").write_text(json.dumps({"fail_to_pass": ["target1", "target2"], "pass_to_pass": ["control"]}))
    namespace = {"__file__": str(tmp_path / "project_runner.py")}
    exec(compile(RUNNER, "project_runner.py", "exec"), namespace)
    result = {"statuses": statuses, "test_exit_code": exit_code}
    if expected is None:
        with pytest.raises(RuntimeError):
            namespace["original_failure"](result)
    else:
        assert namespace["original_failure"](result) is expected
