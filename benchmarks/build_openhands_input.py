"""Join one OpenHands trajectory row to its SWE-rebench task test metadata."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _single_trace_row(source: dict[str, Any]) -> dict[str, Any]:
    if isinstance(source.get("trajectory"), list):
        return source
    rows = source.get("rows")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("trace JSON must contain one row or a one-row Hugging Face 'rows' result")
    row = rows[0].get("row")
    if not isinstance(row, dict):
        raise ValueError("Hugging Face result row is missing its 'row' object")
    return row


def build_input(trace_source: dict[str, Any], task_row: dict[str, Any]) -> dict[str, Any]:
    trace = _single_trace_row(trace_source)
    instance_id = trace.get("instance_id")
    if not isinstance(instance_id, str) or not instance_id:
        raise ValueError("OpenHands trace row must have an instance_id")
    if task_row.get("instance_id") != instance_id:
        raise ValueError("OpenHands trace and SWE-rebench task instance_id do not match")
    install = task_row.get("install_config")
    if not isinstance(install, dict) or install.get("log_parser") != "parse_log_pytest":
        raise ValueError("only SWE-rebench tasks using the pytest log parser are supported")
    image = task_row.get("docker_image") or task_row.get("image_name")
    if not isinstance(image, str) or not image:
        raise ValueError("SWE-rebench task row has no Docker image name")
    fail_to_pass = task_row.get("FAIL_TO_PASS")
    pass_to_pass = task_row.get("PASS_TO_PASS")
    test_command = install.get("test_cmd")
    if not isinstance(fail_to_pass, list) or not fail_to_pass:
        raise ValueError("SWE-rebench task must provide FAIL_TO_PASS tests")
    if not isinstance(pass_to_pass, list) or not pass_to_pass:
        raise ValueError("SWE-rebench task must provide PASS_TO_PASS controls")
    if not isinstance(test_command, str) or not test_command:
        raise ValueError("SWE-rebench task install_config must provide test_cmd")
    trajectory = trace.get("trajectory")
    if not isinstance(trajectory, list):
        raise ValueError("OpenHands trace trajectory must be an array")

    # Allowlist only runner inputs. Patches, prompts, model output, and task
    # description are unnecessary for this workflow and may carry private data.
    row = {"instance_id": instance_id, "repo": trace.get("repo"), "trajectory": trajectory}
    task = {
        "image": image,
        "fail_to_pass": fail_to_pass,
        "pass_to_pass": pass_to_pass,
        "test_command": test_command,
        "workdir": "/testbed",
    }
    if isinstance(task_row.get("license_name"), str):
        task["license"] = task_row["license_name"]
    return {"row": row, "task": task}


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-json", required=True, type=Path, help="OpenHands row or one-row Hugging Face JSON response")
    parser.add_argument("--tasks-parquet", required=True, type=Path, help="SWE-rebench parquet shard containing the matching task")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        import pyarrow.parquet as pq
    except ImportError:
        raise SystemExit("Install pyarrow to read the SWE-rebench parquet shard") from None
    trace_source = _read_json(args.trace_json)
    trace = _single_trace_row(trace_source)
    instance_id = trace.get("instance_id")
    task_row = None
    for batch in pq.ParquetFile(args.tasks_parquet).iter_batches(batch_size=256):
        for candidate in batch.to_pylist():
            if candidate.get("instance_id") == instance_id:
                task_row = candidate
                break
        if task_row is not None:
            break
    if task_row is None:
        raise ValueError(f"No SWE-rebench task found for instance_id {instance_id!r}")
    data = build_input(trace_source, task_row)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Prepared {instance_id}: {len(data['task']['fail_to_pass'])} FAIL_TO_PASS and {len(data['task']['pass_to_pass'])} PASS_TO_PASS tests")
    print(f"Input contains raw action arguments; keep it local: {args.output}")


if __name__ == "__main__":
    main()
