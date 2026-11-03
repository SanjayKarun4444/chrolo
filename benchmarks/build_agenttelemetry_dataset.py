"""Build a Chrolo dataset from AgentTelemetry's deterministic fault harness.

Run this script in AgentTelemetry's locked environment after generating its
Inspect JSONL. It regenerates every row to validate the recorded detector labels
and report control/fault classification metrics, then keeps reproducible
positive cases for Chrolo's minimization runner.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import platform
import subprocess
from typing import Any


def _load_generator(root: Path):
    path = root / "src" / "agenttelemetry_inspect" / "generate_dataset.py"
    spec = importlib.util.spec_from_file_location("agenttelemetry_generator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load AgentTelemetry generator at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_dataset(root: Path, input_path: Path) -> dict[str, Any]:
    root = root.resolve()
    input_path = input_path.resolve()
    generator = _load_generator(root)
    rows = [json.loads(line) for line in input_path.read_text(encoding="utf-8").splitlines() if line]
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False
    )
    source_commit = commit.stdout.strip() if commit.returncode == 0 else "unknown"
    cases = []
    skipped = 0
    detector_counts = {"true_positive": 0, "false_negative": 0, "false_positive": 0, "true_negative": 0}

    for row in rows:
        metadata = row["metadata"]
        condition = metadata["condition"]
        fault = generator.FaultType(metadata["fault_type"])
        spans, ground_truth, result = generator._run_cell(
            condition,
            metadata["framework"],
            fault,
            task=metadata["task"],
        )
        if result.get("error"):
            raise RuntimeError(f"Regeneration failed for {row['id']}: {type(result['error']).__name__}")
        detected, _, _, _ = generator.CONDITION_ANALYZERS[condition](spans, fault, ground_truth)
        detected = bool(detected)
        if detected != bool(metadata["oracle_detected"]):
            raise RuntimeError(f"Regenerated detector result disagrees with source metadata for {row['id']}")
        injected_fault = not bool(metadata["is_control"])
        if not injected_fault:
            false_fires = sorted(generator._oracle_all_detectors(condition, spans))
            expected_false_fires = sorted(metadata.get("oracle_false_fires", []))
            if false_fires != expected_false_fires:
                raise RuntimeError(f"Regenerated false-alarm list disagrees with source metadata for {row['id']}")
            detected = bool(false_fires)
        confusion_key = (
            "true_positive" if injected_fault and detected else
            "false_negative" if injected_fault else
            "false_positive" if detected else
            "true_negative"
        )
        detector_counts[confusion_key] += 1
        if not injected_fault or not detected:
            skipped += 1
            continue

        events = [{
            "id": "scenario",
            "kind": "scenario",
            "payload": {
                "condition": condition,
                "fault_type": metadata["fault_type"],
                "ground_truth": ground_truth,
                "framework": metadata["framework"],
                "task": metadata["task"],
            },
        }]
        event_id_by_span: dict[str, str] = {}
        for index, span in enumerate(spans):
            event_id = f"span-{index:04d}"
            parent_id = span.get("parent_span_id")
            dependencies = [event_id_by_span[parent_id]] if parent_id in event_id_by_span else []
            event = {
                "id": event_id,
                "kind": "span",
                "payload": span,
                "requires": dependencies,
            }
            json.dumps(event, ensure_ascii=False, allow_nan=False)
            events.append(event)
            span_id = span.get("span_id")
            if span_id:
                event_id_by_span[span_id] = event_id

        cases.append({
            "id": row["id"],
            "events": events,
            "runner": "chrolo.agenttelemetry_benchmark:analyze_trace",
            "oracle": "chrolo.agenttelemetry_benchmark:preserves_injected_fault",
            "fixed_runner": "chrolo.agenttelemetry_benchmark:run_without_injected_fault",
        })

    if not cases:
        raise ValueError("No reproducible positive fault cases found in the generated dataset")
    tp = detector_counts["true_positive"]
    fp = detector_counts["false_positive"]
    fn = detector_counts["false_negative"]
    tn = detector_counts["true_negative"]
    detector_evaluation = {
        "label_definition": "Fault rows use the injected target detector; a control is positive if any fault detector fires",
        "true_positive": tp,
        "false_negative": fn,
        "false_positive": fp,
        "true_negative": tn,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "specificity": round(tn / (tn + fp), 4) if tn + fp else None,
        "f1": round(2 * tp / (2 * tp + fp + fn), 4) if 2 * tp + fp + fn else None,
    }
    return {
        "format": "chrolo.benchmark.dataset.v1",
        "metadata": {
            "source": "AgentTelemetry deterministic mock-backed fault-injection harness",
            "source_commit": source_commit,
            "python_version": platform.python_version(),
            "source_records": len(rows),
            "included_reproducible_fault_cases": len(cases),
            "skipped_controls_or_undetected_faults": skipped,
            "detector_evaluation": detector_evaluation,
            "comparison_scope": "preservation of the same rule-based telemetry fault signal; not agent-task reproduction",
            "runner_requires_environment": "AGENTTELEMETRY_ROOT points to the AgentTelemetry checkout",
        },
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agenttelemetry-root", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True, help="Generated AgentTelemetry JSONL file")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dataset = build_dataset(args.agenttelemetry_root, args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dataset["metadata"], indent=2))
    print(f"Wrote {len(dataset['cases'])} reproducible cases to {args.output}")


if __name__ == "__main__":
    main()
