"""Freeze and screen a multi-repository cohort with explicit task oracles.

This extends the single-task packaging screen, not minimization or fix evidence.
Raw prepared inputs remain local. Reports contain identities and counts only.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import tempfile

from benchmarks.run_packaging_cohort import evaluate_case
from chrolo.core import parse_events
from chrolo.openhands import extract_actions
from chrolo.openhands_docker import OpenHandsDockerRunner

ROOT = Path(__file__).resolve().parents[1]
OUTCOMES = ("action_dependent_failure", "no_target_failure", "empty_control_rejected",
            "inconclusive", "unstable", "pending")


def content_identity(source: dict) -> str:
    """Outer JSON formatting must not manufacture another cohort case."""
    canonical = json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(json.dumps(value, indent=2) + "\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def local_path(root: Path, value: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("Input path is required")
    relative = Path(value)
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError("Inputs must stay within the selected root")
    return path


def describe(source: dict) -> dict:
    row, task = source["row"], source["task"]
    repository = row["repo"]
    instance = row["instance_id"]
    mode = source["evaluation"]["kind"]
    if not all(isinstance(value, str) and value.strip() for value in (repository, instance, mode)):
        raise ValueError("Repository, task, and oracle kind are required")
    if not re.fullmatch(r"[^/]+/[^/]+", repository) or not re.fullmatch(re.escape(repository.replace('/', '__')) + r"-\d+", instance):
        raise ValueError("Repository and task identity disagree")
    if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", task["image"]):
        raise ValueError("Task image must be pinned by full digest")
    options = {k: v for k, v in task.items() if k not in {"license", "reference_patch"}}
    # Constructor validates options without starting Docker.
    OpenHandsDockerRunner(**options)
    if not task["pass_to_pass"]:
        raise ValueError("Cohort screening requires passing control tests")
    for key in ("fail_to_pass", "pass_to_pass"):
        if len(set(task[key])) != len(task[key]):
            raise ValueError("Duplicate test identifiers")
    events = parse_events(extract_actions(row))
    if not events:
        raise ValueError("A cohort input must contain recorded actions")
    return {"repository": repository, "instance_id": instance, "failure_mode": mode,
            "actions": len(events)}


def freeze(inputs: list[Path], *, root: Path, reason: str) -> dict:
    if not inputs or not isinstance(reason, str) or not reason.strip():
        raise ValueError("A nonempty selection and selection reason are required")
    cases, seen = [], set()
    for path in inputs:
        resolved = path.resolve()
        relative = resolved.relative_to(root.resolve()).as_posix()
        raw = local_path(root, relative).read_bytes()
        fingerprint = hashlib.sha256(raw).hexdigest()
        source = json.loads(raw)
        identity = content_identity(source)
        if identity in seen:
            raise ValueError("Duplicate input content in cohort")
        seen.add(identity)
        cases.append({"id": f"case-{len(cases) + 1:04d}", "input": relative,
                      "sha256": fingerprint, **describe(source)})
    return {"format": "chrolo.task-cohort.v1", "selection_reason": reason, "cases": cases}


def load_cases(manifest: dict, root: Path) -> list[dict]:
    if not isinstance(manifest, dict) or manifest.get("format") != "chrolo.task-cohort.v1" or not isinstance(manifest.get("cases"), list) or not manifest["cases"]:
        raise ValueError("Invalid or empty cohort manifest")
    if set(manifest) != {"format", "selection_reason", "cases"}:
        raise ValueError("Unexpected manifest fields")
    if not isinstance(manifest.get("selection_reason"), str) or not manifest["selection_reason"].strip():
        raise ValueError("Selection reason is required")
    sources, ids, fingerprints, content_ids = [], set(), set(), set()
    for entry in manifest["cases"]:
        if not isinstance(entry, dict) or set(entry) != {"id", "input", "sha256", "repository", "instance_id", "failure_mode", "actions"}:
            raise ValueError("Invalid case fields")
        identity = entry["id"]
        if not isinstance(identity, str) or not identity or identity in ids:
            raise ValueError("Case IDs must be unique nonempty strings")
        ids.add(identity)
        raw = local_path(root, entry["input"]).read_bytes()
        fingerprint = hashlib.sha256(raw).hexdigest()
        if fingerprint != entry["sha256"] or fingerprint in fingerprints:
            raise ValueError("Input content changed or duplicated")
        fingerprints.add(fingerprint)
        source = json.loads(raw)
        content_id = content_identity(source)
        if content_id in content_ids:
            raise ValueError("Duplicate input content in cohort")
        content_ids.add(content_id)
        if any(entry.get(key) != value for key, value in describe(source).items()):
            raise ValueError("Cohort identity or oracle metadata changed")
        sources.append(source)
    return sources


def case_outcome(attempts: list[dict], repeat: int) -> str:
    if len(attempts) != repeat:
        return "pending"
    outcomes = {attempt["outcome"] for attempt in attempts}
    if "inconclusive" in outcomes:
        return "inconclusive"
    return next(iter(outcomes)) if len(outcomes) == 1 else "unstable"


def summarize(records: list[dict], repeat: int) -> dict:
    def counts(rows):
        found = Counter(row["outcome"] for row in rows)
        return {key: found[key] for key in OUTCOMES}

    groups = {}
    for field in ("repository", "instance_id", "failure_mode"):
        groups[field] = {}
        for value in sorted({row[field] for row in records}):
            rows = [row for row in records if row[field] == value]
            groups[field][value] = {"selected": len(rows), "counts": counts(rows)}
    return {"selected": len(records), "evaluated": sum(row["outcome"] != "pending" for row in records),
            "repositories": len(groups["repository"]), "tasks": len(groups["instance_id"]),
            "failure_modes": len(groups["failure_mode"]),
            "attempts_completed": sum(len(row["attempts"]) for row in records),
            "attempts_planned": len(records) * repeat, "counts": counts(records), "groups": groups}


def run_cohort(manifest: dict, *, root: Path, output: Path, repeat: int = 3,
               runner_factory=OpenHandsDockerRunner) -> dict:
    if type(repeat) is not int or repeat < 1:
        raise ValueError("repeat must be a positive integer")
    for entry in manifest.get("cases", []) if isinstance(manifest, dict) and isinstance(manifest.get("cases"), list) else []:
        try:
            path = local_path(root, entry["input"])
        except (ValueError, KeyError, TypeError):
            continue  # Invalid fixture will be rejected below, before any runner.
        if output.resolve() == path:
            raise ValueError("Report output must differ from every input")
    report = {"format": "chrolo.task-cohort-report.v1", "complete": False,
              "manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
              "selection": None, "repeat": repeat, "cases": [],
              "limitation": "Repeated task-oracle screening only; correlated runs are not independent tasks. No minimization or fix sensitivity is established."}
    # Invalidate stale success before checking any fixtures or constructing runners.
    write_json(output, report)
    try:
        sources = load_cases(manifest, root)
    except (OSError, ValueError, KeyError, TypeError):
        report["error"] = "fixtures_missing_or_invalid"
        write_json(output, report)
        return report
    records = [{**entry, "attempts": [], "outcome": "pending"} for entry in manifest["cases"]]
    report["selection"] = manifest
    report["cases"] = records
    report.update(summarize(records, repeat))
    write_json(output, report)
    for record, source in zip(records, sources):
        for _ in range(repeat):
            try:
                attempt = evaluate_case(source, runner_factory=runner_factory)
            except Exception as error:
                attempt = {"outcome": "inconclusive", "stage": "runner_setup", "error_type": type(error).__name__}
            record["attempts"].append(attempt)
            record["outcome"] = case_outcome(record["attempts"], repeat)
            report.update(summarize(records, repeat))
            report["complete"] = report["evaluated"] == report["selected"]
            write_json(output, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("freeze", help="Pin the selection without executing actions")
    create.add_argument("inputs", nargs="+", type=Path)
    create.add_argument("--reason", required=True)
    create.add_argument("--output", required=True, type=Path)
    run = commands.add_parser("run", help="Validate every input, then screen each case repeatedly")
    run.add_argument("manifest", type=Path)
    run.add_argument("--repeat", type=int, default=3)
    run.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "freeze":
            manifest = freeze(args.inputs, root=args.root, reason=args.reason)
            if args.output.exists():
                raise ValueError("Choose a new manifest path; frozen selections are not overwritten")
            write_json(args.output, manifest)
            print(f"Frozen {len(manifest['cases'])} cases; no actions executed")
            return 0
        # Input/report paths may never alias: writing the report must not destroy fixtures.
        if args.output.resolve() == args.manifest.resolve():
            raise ValueError("Report output must differ from the manifest")
        try:
            manifest = json.loads(args.manifest.read_bytes())
        except (OSError, ValueError):
            write_json(args.output, {"format": "chrolo.task-cohort-report.v1", "complete": False,
                                     "error": "manifest_missing_or_invalid", "cases": []})
            print("Cohort manifest is missing or invalid; no actions executed")
            return 2
        report = run_cohort(manifest, root=args.root, output=args.output, repeat=args.repeat)
        if "error" in report:
            print("Cohort fixtures are missing or invalid; no actions executed")
            return 2
        print(json.dumps(report["counts"], sort_keys=True))
        return 1 if any(report["counts"][key] for key in ("inconclusive", "unstable", "empty_control_rejected")) else 0
    except (OSError, ValueError, KeyError, TypeError):
        print("Invalid cohort configuration; check paths, identities, image digests, and task oracles")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
