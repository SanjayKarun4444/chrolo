"""Validate an installed wheel's capture-to-generated-test path outside the repo.

Run this script with a clean environment containing the wheel and pytest. The
workflow uses only standard-library model/tool fixtures, not hosted providers.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    import chrolo
    package_path = Path(chrolo.__file__).resolve()
    if not package_path.is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError("Use a clean environment with a non-editable wheel install")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps({"passed": False, "complete": False}) + "\n")
    workspace = Path(tempfile.mkdtemp(prefix="chrolo-wheel-workflow-")).resolve()
    if workspace.is_relative_to(Path(__file__).resolve().parents[1]):
        raise RuntimeError("Package validation workspace must be outside the source checkout")
    fixture = Path(__file__).resolve().parents[1] / "examples" / "interaction_minimization.py"
    (workspace / "workflow_fixture.py").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    source = {"initial_state": {}, "events": [
        {"id": "noise", "kind": "probe", "payload": {"query": "diagnostic"}},
        {"id": "failure", "kind": "trigger", "payload": {"request": "failure"}},
    ]}
    (workspace / "input.json").write_text(json.dumps(source), encoding="utf-8")
    environment = dict(os.environ, PYTHONPATH=str(workspace), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    steps = []

    def run(arguments, expected=0):
        completed = subprocess.run([sys.executable, *arguments], cwd=workspace, env=environment,
                                   capture_output=True, text=True, timeout=120)
        if completed.returncode != expected:
            raise RuntimeError("Packaged workflow command failed: " + " ".join(arguments[:3]) + "\n" + completed.stdout + completed.stderr)
        steps.append({"command": arguments, "exit_code": completed.returncode})
        return completed

    run(["-m", "chrolo", "capture", "--graph", "workflow_fixture:build_graph", "--input", "input.json",
         "--record-interactions", "--output", "capture.json"])
    run(["-m", "chrolo", "replay", "capture.json", "--oracle", "workflow_fixture:original_failure", "--require-failure"])
    run(["-m", "chrolo", "minimize", "capture.json", "--oracle", "workflow_fixture:original_failure",
         "--output", "minimal.json", "--test", "test_regression.py"])
    minimal = json.loads((workspace / "minimal.json").read_text())
    if [event["id"] for event in minimal["events"]] != ["failure"] or len(minimal["interactions"]["interactions"]) != 1:
        raise RuntimeError("Packaged minimization did not prune the irrelevant event and call")
    broken = run(["-m", "pytest", "-q", "test_regression.py"], expected=1)
    if "Original failure recurred" not in broken.stdout:
        raise RuntimeError("Generated test failed for a different reason")
    (workspace / "broken_minimal.json").write_text(json.dumps(minimal, indent=2), encoding="utf-8")
    minimal["graph"] = "workflow_fixture:build_fixed_graph"
    (workspace / "minimal.json").write_text(json.dumps(minimal, indent=2), encoding="utf-8")
    run(["-m", "pytest", "-q", "test_regression.py"])
    artifacts = output / "artifacts"
    artifacts.mkdir(exist_ok=True)
    for name in ["workflow_fixture.py", "input.json", "capture.json", "minimal.json", "broken_minimal.json", "test_regression.py"]:
        (artifacts / name).write_bytes((workspace / name).read_bytes())
    report = {"format": "chrolo.package-workflow-smoke.v1", "passed": True, "complete": True,
              "package_path": str(package_path), "interpreter": sys.executable, "workspace": str(workspace),
              "scope": "Clean installed-wheel CLI with stdlib model/tool fixtures outside the source checkout",
              "events_before": 2, "events_after": 1, "interactions_before": 2, "interactions_after": 1,
              "steps": steps}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Installed-wheel workflow passed capture, replay, minimization, and before/after generated pytest")


if __name__ == "__main__":
    main()
