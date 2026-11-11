"""Run local, opt-in real-agent replay regression checks in isolated Docker tasks.

These checks pass when known failures reproduce and their controls pass. They
validate Chrolo replay, not repaired agent behavior. Raw fixtures stay local.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from chrolo.core import parse_events
from chrolo.openhands import extract_actions
from chrolo.openhands_docker import OpenHandsDockerRunner, failure_oracle

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path(__file__).with_name("real_regressions.json")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_case(entry: dict, root: Path = ROOT) -> dict:
    source = read_json(root / entry["input"])
    report = read_json(root / entry["report"])
    events = parse_events(report["events"])
    if [event.id for event in events] != entry["expected_events"]:
        raise ValueError("Reduced fixture differs from the expected regression events")
    original = [event.to_dict() for event in parse_events(extract_actions(source["row"]))]
    retained = [event for event in original if event["id"] in entry["expected_events"]]
    if retained != [event.to_dict() for event in events]:
        raise ValueError("Reduced actions must be an unchanged ordered subset of the source")
    removed = set(entry["remove_for_passing_control"])
    if not removed or not removed <= set(entry["expected_events"]):
        raise ValueError("Passing control must remove known regression events")
    task = source["task"]
    if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", task["image"]):
        raise ValueError("Regression task image must be pinned by digest")
    comparison = read_json(root / entry["comparison"]) if entry.get("comparison") else None
    fingerprint = hashlib.sha256(json.dumps({"task": task, "events": report["events"]}, sort_keys=True).encode()).hexdigest()
    if fingerprint != entry.get("fixture_sha256"):
        raise ValueError("Regression fixture fingerprint changed")
    if comparison and hashlib.sha256(json.dumps(comparison, sort_keys=True).encode()).hexdigest() != entry.get("comparison_sha256"):
        raise ValueError("Comparison fixture fingerprint changed")
    if comparison and comparison["task"] != task:
        # Workspace mappings can differ between original runs, but the oracle,
        # image, and execution limits must remain identical.
        left = {k: v for k, v in task.items() if k != "source_workspace"}
        right = {k: v for k, v in comparison["task"].items() if k != "source_workspace"}
        if left != right:
            raise ValueError("Comparison must use the same pinned image and evaluation task")
    return {"entry": entry, "source": source, "events": events, "comparison": comparison,
            "fixture_sha256": fingerprint}


def run_case(case: dict, *, repeat: int = 3, runner_factory=OpenHandsDockerRunner) -> dict:
    if type(repeat) is not int or repeat < 1:
        raise ValueError("repeat must be a positive integer")
    task = case["source"]["task"]
    options = {k: v for k, v in task.items() if k not in {"license", "reference_patch"}}
    runner = runner_factory(**options)
    oracle = failure_oracle(task["fail_to_pass"], task["pass_to_pass"])
    required = task["fail_to_pass"] + task["pass_to_pass"]
    events = case["events"]
    removed = set(case["entry"]["remove_for_passing_control"])
    passing = [event for event in events if event.id not in removed]
    checks = []

    def check(label, candidate, expect_failure, active_runner=runner):
        try:
            result = active_runner(candidate)
            if expect_failure:
                passed = oracle(result) and all(result.get("statuses", {}).get(test) == "FAILED" for test in task["fail_to_pass"]) and all(result.get("statuses", {}).get(test) == "PASSED" for test in task["pass_to_pass"])
            else:
                passed = all(result.get("statuses", {}).get(test) == "PASSED" for test in required)
            checks.append({"check": label, "passed": passed})
            return passed
        except Exception as error:
            checks.append({"check": label, "passed": False, "error_type": type(error).__name__})
            return False

    for repetition in range(repeat):
        prefix = str(repetition + 1)
        if not check(prefix + ":empty", [], False):
            break
        if not check(prefix + ":minimal", events, True):
            break
        if not check(prefix + ":passing_control", passing, False):
            break
    if case["comparison"] and all(check["passed"] for check in checks):
        comparison = case["comparison"]
        comparison_options = {k: v for k, v in comparison["task"].items() if k not in {"license", "reference_patch"}}
        check("comparison", parse_events(extract_actions(comparison["row"])), False, runner_factory(**comparison_options))
    return {"name": case["entry"]["name"], "passed": all(check["passed"] for check in checks),
            "fixture_sha256": case["fixture_sha256"], "image": task["image"], "checks": checks,
            "scope": case["source"].get("evaluation", {}).get("kind")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("results/real-regression-checks.json"))
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be a positive integer")
    try:
        manifest = read_json(args.manifest)
        if manifest.get("format") != "chrolo.real-regressions.v1" or not manifest.get("cases"):
            raise ValueError("Invalid or empty regression manifest")
        # Validate every local fixture before starting any Docker execution.
        cases = [load_case(entry) for entry in manifest["cases"]]
    except (OSError, ValueError, KeyError, TypeError):
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"format": "chrolo.real-regression-checks.v1", "cases": [], "complete": False,
                                          "passed": False, "error": "fixtures_missing_or_invalid"}, indent=2) + "\n", encoding="utf-8")
        print("Regression fixtures are missing or invalid; see benchmarks/README.md for local fixture preparation.")
        return 2
    results = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"format": "chrolo.real-regression-checks.v1", "cases": [],
                                      "complete": False, "passed": False}, indent=2) + "\n", encoding="utf-8")
    for case in cases:
        result = run_case(case, repeat=args.repeat)
        results.append(result)
        args.output.write_text(json.dumps({"format": "chrolo.real-regression-checks.v1", "cases": results,
                                          "complete": len(results) == len(cases),
                                          "passed": len(results) == len(cases) and all(row["passed"] for row in results)}, indent=2) + "\n", encoding="utf-8")
        print(result["name"] + ": " + ("passed" if result["passed"] else "FAILED"), flush=True)
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
