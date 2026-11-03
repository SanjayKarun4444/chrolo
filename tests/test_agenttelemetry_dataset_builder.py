import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks import build_agenttelemetry_dataset as builder


def _mock_generator():
    def run_cell(condition, framework, fault, *, task):
        assert (condition, framework, fault) == ("agenttelemetry", "fake-framework", "tool_failure")
        spans = [
            {"span_id": "root", "parent_span_id": None, "name": "agent"},
            {
                "span_id": "child",
                "parent_span_id": "root",
                "name": "tool",
                "test_detected": task == "detected",
                "force_false_fire": task == "false-fire",
            },
        ]
        return spans, {"expected": "tool"}, {"error": None}

    def analyze(spans, fault, ground_truth):
        assert len(spans) == 2
        assert fault in {"tool_failure", "no_fault"}
        assert ground_truth == {"expected": "tool"}
        return spans[1]["test_detected"], None, None, None

    def false_fires(condition, spans):
        assert condition == "agenttelemetry"
        return ["tool_failure"] if spans[1].get("force_false_fire") else []

    return SimpleNamespace(
        FaultType=lambda value: value,
        _run_cell=run_cell,
        CONDITION_ANALYZERS={"agenttelemetry": analyze},
        _oracle_all_detectors=false_fires,
    )


def _input_file(tmp_path: Path, rows):
    path = tmp_path / "traces.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def _row(*, is_control=False, oracle_detected=True, false_fire=False):
    return {
        "id": "case-1",
        "metadata": {
            "is_control": is_control,
            "oracle_detected": oracle_detected,
            "oracle_false_fires": ["tool_failure"] if false_fire else [],
            "condition": "agenttelemetry",
            "fault_type": "tool_failure",
            "framework": "fake-framework",
            "task": "detected" if oracle_detected else "undetected",
        },
    }


def test_builder_recovers_spans_and_parent_dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "_load_generator", lambda _: _mock_generator())
    monkeypatch.setattr(
        builder.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="abc123\n"),
    )
    input_path = _input_file(tmp_path, [_row()])

    dataset = builder.build_dataset(tmp_path, input_path)

    assert dataset["format"] == "chrolo.benchmark.dataset.v1"
    assert dataset["metadata"]["source_commit"] == "abc123"
    assert dataset["metadata"]["included_reproducible_fault_cases"] == 1
    assert dataset["metadata"]["detector_evaluation"] == {
        "label_definition": "Fault rows use the injected target detector; a control is positive if any fault detector fires",
        "true_positive": 1,
        "false_negative": 0,
        "false_positive": 0,
        "true_negative": 0,
        "precision": 1.0,
        "recall": 1.0,
        "specificity": None,
        "f1": 1.0,
    }
    events = dataset["cases"][0]["events"]
    assert [event["id"] for event in events] == ["scenario", "span-0000", "span-0001"]
    assert events[0]["payload"]["framework"] == "fake-framework"
    assert events[0]["payload"]["task"] == "detected"
    assert events[2]["requires"] == ["span-0000"]
    assert dataset["cases"][0]["fixed_runner"] == "chrolo.agenttelemetry_benchmark:run_without_injected_fault"


@pytest.mark.parametrize("row", [_row(is_control=True, oracle_detected=False), _row(oracle_detected=False)])
def test_builder_skips_controls_and_unreproducible_rows(tmp_path, monkeypatch, row):
    monkeypatch.setattr(builder, "_load_generator", lambda _: _mock_generator())
    monkeypatch.setattr(
        builder.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="abc123\n"),
    )

    with pytest.raises(ValueError, match="No reproducible positive"):
        builder.build_dataset(tmp_path, _input_file(tmp_path, [row]))


def test_builder_fails_closed_if_generated_detector_result_disagrees_with_source(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "_load_generator", lambda _: _mock_generator())
    monkeypatch.setattr(
        builder.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="abc123\n"),
    )

    mismatched = _row()
    mismatched["metadata"]["task"] = "undetected"
    with pytest.raises(RuntimeError, match="disagrees with source metadata"):
        builder.build_dataset(tmp_path, _input_file(tmp_path, [mismatched]))


def test_builder_reports_detector_confusion_matrix_for_faults_and_controls(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "_load_generator", lambda _: _mock_generator())
    monkeypatch.setattr(
        builder.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="abc123\n"),
    )
    rows = [
        _row(),  # true positive
        _row(oracle_detected=False),  # false negative
        _row(is_control=True, oracle_detected=False, false_fire=True),  # false positive
        _row(is_control=True, oracle_detected=False),  # true negative
    ]
    # The control detector's direct result remains false; another detector can still false-fire.
    rows[2]["metadata"]["task"] = "false-fire"

    dataset = builder.build_dataset(tmp_path, _input_file(tmp_path, rows))

    evaluation = dataset["metadata"]["detector_evaluation"]
    assert evaluation["true_positive"] == 1
    assert evaluation["false_negative"] == 1
    assert evaluation["false_positive"] == 1
    assert evaluation["true_negative"] == 1
    assert evaluation["precision"] == 0.5
    assert evaluation["recall"] == 0.5
    assert evaluation["specificity"] == 0.5
    assert evaluation["f1"] == 0.5
    assert len(dataset["cases"]) == 1
