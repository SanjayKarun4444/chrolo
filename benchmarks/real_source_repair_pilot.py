"""Generate and exercise a real-action regression with a separate source repair.

Uses fingerprinted local fixtures and pinned offline Docker images.
This is action replay, not a provider cassette or a repair of agent policy.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from benchmarks.real_regressions import DEFAULT_MANIFEST, load_case, read_json
from chrolo.openhands import extract_actions


RUNNER = '''import json
from pathlib import Path
from chrolo.openhands_docker import OpenHandsDockerRunner

ROOT = Path(__file__).resolve().parent
TASK = json.loads((ROOT / "task.json").read_text(encoding="utf-8"))

def run(events):
    options = {key: value for key, value in TASK.items() if key not in {"license", "reference_patch"}}
    patch = ROOT / "source_fix.patch"
    if patch.exists():
        options["repair_patch"] = patch.read_text(encoding="utf-8")
    return OpenHandsDockerRunner(**options)(events)

def original_failure(result):
    statuses = result.get("statuses", {})
    targets = TASK["fail_to_pass"]
    controls = TASK["pass_to_pass"]
    if not all(statuses.get(test) == "PASSED" for test in controls):
        raise RuntimeError("Regression controls did not all pass")
    if all(statuses.get(test) == "FAILED" for test in targets):
        return True
    if all(statuses.get(test) == "PASSED" for test in targets) and result.get("test_exit_code") == 0:
        return False
    raise RuntimeError("Different or inconclusive target outcome")
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--case", choices=["vermouth-legacy-protocol", "pyfilesystem-installed-opener"], default="vermouth-legacy-protocol")
    parser.add_argument("--use-retained-minimal", action="store_true", help="Start test generation from the already verified minimized actions")
    args = parser.parse_args()
    entry = next(row for row in read_json(DEFAULT_MANIFEST)["cases"] if row["name"] == args.case)
    case = load_case(entry)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {"format": "chrolo.real-source-repair-pilot.v1", "complete": False, "passed": False,
              "fixture_sha256": case["fixture_sha256"], "image": case["source"]["task"]["image"],
              "case": args.case, "search_started_from_retained_minimal": args.use_retained_minimal,
              "scope": "Real recorded agent edits with deterministic external fixtures; repairs application source, not agent policy", "steps": []}
    report_path = output / "report.json"

    def checkpoint():
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    checkpoint()
    (output / ".gitignore").write_text("*\n", encoding="utf-8")
    (output / "project_runner.py").write_text(RUNNER, encoding="utf-8")
    task_path = output / "task.json"
    task_path.write_text(json.dumps(case["source"]["task"], indent=2), encoding="utf-8")
    task_bytes = task_path.read_bytes()
    full = {"events": extract_actions(case["source"]["row"])}
    search_input = {"events": [event.to_dict() for event in case["events"]]} if args.use_retained_minimal else full
    source = output / "input.json"
    source.write_text(json.dumps(search_input, indent=2), encoding="utf-8")
    source_bytes = source.read_bytes()
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(output), str(Path(__file__).resolve().parents[1] / "src")]),
                       PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")

    def run(label, arguments, expected=0, message=None):
        completed = subprocess.run([sys.executable, *arguments], cwd=output, env=environment,
                                   capture_output=True, text=True, timeout=1800)
        (output / (label + ".log")).write_text(completed.stdout + completed.stderr, encoding="utf-8")
        report["steps"].append({"label": label, "exit_code": completed.returncode})
        checkpoint()
        if completed.returncode != expected or (message and message not in completed.stdout + completed.stderr):
            raise RuntimeError(f"Unexpected {label} outcome; see retained log")
        print(label + ": verified", flush=True)

    run("empty_control", ["-c", "import project_runner as p; assert not p.original_failure(p.run([]))"])
    run("minimize", ["-m", "chrolo", "minimize", "input.json", "--run", "project_runner:run",
                     "--oracle", "project_runner:original_failure", "--output", "minimal.json",
                     "--test", "test_regression.py", "--max-checks", "100"])
    fixture = output / "minimal.json"
    fixture_bytes = fixture.read_bytes()
    reduced = json.loads(fixture_bytes)
    assert [event["id"] for event in reduced["events"]] == entry["expected_events"]
    test = ["-m", "pytest", "-q", "test_regression.py"]
    run("before_fix", test, expected=1, message="Original failure recurred")
    # Repair application source after replaying the exact recorded edits.
    # The oracle and recorded actions do not change.
    edit = case["events"][-1].payload["arguments"]
    path = "fs/opener/registry.py" if args.case == "pyfilesystem-installed-opener" else "vermouth/dssp/dssp.py"
    original = subprocess.run(["docker", "run", "--rm", "--network=none", "--cap-drop=ALL",
                               "--security-opt=no-new-privileges", report["image"], "cat", "/testbed/" + path],
                              capture_output=True, text=True, timeout=60, check=True).stdout
    modified = original
    for event in case["events"]:
        arguments = event.payload["arguments"]
        if event.kind != "str_replace_editor" or arguments.get("command") != "str_replace" or arguments["path"] != case["source"]["task"]["source_workspace"] + "/" + path:
            raise RuntimeError("Source repair requires same-file recorded replacement edits")
        if modified.count(arguments["old_str"]) != 1:
            raise RuntimeError("Recorded edit does not uniquely match pinned application source")
        modified = modified.replace(arguments["old_str"], arguments["new_str"])
    if args.case == "pyfilesystem-installed-opener":
        anchor = "        should_fallback = not entry_point_found or entry_point_error is not None"
        if modified.count(anchor) != 1:
            raise RuntimeError("Expected installed-plugin fallback decision was not unique")
        fixed = modified.replace(anchor, "        if entry_point_found and entry_point_error is not None:\n"
                                 "            raise entry_point_error\n"
                                 "        should_fallback = not entry_point_found")
    else:
        fixed = modified.replace(edit["new_str"], edit["old_str"])
    repair = "".join(difflib.unified_diff(modified.splitlines(keepends=True), fixed.splitlines(keepends=True),
                                        fromfile="a/" + path, tofile="b/" + path))
    repair_path = output / "source_fix.patch"
    repair_path.write_text(repair, encoding="utf-8")
    run("after_fix", test)
    # Broken source repair must be an execution error, never a passing test.
    repair_path.write_text("invalid patch\n", encoding="utf-8")
    run("invalid_repair_rejected", test, expected=1, message="Docker replay failed")
    repair_path.write_text(repair, encoding="utf-8")
    run("fixed_again", test)
    assert fixture.read_bytes() == fixture_bytes and source.read_bytes() == source_bytes
    assert task_path.read_bytes() == task_bytes
    report.update(complete=True, passed=True, original_events=len(full["events"]), search_input_events=len(search_input["events"]), remaining_events=len(reduced["events"]),
                  fixture_unchanged=True, input_unchanged=True, task_and_oracle_unchanged=True,
                  minimized_sha256=hashlib.sha256(fixture_bytes).hexdigest(), reduction=reduced["reduction"])
    checkpoint()
    print("Real-action source-repair pilot passed:", report_path)


if __name__ == "__main__":
    main()
