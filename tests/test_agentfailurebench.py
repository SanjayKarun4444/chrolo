import json

import pytest

from chrolo.agentfailurebench import (
    preserves_trace_divergence,
    replay_corrected_trace,
    replay_trace,
)
from chrolo.benchmark import run_dataset
from chrolo.core import parse_events
from benchmarks.build_agentfailurebench_dataset import build_dataset


def _step(tool, output):
    return {"step_index": 0, "tool_call": {"name": tool, "arguments": {}}, "tool_output": output}


def _row(task_id="case-1", *, actual=None, corrected=None, category="incorrect_tool_selection"):
    actual = [_step("wrong", {"id": "bad"})] if actual is None else actual
    corrected = [_step("right", {"id": "good"})] if corrected is None else corrected
    return {
        "task_id": task_id,
        "data_provenance": "procedurally_generated_synthetic",
        "synthetic_data": True,
        "quality_control_status": "valid",
        "domain": "test",
        "failure_category": category,
        "failure_point": 0,
        "agent_trajectory": actual,
        "corrected_trajectory": corrected,
        "generator_version": "0.1.0",
        "schema_version": "1.0.0",
    }


def _build(tmp_path, rows):
    path = tmp_path / "input.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    return build_dataset(path, source_revision="deadbeef")


def test_builder_and_runner_preserve_witness_then_correct_it(tmp_path):
    dataset = _build(tmp_path, [_row()])
    result = run_dataset(dataset, repeat=2)
    report = result["scenarios"]["case-1"]
    assert report["reproduction_rate"] == 1
    assert report["failure_signature_preserved"] is True
    assert report["failure_after_simulated_fix"] is False
    assert result["dataset_metadata"]["source_revision"] == "deadbeef"


@pytest.mark.parametrize("kind", ["changed", "extra", "missing"])
def test_witness_kinds_replay_and_clear_after_correction(kind):
    failed = _step("wrong", {"id": "bad"})
    fixed = _step("right", {"id": "good"})
    if kind == "changed":
        witnesses = [{"kind": kind, "step_index": 0, "failed_signature": _signature(failed), "corrected_step": fixed}]
        steps, terminals = [failed], []
    elif kind == "extra":
        witnesses = [{"kind": kind, "step_index": 0, "failed_signature": _signature(failed)}]
        steps, terminals = [failed], []
    else:
        witnesses = [{"kind": kind, "step_index": 0, "corrected_step": fixed}]
        steps, terminals = [], [{"missing_step_index": 0}]
    events = parse_events([
        {"id": "scenario", "kind": "scenario", "payload": {"witnesses": witnesses}},
        *[{"id": f"step-{i}", "kind": "agent_step", "payload": item} for i, item in enumerate(steps)],
        *[{"id": f"terminal-{i}", "kind": "trace_terminal", "payload": item} for i, item in enumerate(terminals)],
    ])
    candidate = replay_trace(events)
    assert preserves_trace_divergence(candidate)
    assert not preserves_trace_divergence(replay_corrected_trace(events))


def _signature(step):
    import hashlib

    raw = json.dumps({"tool_call": step["tool_call"], "tool_output": step["tool_output"]}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def test_builder_skips_clean_and_label_only_failures(tmp_path):
    clean = _row("clean", category=None)
    clean["corrected_trajectory"] = clean["agent_trajectory"]
    label_only = _row("label-only")
    label_only["corrected_trajectory"] = label_only["agent_trajectory"]
    included = _row("included")
    dataset = _build(tmp_path, [clean, label_only, included])
    assert [case["id"] for case in dataset["cases"]] == ["included"]
    assert dataset["metadata"]["skipped_clean_records"] == 1
    assert dataset["metadata"]["skipped_failures_without_execution_witness"] == {
        "incorrect_tool_selection": 1
    }


def test_builder_rejects_untrusted_provenance_and_duplicate_ids(tmp_path):
    bad = _row()
    bad["synthetic_data"] = False
    with pytest.raises(ValueError, match="procedurally generated"):
        _build(tmp_path, [bad])
    with pytest.raises(ValueError, match="duplicate task_id"):
        _build(tmp_path, [_row("same"), _row("same")])
