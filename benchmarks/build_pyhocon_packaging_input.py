"""Prepare the real PyHOCON cleanup regression with an independent packaging oracle.

Run from the repository root with ``python -m benchmarks.build_pyhocon_packaging_input``.
The original trajectory remains unmodified. The added pytest check measures an
existing package metadata command; it does not inject a faulty agent action.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.build_openhands_input import _read_json, _single_trace_row, build_input


ORACLE_PATH = "tests/test_chrolo_packaging.py"
ORACLE_SOURCE = '''import subprocess
import sys
from pathlib import Path
from pyhocon import ConfigFactory

ROOT = Path(__file__).resolve().parents[1]

def test_package_metadata_command():
    result = subprocess.run([sys.executable, "setup.py", "--name"], cwd=str(ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    assert result.returncode == 0
    assert result.stdout.strip() == "pyhocon"

def test_package_import_control():
    assert ConfigFactory.parse_string("answer = 42")["answer"] == 42

def test_package_source_control():
    assert (ROOT / "pyhocon" / "config_parser.py").is_file()
'''


def build_packaging_input(trace_source: dict, task_row: dict) -> dict:
    """Select a packaging target and the task's previously passing controls."""
    if task_row.get("instance_id") != "chimpler__pyhocon-322":
        raise ValueError("This packaging oracle is specific to chimpler__pyhocon-322")
    source = build_input(trace_source, task_row)
    task = source["task"]
    patch = (
        f"diff --git a/{ORACLE_PATH} b/{ORACLE_PATH}\nnew file mode 100644\n"
        f"--- /dev/null\n+++ b/{ORACLE_PATH}\n"
        f"@@ -0,0 +1,{len(ORACLE_SOURCE.splitlines())} @@\n"
        + "".join("+" + line + "\n" for line in ORACLE_SOURCE.splitlines())
    )
    task["test_patch"] = task.get("test_patch", "") + "\n" + patch
    task["fail_to_pass"] = [ORACLE_PATH + "::test_package_metadata_command"]
    task["pass_to_pass"] = list(dict.fromkeys(
        task_row["PASS_TO_PASS"] + task_row.get("PASS_TO_FAIL", [])
        + [ORACLE_PATH + "::test_package_import_control", ORACLE_PATH + "::test_package_source_control"]
    ))
    task.pop("reference_patch", None)
    task["environment"] = {
        "PATH": "/opt/conda/envs/testbed/bin:/opt/conda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHONPATH": "/testbed",
    }
    source["evaluation"] = {
        "kind": "independent-packaging-regression",
        "description": "The existing setup.py --name command must still succeed and name pyhocon.",
        "original_trajectory_modified": False,
        "requires_empty_action_control": True,
        "reference_issue_patch_is_not_a_packaging_fix": True,
    }
    return source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-json", required=True, type=Path)
    parser.add_argument("--tasks-parquet", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    import pyarrow.parquet as pq
    trace = _read_json(args.trace_json)
    instance_id = _single_trace_row(trace)["instance_id"]
    rows = pq.read_table(args.tasks_parquet, filters=[("instance_id", "=", instance_id)]).to_pylist()
    if len(rows) != 1:
        raise ValueError("Expected exactly one matching task")
    source = build_packaging_input(trace, rows[0])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
    print("Prepared packaging regression input; keep raw action arguments local:", args.output)


if __name__ == "__main__":
    main()
