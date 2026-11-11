"""Build an independent legacy-DSSP compatibility oracle for a real Vermouth trace.

The deterministic executable fixture models the previously used ``-i`` protocol
and returns the repository's existing DSSP output fixture. It is not a DSSP
binary or a validation of protein-analysis accuracy.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.build_openhands_input import _read_json, build_input

ORACLE_PATH = "vermouth/tests/test_chrolo_legacy_dssp.py"
ORACLE_SOURCE = '''import sys
import tempfile
from pathlib import Path
import pytest
import vermouth
from vermouth.dssp import dssp
from vermouth.pdb.pdb import read_pdb
from vermouth.tests.datafiles import PDB_PROTEIN, DSSP_OUTPUT

@pytest.fixture
def legacy_tool_directory():
    with tempfile.TemporaryDirectory(prefix=".chrolo-legacy-", dir=str(Path(__file__).resolve().parents[2])) as directory:
        yield Path(directory)

@pytest.mark.parametrize("savefile", [True, False])
def test_legacy_dssp_protocol(tmp_path, savefile, legacy_tool_directory):
    executable = legacy_tool_directory / "legacy_dssp"
    executable.write_text(
        "#!" + sys.executable + "\\n"
        "import sys\\nfrom pathlib import Path\\n"
        "if len(sys.argv) != 3 or sys.argv[1] != '-i':\\n"
        "    sys.stderr.write('CHROLO_LEGACY_UNSUPPORTED_PROTOCOL\\\\n'); sys.exit(64)\\n"
        "pdb = sys.stdin.read() if sys.argv[2] == '/dev/stdin' else Path(sys.argv[2]).read_text()\\n"
        "if 'ATOM' not in pdb: sys.exit(65)\\n"
        "sys.stdout.write(Path(" + repr(str(DSSP_OUTPUT)) + ").read_text())\\n"
    )
    executable.chmod(0o700)
    system = vermouth.System()
    for molecule in read_pdb(str(PDB_PROTEIN)):
        system.add_molecule(molecule)
    output = tmp_path / "result.dssp" if savefile else None
    expected = dssp.read_dssp2(Path(DSSP_OUTPUT).read_text().splitlines())
    assert expected
    try:
        found = dssp.run_dssp(system, executable=str(executable), savefile=output)
    except dssp.DSSPError as error:
        if "CHROLO_LEGACY_UNSUPPORTED_PROTOCOL" in str(error):
            pytest.fail("Legacy DSSP command protocol was not preserved")
        pytest.skip("Different tool failure; not the target signature")
    except Exception:
        pytest.skip("Different execution failure; not the target signature")
    if found != expected:
        pytest.skip("Different output failure; not the target signature")
    if savefile:
        if not output.is_file() or output.read_text() != Path(DSSP_OUTPUT).read_text():
            pytest.skip("Different savefile failure; not the target signature")
'''


def build_legacy_input(trace_source: dict, task_row: dict) -> dict:
    if task_row.get("instance_id") != "marrink-lab__vermouth-martinize-279":
        raise ValueError("This oracle is specific to marrink-lab__vermouth-martinize-279")
    source = build_input(trace_source, task_row)
    task = source["task"]
    patch = (f"diff --git a/{ORACLE_PATH} b/{ORACLE_PATH}\nnew file mode 100644\n"
             f"--- /dev/null\n+++ b/{ORACLE_PATH}\n@@ -0,0 +1,{len(ORACLE_SOURCE.splitlines())} @@\n"
             + "".join("+" + line + "\n" for line in ORACLE_SOURCE.splitlines()))
    task["test_patch"] = task.get("test_patch", "") + "\n" + patch
    task["fail_to_pass"] = [ORACLE_PATH + "::test_legacy_dssp_protocol[True]", ORACLE_PATH + "::test_legacy_dssp_protocol[False]"]
    task["pass_to_pass"] = list(dict.fromkeys(test for test in task_row["PASS_TO_PASS"] + task_row.get("PASS_TO_FAIL", [])
                                              if test.startswith("vermouth/tests/test_dssp.py::")))
    task.pop("reference_patch", None)
    task["environment"] = {
        "PATH": "/opt/conda/envs/testbed/bin:/opt/conda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHONPATH": "/testbed",
    }
    task["image"] = "swerebench/sweb.eval.x86_64.marrink-lab_1776_vermouth-martinize-279@sha256:741f67ef68d1f287d91f0788385e664d089501ac065b78bdba9dd05014a7cbae"
    source["evaluation"] = {
        "kind": "independent-legacy-tool-compatibility",
        "description": "The existing run_dssp API must retain its legacy -i tool protocol, parsed result, and savefile behavior.",
        "tool_fixture": "Deterministic executable accepting the original -i protocol and emitting the repository DSSP_OUTPUT fixture; not a real legacy DSSP binary.",
        "failure_signature": "Only the fixture's explicit unsupported-protocol marker produces FAILED; unrelated exceptions/output failures produce SKIPPED and are rejected by the benchmark.",
        "original_trajectory_modified": False,
        "requires_empty_action_control": True,
        "control_scope": "DSSP tests only; unrelated molecule-naming tests excluded after one failed on the untouched image.",
    }
    return source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-json", required=True, type=Path)
    parser.add_argument("--tasks-parquet", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    import pyarrow.parquet as pq
    rows = pq.read_table(args.tasks_parquet, filters=[("instance_id", "=", "marrink-lab__vermouth-martinize-279")]).to_pylist()
    if len(rows) != 1:
        raise ValueError("Expected exactly one matching task")
    source = build_legacy_input(_read_json(args.trace_json), rows[0])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
    print("Prepared legacy-tool compatibility input; raw actions remain local:", args.output)


if __name__ == "__main__":
    main()
