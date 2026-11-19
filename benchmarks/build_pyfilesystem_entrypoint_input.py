"""Prepare an independent installed-opener error-propagation oracle.

The oracle uses the repository's Registry with deterministic installed-plugin
fixtures. Container/full-trajectory validation is required before evidence claims.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from benchmarks.build_openhands_input import _read_json, build_input
from chrolo.core import parse_events
from chrolo.openhands import extract_actions

INSTANCE = "PyFilesystem__pyfilesystem2-232"
ORACLE_PATH = "tests/test_chrolo_entrypoint_errors.py"
ORACLE_SOURCE = '''import importlib
import pytest

registry_module = importlib.import_module("fs.opener.registry")


def install_fixture(monkeypatch, stage):
    marker = RuntimeError("CHROLO_INSTALLED_PLUGIN_ERROR")
    calls = []

    class InstalledOpener(registry_module.Opener):
        protocols = ["osfs"]

        def __init__(self):
            calls.append("construct")
            if stage == "construct":
                raise marker

        def open_fs(self, *args, **kwargs):
            raise AssertionError("This fixture must never open a filesystem")

    class EntryPoint(object):
        name = "osfs"

        def load(self):
            calls.append("load")
            if stage == "load":
                raise marker
            return InstalledOpener

    def entry_points(group, name=None):
        if group != "fs.opener":
            raise RuntimeError("Unexpected entry point group")
        return iter([EntryPoint()] if name in (None, "osfs") else [])

    monkeypatch.setattr(registry_module.pkg_resources, "iter_entry_points", entry_points)
    return marker, calls, InstalledOpener


@pytest.mark.parametrize("stage", ["load", "construct"])
def test_installed_opener_error_is_preserved(monkeypatch, stage):
    marker, calls, installed_type = install_fixture(monkeypatch, stage)
    expected = ["load"] if stage == "load" else ["load", "construct"]
    try:
        result = registry_module.Registry().get_opener("osfs")
    except registry_module.EntryPointError as error:
        if error.__cause__ is marker and calls == expected:
            return
        pytest.skip("Different plugin-error signature; inconclusive")
    except Exception:
        pytest.skip("Different registry execution error; inconclusive")
    if calls == expected and isinstance(result, registry_module.Opener) and not isinstance(result, installed_type):
        pytest.fail("CHROLO_INSTALLED_OPENER_ERROR_SUPPRESSED")
    pytest.skip("Different registry result; inconclusive")


def test_healthy_installed_opener_control(monkeypatch):
    marker, calls, installed_type = install_fixture(monkeypatch, "healthy")
    first = registry_module.Registry().get_opener("osfs")
    second = registry_module.Registry().get_opener("osfs")
    assert isinstance(first, installed_type) and isinstance(second, installed_type)
    assert first is not second
    assert calls == ["load", "construct", "load", "construct"]
'''


def build_entrypoint_input(trace: dict, task_row: dict, *, image: str) -> dict:
    if task_row.get("instance_id") != INSTANCE:
        raise ValueError("This oracle is specific to PyFilesystem__pyfilesystem2-232")
    if not isinstance(image, str) or not re.fullmatch(r".+@sha256:[0-9a-f]{64}", image):
        raise ValueError("Supply the verified task image pinned by full digest")
    source = build_input(trace, task_row)
    if image.split("@", 1)[0] != source["task"]["image"]:
        raise ValueError("Pinned image must use the task metadata's image name")
    if source["row"].get("repo") != "PyFilesystem/pyfilesystem2":
        raise ValueError("Unexpected repository identity")
    if not parse_events(extract_actions(source["row"])):
        raise ValueError("A full trajectory with recorded actions is required; a model patch is insufficient")
    task = source["task"]
    if ORACLE_PATH in task.get("test_patch", ""):
        raise ValueError("Oracle path already appears in the task test patch")
    patch = (f"diff --git a/{ORACLE_PATH} b/{ORACLE_PATH}\nnew file mode 100644\n"
             f"--- /dev/null\n+++ b/{ORACLE_PATH}\n@@ -0,0 +1,{len(ORACLE_SOURCE.splitlines())} @@\n"
             + "".join("+" + line + "\n" for line in ORACLE_SOURCE.splitlines()))
    task["test_patch"] = task.get("test_patch", "").rstrip("\n") + "\n" + patch
    task["image"] = image
    task["fail_to_pass"] = [ORACLE_PATH + "::test_installed_opener_error_is_preserved[" + stage + "]"
                            for stage in ("load", "construct")]
    task["pass_to_pass"] = list(dict.fromkeys(task_row["PASS_TO_PASS"] + task_row.get("PASS_TO_FAIL", [])
                                             + [ORACLE_PATH + "::test_healthy_installed_opener_control"]))
    task.pop("reference_patch", None)
    task["environment"] = {
        "PATH": "/opt/conda/envs/testbed/bin:/opt/conda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHONPATH": "/testbed",
    }
    source["evaluation"] = {
        "kind": "independent-installed-opener-error-propagation",
        "description": "An installed osfs plugin failure must retain its original EntryPointError cause instead of silently selecting a built-in fallback.",
        "failure_signature": "Both load/constructor fixtures must return a different Opener after the injected marker; unrelated errors, changed causes, missing fixture calls, and invalid results are inconclusive.",
        "oracle_sha256": hashlib.sha256(ORACLE_SOURCE.encode()).hexdigest(),
        "fixture_scope": "Deterministic installed-plugin fixtures; no actual plugin discovery, filesystem open, or external service is performed by the oracle.",
        "original_trajectory_modified": False,
        "requires_empty_action_control": True,
        "reference_issue_patch_is_not_an_independent_fix": True,
        "validation_status": "pending_task_image_and_full_action_replay",
    }
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    oracle = commands.add_parser("oracle", help="Write the standalone oracle without running repository code")
    oracle.add_argument("--output", type=Path, required=True)
    prepare = commands.add_parser("prepare", help="Join a full trace to task metadata and the independent oracle")
    prepare.add_argument("--trace-json", type=Path, required=True)
    prepare.add_argument("--task-json", type=Path, required=True)
    prepare.add_argument("--image", required=True)
    prepare.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "oracle":
        content = ORACLE_SOURCE
    else:
        protected = [args.trace_json.resolve(), args.task_json.resolve()]
        if args.output.resolve() in protected:
            parser.error("Output must differ from trace and task input files")
        source = build_entrypoint_input(_read_json(args.trace_json), _read_json(args.task_json), image=args.image)
        content = json.dumps(source, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print("Prepared oracle data only; task-image replay remains unverified")


if __name__ == "__main__":
    main()
