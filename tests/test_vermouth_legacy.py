import sys
from types import ModuleType

import pytest

from benchmarks.build_vermouth_legacy_input import ORACLE_SOURCE, build_legacy_input


def test_legacy_input_preserves_actions_and_documents_fixture_scope():
    trace = {"instance_id": "marrink-lab__vermouth-martinize-279", "trajectory": []}
    task = {
        "instance_id": trace["instance_id"], "docker_image": "example/task",
        "FAIL_TO_PASS": ["original_issue"],
        "PASS_TO_PASS": ["vermouth/tests/test_dssp.py::parser", "unrelated::already_failing"],
        "PASS_TO_FAIL": ["vermouth/tests/test_dssp.py::invalid_input"],
        "test_patch": "official test patch\n", "patch": "issue patch",
        "install_config": {"log_parser": "parse_log_pytest", "test_cmd": "pytest -rA"},
    }
    source = build_legacy_input(trace, task)
    assert source["row"]["trajectory"] == trace["trajectory"]
    assert len(source["task"]["fail_to_pass"]) == 2
    assert source["task"]["pass_to_pass"] == ["vermouth/tests/test_dssp.py::parser", "vermouth/tests/test_dssp.py::invalid_input"]
    assert "reference_patch" not in source["task"]
    assert "@sha256:" in source["task"]["image"]
    assert source["task"]["test_patch"].startswith("official test patch\n")
    assert "not a real legacy DSSP binary" in source["evaluation"]["tool_fixture"]


@pytest.mark.parametrize("mode", ["passed", "protocol_failure", "different_tool_error", "name_error", "wrong_output"])
def test_legacy_oracle_accepts_only_the_specific_protocol_failure(tmp_path, monkeypatch, mode):
    class DSSPError(Exception):
        pass

    class System:
        def add_molecule(self, molecule):
            pass

    def run_dssp(*args, **kwargs):
        if mode == "protocol_failure":
            raise DSSPError("CHROLO_LEGACY_UNSUPPORTED_PROTOCOL")
        if mode == "different_tool_error":
            raise DSSPError("unrelated failure")
        if mode == "name_error":
            raise NameError("missing import")
        return ["wrong"] if mode == "wrong_output" else ["H"]

    names = ["vermouth", "vermouth.dssp", "vermouth.dssp.dssp", "vermouth.pdb", "vermouth.pdb.pdb", "vermouth.tests", "vermouth.tests.datafiles"]
    modules = {name: ModuleType(name) for name in names}
    modules["vermouth"].System = System
    modules["vermouth.dssp"].dssp = modules["vermouth.dssp.dssp"]
    modules["vermouth.dssp.dssp"].DSSPError = DSSPError
    modules["vermouth.dssp.dssp"].run_dssp = run_dssp
    modules["vermouth.dssp.dssp"].read_dssp2 = lambda lines: ["H"]
    modules["vermouth.pdb.pdb"].read_pdb = lambda path: [object()]
    output = tmp_path / "reference.dssp"
    output.write_text("reference\n", encoding="utf-8")
    modules["vermouth.tests.datafiles"].PDB_PROTEIN = tmp_path / "protein.pdb"
    modules["vermouth.tests.datafiles"].DSSP_OUTPUT = output
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    namespace = {"__file__": str(tmp_path / "vermouth" / "tests" / "oracle.py")}
    exec(compile(ORACLE_SOURCE, "<legacy-oracle>", "exec"), namespace)
    test = namespace["test_legacy_dssp_protocol"]
    if mode == "passed":
        test(tmp_path, False, tmp_path)
    elif mode == "protocol_failure":
        with pytest.raises(pytest.fail.Exception, match="Legacy DSSP command protocol"):
            test(tmp_path, False, tmp_path)
    else:
        with pytest.raises(pytest.skip.Exception, match="not the target signature"):
            test(tmp_path, False, tmp_path)
    # The nested executable text must also be valid Python.
    compile((tmp_path / "legacy_dssp").read_text(), "<legacy-tool-fixture>", "exec")
