"""Convert AgentFailureBench JSONL into Chrolo's trace-differential benchmark format.

Only failed records with a tool-call or tool-output difference from their paired
corrected trajectory are included. This is deterministic recorded-trace replay,
not execution of the benchmark's synthetic tools or a real-agent evaluation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _execution_signature(step: dict[str, Any]) -> str:
    value = {"tool_call": step.get("tool_call"), "tool_output": step.get("tool_output")}
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _output_values(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for child in value.values():
            found.update(_output_values(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_output_values(child))
    elif isinstance(value, str) and len(value) >= 5 and value.lower() not in {"success", "error", "true", "false"}:
        found.add(value)
    return found


def _argument_values(value: Any) -> set[str]:
    return _output_values(value)


def _witnesses(actual: list[dict[str, Any]], corrected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    witnesses = []
    for index in range(max(len(actual), len(corrected))):
        if index >= len(actual):
            witnesses.append({
                "kind": "missing", "step_index": index, "corrected_step": corrected[index]
            })
        elif index >= len(corrected):
            witnesses.append({
                "kind": "extra", "step_index": index,
                "failed_signature": _execution_signature(actual[index]),
            })
        elif _execution_signature(actual[index]) != _execution_signature(corrected[index]):
            witnesses.append({
                "kind": "changed", "step_index": index,
                "failed_signature": _execution_signature(actual[index]),
                "corrected_step": corrected[index],
            })
    return witnesses


def _dependencies(steps: list[dict[str, Any]]) -> dict[int, list[int]]:
    """Infer only explicit prior-output-to-later-argument value references."""
    dependencies: dict[int, list[int]] = {}
    for index, step in enumerate(steps):
        arguments = _argument_values((step.get("tool_call") or {}).get("arguments", {}))
        parents = []
        for prior_index, prior in enumerate(steps[:index]):
            outputs = _output_values(prior.get("tool_output", {}))
            if outputs & arguments:
                parents.append(prior_index)
        dependencies[index] = parents
    return dependencies


def build_dataset(input_path: Path, *, source_revision: str, split: str = "test") -> dict[str, Any]:
    rows = [
        json.loads(line)
        for line in input_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError("AgentFailureBench input must contain at least one JSONL record")
    cases = []
    skipped_clean = 0
    skipped_without_execution_witness: dict[str, int] = {}
    task_ids: set[str] = set()
    failure_category_counts: dict[str, int] = {}

    for row_number, row in enumerate(rows, 1):
        task_id = row.get("task_id")
        if not isinstance(task_id, str) or not task_id or task_id in task_ids:
            raise ValueError(f"Row {row_number} has a missing or duplicate task_id")
        task_ids.add(task_id)
        if row.get("data_provenance") != "procedurally_generated_synthetic" or row.get("synthetic_data") is not True:
            raise ValueError(f"Row {task_id} is not marked as procedurally generated synthetic data")
        if row.get("quality_control_status") != "valid":
            raise ValueError(f"Row {task_id} did not pass the source quality-control status")
        actual = row.get("agent_trajectory")
        if not isinstance(actual, list):
            raise ValueError(f"Row {task_id} has no agent_trajectory array")
        category = row.get("failure_category")
        if category is None:
            skipped_clean += 1
            clean_reference = row.get("corrected_trajectory")
            if clean_reference is not None and (
                not isinstance(clean_reference, list)
                or [_execution_signature(step) for step in actual]
                != [_execution_signature(step) for step in clean_reference]
            ):
                raise ValueError(f"Clean row {task_id} differs from its corrected trajectory")
            continue
        corrected = row.get("corrected_trajectory")
        if not isinstance(corrected, list):
            raise ValueError(f"Failed row {task_id} has no corrected_trajectory array")
        witnesses = _witnesses(actual, corrected)
        if not witnesses:
            skipped_without_execution_witness[category] = skipped_without_execution_witness.get(category, 0) + 1
            continue

        scenario = {
            "task_id": task_id,
            "domain": row["domain"],
            "failure_category": category,
            "failure_point": row.get("failure_point"),
            "witnesses": witnesses,
        }
        events = [{"id": "scenario", "kind": "scenario", "payload": scenario}]
        dependencies = _dependencies(actual)
        for index, step in enumerate(actual):
            required = ["scenario"] + [f"step-{parent:04d}" for parent in dependencies[index]]
            events.append({
                "id": f"step-{index:04d}", "kind": "agent_step", "payload": step,
                "requires": list(dict.fromkeys(required)),
            })
        for witness in witnesses:
            if witness["kind"] == "missing":
                required = ["scenario"] + [f"step-{index:04d}" for index in range(len(actual))]
                events.append({
                    "id": f"terminal-missing-{witness['step_index']:04d}",
                    "kind": "trace_terminal",
                    "payload": {"missing_step_index": witness["step_index"]},
                    "requires": required,
                })
        failure_category_counts[category] = failure_category_counts.get(category, 0) + 1
        cases.append({
            "id": task_id,
            "events": events,
            "runner": "chrolo.agentfailurebench:replay_trace",
            "oracle": "chrolo.agentfailurebench:preserves_trace_divergence",
            "fixed_runner": "chrolo.agentfailurebench:replay_corrected_trace",
        })

    if not cases:
        raise ValueError("No failed records with execution-visible trace differences were found")
    return {
        "format": "chrolo.benchmark.dataset.v1",
        "metadata": {
            "source": "fahadhafeezofficial/agent-failure-bench",
            "source_revision": source_revision,
            "split": split,
            "license": "CC-BY-4.0",
            "generator_version": rows[0].get("generator_version"),
            "schema_version": rows[0].get("schema_version"),
            "source_records": len(rows),
            "included_execution_divergence_cases": len(cases),
            "skipped_clean_records": skipped_clean,
            "skipped_failures_without_execution_witness": skipped_without_execution_witness,
            "included_failure_categories": failure_category_counts,
            "comparison_scope": "exact preservation of recorded tool-call/output differences from the paired corrected trajectory; tools are not executed",
            "limitations": "synthetic data; one canonical template per domain; replayed observations, not independent environment execution",
        },
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="AgentFailureBench split JSONL")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-revision", required=True, help="Pinned Hugging Face revision")
    parser.add_argument("--split", default="test")
    args = parser.parse_args()
    dataset = build_dataset(args.input, source_revision=args.source_revision, split=args.split)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dataset, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(dataset["metadata"], ensure_ascii=False, indent=2))
    print(f"Wrote {len(dataset['cases'])} trace-differential cases to {args.output}")


if __name__ == "__main__":
    main()
