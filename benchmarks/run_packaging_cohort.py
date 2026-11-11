"""Evaluate a preselected cohort with passing empty controls and isolated replays.

This measures the explicit packaging oracle, not the original issue outcome.
Inputs contain raw public actions and must remain local. Reports omit payloads.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from chrolo.core import parse_events
from chrolo.openhands import extract_actions
from chrolo.openhands_docker import OpenHandsDockerRunner


def evaluate_case(source: dict, *, runner_factory=OpenHandsDockerRunner) -> dict:
    row, task = source["row"], source["task"]
    events = parse_events(extract_actions(row))
    options = {key: value for key, value in task.items() if key not in {"license", "reference_patch"}}
    runner = runner_factory(**options)
    targets, controls = task["fail_to_pass"], task["pass_to_pass"]
    record = {"instance_id": row.get("instance_id"), "actions": len(events),
              "trajectory_id": source.get("evaluation", {}).get("trajectory_id", row.get("trajectory_id"))}
    stage = "empty_control"
    try:
        empty = runner([])
        statuses = empty.get("statuses", {})
        if any(statuses.get(test) != "PASSED" for test in targets + controls):
            return {**record, "outcome": "empty_control_rejected"}
        stage = "full_replay"
        full = runner(events)
        statuses = full.get("statuses", {})
        if any(statuses.get(test) != "PASSED" for test in controls):
            return {**record, "outcome": "inconclusive", "stage": stage, "reason": "control_not_passing"}
        target_statuses = [statuses.get(test) for test in targets]
        if all(status == "FAILED" for status in target_statuses):
            outcome = "action_dependent_failure"
        elif all(status == "PASSED" for status in target_statuses):
            outcome = "no_target_failure"
        else:
            return {**record, "outcome": "inconclusive", "stage": stage, "reason": "target_not_decisive"}
        return {**record, "outcome": outcome, "empty_control_passed": True,
                "passing_controls": len(controls), "target_statuses": target_statuses}
    except Exception as error:
        return {**record, "outcome": "inconclusive", "stage": stage, "error_type": type(error).__name__}


def summarize(records: list[dict]) -> dict:
    counts = {outcome: sum(row["outcome"] == outcome for row in records) for outcome in (
        "action_dependent_failure", "no_target_failure", "empty_control_rejected", "inconclusive")}
    return {"format": "chrolo.packaging-cohort.v1", "evaluated": len(records), "counts": counts,
            "cases": records, "limitation": "Task-specific packaging oracle; correlated runs of one issue are not independent task-level reliability estimates."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    # Freeze and persist the selection before running any candidate.
    report = {"selection": [path.name for path in args.inputs], **summarize([])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    records = []
    for path in args.inputs:
        result = evaluate_case(json.loads(path.read_text(encoding="utf-8")))
        result["input"] = path.name
        records.append(result)
        report.update(summarize(records))
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(path.name + ": " + result["outcome"], flush=True)


if __name__ == "__main__":
    main()
