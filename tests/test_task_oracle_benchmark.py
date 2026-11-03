from __future__ import annotations

import json
from pathlib import Path

from chrolo.benchmark import run_dataset
from chrolo.core import parse_events
from examples.task_oracle_benchmark import (
    is_original_task_failure,
    run_task,
    run_task_fixed,
)


DATASET = Path(__file__).parents[1] / "examples" / "task_oracle_dataset.json"


def _dataset():
    return json.loads(DATASET.read_text(encoding="utf-8"))


def test_task_runner_mutates_fresh_environment_and_fixed_runner_clears_signature():
    dataset = _dataset()
    for case in dataset["cases"]:
        events = parse_events(case["events"])
        failed = run_task(events)
        fixed = run_task_fixed(events)
        assert is_original_task_failure(failed)
        assert not is_original_task_failure(fixed)
        # Every run starts from the fixture's initial state; mutations do not leak.
        assert is_original_task_failure(run_task(events))


def test_minimizer_keeps_causal_tool_chain_and_clears_failure_after_fix():
    report = run_dataset(_dataset(), repeat=5)
    assert report["synthetic_only"] is True
    assert report["dataset_metadata"]["synthetic_only"] is True
    assert report["aggregate"]["case_count"] == 3
    assert report["aggregate"]["mean_reproduction_rate"] == 1
    assert report["aggregate"]["signature_preservation_rate"] == 1
    assert report["aggregate"]["fix_sensitivity_rate"] == 1
    for case_id in (
        "wrong_ticket_assignment",
        "duplicate_invoice_payment",
        "delete_protected_file",
    ):
        case = report["scenarios"][case_id]
        assert case["one_minimal_under_tested_cascade_deletions"] is True
        assert case["failure_after_simulated_fix"] is False
        assert case["remaining_events"] < case["original_events"]
