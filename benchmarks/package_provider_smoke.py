"""Run the SDK/LangGraph contract matrix against an installed wheel outside checkout.

Requires pytest and the langgraph, openai, and anthropic extras in a clean venv.
Only the example fixtures and integration tests are copied, never Chrolo source.
"""
from __future__ import annotations

import argparse
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "report.json"
    report_path.write_text(json.dumps({"passed": False, "complete": False}) + "\n")

    import chrolo
    package_path = Path(chrolo.__file__).resolve()
    if not package_path.is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError("Use a clean environment with a non-editable wheel install")

    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="chrolo-wheel-providers-") as directory:
        workspace = Path(directory).resolve()
        if workspace.is_relative_to(root):
            raise RuntimeError("Provider validation workspace must be outside the checkout")
        examples = workspace / "examples"
        examples.mkdir()
        (examples / "__init__.py").write_text("")
        for name in ("openai_langgraph_regression.py", "openai_chat_langgraph_regression.py",
                     "anthropic_langgraph_regression.py", "openai_langgraph_input.json"):
            shutil.copyfile(root / "examples" / name, examples / name)
        tests = workspace / "tests"
        tests.mkdir()
        shutil.copyfile(root / "tests/test_provider_graph_workflow.py", tests / "test_provider_graph_workflow.py")
        # Fail if optional dependencies are missing instead of accepting skipped tests.
        # Verify again inside pytest, where import paths differ from this launcher.
        (tests / "conftest.py").write_text(
            "import importlib\nimport sys\nfrom pathlib import Path\n"
            "def pytest_sessionstart(session):\n"
            "    import chrolo\n"
            "    assert Path(chrolo.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())\n"
            "    for name in ('pytest', 'langgraph', 'openai', 'anthropic', 'httpx'):\n"
            "        importlib.import_module(name)\n", encoding="utf-8")
        environment = dict(os.environ, PYTHONPATH=str(workspace),
                           PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", LANGSMITH_TRACING="false")
        environment.pop("PYTEST_ADDOPTS", None)
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests", "--junitxml=" + str(output / "junit.xml")],
            cwd=workspace, env=environment, capture_output=True, text=True, timeout=300)
        (output / "pytest.stdout.txt").write_text(completed.stdout, encoding="utf-8")
        (output / "pytest.stderr.txt").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode:
            raise RuntimeError("Installed-wheel provider matrix failed; see " + str(output))
        cases = ET.parse(output / "junit.xml").findall(".//testcase")
        if len(cases) != 21 or any(case.find(tag) is not None for case in cases
                                   for tag in ("skipped", "failure", "error")):
            raise RuntimeError("Expected all 21 provider contract tests to pass without skips")
        report = {"format": "chrolo.package-provider-smoke.v1", "passed": True, "complete": True,
                  "package_path": str(package_path), "interpreter": sys.executable,
                  "tests_passed": len(cases), "interfaces": ["openai_responses", "openai_chat", "anthropic_messages"],
                  "versions": {name: version(name) for name in ("chrolo-starter", "langgraph", "openai", "anthropic", "httpx")},
                  "scope": "Installed wheel outside checkout; actual SDK/LangGraph with local HTTP/tool fixtures"}
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Installed-wheel provider matrix passed all 21 tests without skips")


if __name__ == "__main__":
    main()
