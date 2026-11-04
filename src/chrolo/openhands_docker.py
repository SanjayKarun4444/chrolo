"""Disposable, network-isolated Docker runner for extracted OpenHands actions.

The trace is untrusted. This runner never bind-mounts host files and starts a
fresh container for every candidate. End-to-end semantics still require
validation against the published task image before benchmark claims are made.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from .core import Event, minimize, parse_events
from .openhands import extract_actions


_RESULT_PATTERN = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED)\s+(.+?)(?:\s+-\s+.*)?$")

# This program runs inside an ephemeral task image. It receives only JSON on
# stdin; no Chrolo source or host directory is mounted into the container.
_CONTAINER_PROGRAM = r'''
import json, os, re, signal, subprocess, sys
from pathlib import Path

request = json.load(sys.stdin)
root = Path(request["workdir"]).resolve()
edit_history = {}
source_workspace = request.get("source_workspace")

def map_workspace_path(value):
    if isinstance(source_workspace, str) and (value == source_workspace or value.startswith(source_workspace + "/")):
        return str(root) + value[len(source_workspace):]
    return value

reference_patch = request.get("reference_patch")
if reference_patch is not None:
    patched = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], cwd=root,
                             input=reference_patch, text=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             timeout=request["test_timeout"])
    if patched.returncode != 0:
        raise RuntimeError("reference patch could not be applied")

def in_workspace(value):
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("editor path escapes workspace")
    return candidate

for event in request["actions"]:
    kind = event["kind"]
    args = event["payload"]["arguments"]
    if kind == "execute_bash":
        command = args.get("command")
        if not isinstance(command, str):
            raise ValueError("execute_bash command must be a string")
        if isinstance(source_workspace, str):
            command = command.replace(source_workspace, str(root))
        process = subprocess.Popen(command, shell=True, executable="/bin/bash", cwd=root,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        try:
            process.wait(timeout=request["action_timeout"])
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    elif kind == "str_replace_editor":
        command = args.get("command")
        path = in_workspace(map_workspace_path(args.get("path", "")))
        try:
            if command == "view":
                continue
            if command == "create":
                if path.exists() or not isinstance(args.get("file_text"), str):
                    continue
                if not path.parent.is_dir():
                    continue
                path.write_text(args["file_text"], encoding="utf-8")
            elif command == "str_replace":
                source = path.read_text(encoding="utf-8").expandtabs()
                old = args["old_str"].expandtabs()
                new = args.get("new_str", "")
                if not isinstance(new, str) or source.count(old) != 1:
                    continue
                edit_history.setdefault(str(path), []).append(source)
                path.write_text(source.replace(old, new.expandtabs()), encoding="utf-8")
            elif command == "insert":
                source = path.read_text(encoding="utf-8").expandtabs()
                insert_line = args.get("insert_line")
                new = args.get("new_str")
                lines = source.split("\n")
                if type(insert_line) is not int or not isinstance(new, str) or not 0 <= insert_line <= len(lines):
                    continue
                edit_history.setdefault(str(path), []).append(source)
                path.write_text("\n".join(lines[:insert_line] + new.expandtabs().split("\n") + lines[insert_line:]), encoding="utf-8")
            elif command == "undo_edit":
                history = edit_history.get(str(path), [])
                if not history:
                    continue
                path.write_text(history.pop(), encoding="utf-8")
            else:
                raise ValueError("unsupported str_replace_editor command")
        except (OSError, KeyError, UnicodeError):
            # Tool errors are observations in the original run. Later recorded
            # actions still execute; their arguments do not depend on outputs.
            continue
    else:
        raise ValueError("unsupported action kind")

command = request["test_command"]
tests = request["fail_to_pass"] + request["pass_to_pass"]
result = subprocess.run(command + tests, cwd=root, text=True,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        timeout=request["test_timeout"])
statuses = {}
for line in result.stdout.splitlines():
    match = re.match(request["result_pattern"], line.strip())
    if match:
        statuses[match.group(2)] = match.group(1)
print(json.dumps({"statuses": statuses, "test_exit_code": result.returncode}))
'''


class OpenHandsDockerRunner:
    """Callable Chrolo runner that replays actions in a clean task container."""

    def __init__(
        self,
        *,
        image: str,
        fail_to_pass: Sequence[str],
        pass_to_pass: Sequence[str],
        test_command: str,
        workdir: str = "/testbed",
        docker_executable: str = "docker",
        action_timeout: int = 120,
        test_timeout: int = 300,
        total_timeout: int = 900,
        memory: str = "4g",
        cpus: str = "2",
        pids_limit: int = 256,
        reference_patch: str | None = None,
        source_workspace: str | None = None,
    ) -> None:
        if not isinstance(image, str) or not image.strip():
            raise ValueError("Docker image must be a non-empty string")
        if not workdir.startswith("/"):
            raise ValueError("Container workdir must be an absolute path")
        if isinstance(fail_to_pass, (str, bytes)) or not fail_to_pass or any(not isinstance(x, str) or not x for x in fail_to_pass):
            raise ValueError("FAIL_TO_PASS must contain test identifiers")
        if isinstance(pass_to_pass, (str, bytes)) or any(not isinstance(x, str) or not x for x in pass_to_pass):
            raise ValueError("PASS_TO_PASS must contain test identifiers")
        if set(fail_to_pass) & set(pass_to_pass):
            raise ValueError("FAIL_TO_PASS and PASS_TO_PASS must be disjoint")
        if not isinstance(test_command, str) or not test_command.strip():
            raise ValueError("Test command must be a non-empty string")
        if not isinstance(docker_executable, str) or not docker_executable:
            raise ValueError("Docker executable must be a non-empty string")
        if reference_patch is not None and not isinstance(reference_patch, str):
            raise ValueError("reference_patch must be a string")
        if source_workspace is not None and (not isinstance(source_workspace, str) or not source_workspace.startswith("/")):
            raise ValueError("source_workspace must be an absolute OpenHands path")
        for name, value in (("action_timeout", action_timeout), ("test_timeout", test_timeout), ("total_timeout", total_timeout), ("pids_limit", pids_limit)):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not re.fullmatch(r"[1-9][0-9]*(?:[kKmMgG])?", memory):
            raise ValueError("memory must be a Docker size such as 4g")
        if not re.fullmatch(r"[1-9][0-9]*(?:\.[0-9]+)?", cpus):
            raise ValueError("cpus must be a positive Docker CPU quota")
        self.image = image
        self.fail_to_pass = list(fail_to_pass)
        self.pass_to_pass = list(pass_to_pass)
        self.test_command = shlex.split(test_command)
        self.workdir = workdir
        self.docker_executable = docker_executable
        self.action_timeout = action_timeout
        self.test_timeout = test_timeout
        self.total_timeout = total_timeout
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.reference_patch = reference_patch
        self.source_workspace = source_workspace

    def docker_argv(self, container_name: str | None = None) -> list[str]:
        """Build the bounded Docker invocation, useful for audit and testing."""
        argv = [self.docker_executable, "run", "--rm", "-i"]
        if container_name is not None:
            argv.extend(["--name", container_name])
        argv.extend([
            "--network=none", f"--memory={self.memory}", f"--cpus={self.cpus}",
            f"--pids-limit={self.pids_limit}", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--tmpfs=/tmp:rw,nosuid,nodev,size=256m",
            "--workdir", self.workdir, self.image,
            "python", "-c", _CONTAINER_PROGRAM,
        ])
        return argv

    def __call__(self, events: list[Event]) -> dict[str, Any]:
        actions = [event.to_dict() for event in events]
        for event in events:
            arguments = event.payload.get("arguments")
            if not isinstance(arguments, Mapping):
                raise ValueError(f"OpenHands action {event.id!r} requires an arguments object")
            if event.kind == "execute_bash":
                if not isinstance(arguments.get("command"), str):
                    raise ValueError(f"OpenHands action {event.id!r} requires a shell command string")
            elif event.kind == "str_replace_editor":
                command = arguments.get("command")
                required = {
                    "view": (),
                    "create": ("file_text",),
                    "str_replace": ("old_str",),
                    "insert": ("insert_line", "new_str"),
                    "undo_edit": (),
                }
                if not isinstance(command, str) or command not in required:
                    raise ValueError(f"OpenHands action {event.id!r} uses unsupported editor command")
                if not isinstance(arguments.get("path"), str) or any(
                    (
                        type(arguments.get(key)) is not int if key == "insert_line"
                        else not isinstance(arguments.get(key), str)
                    ) for key in required[command]
                ):
                    raise ValueError(f"OpenHands action {event.id!r} has invalid editor arguments")
            else:
                raise ValueError(f"OpenHands action {event.id!r} has unsupported tool kind")
        request = {
            "actions": actions,
            "workdir": self.workdir,
            "action_timeout": self.action_timeout,
            "test_timeout": self.test_timeout,
            "test_command": self.test_command,
            "fail_to_pass": self.fail_to_pass,
            "pass_to_pass": self.pass_to_pass,
            "result_pattern": _RESULT_PATTERN.pattern,
            "reference_patch": self.reference_patch,
            "source_workspace": self.source_workspace,
        }
        container_name = f"chrolo-replay-{uuid.uuid4().hex}"
        argv = self.docker_argv(container_name)
        try:
            completed = subprocess.run(
                argv,
                input=json.dumps(request, separators=(",", ":")),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.total_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            try:
                subprocess.run(
                    [self.docker_executable, "rm", "--force", container_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            raise RuntimeError("OpenHands Docker replay exceeded its time limit") from None
        if completed.returncode != 0:
            # Avoid surfacing task output or commands in an exception message.
            raise RuntimeError(f"OpenHands Docker replay failed (exit {completed.returncode})")
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError:
            raise RuntimeError("OpenHands Docker replay returned invalid result data") from None
        statuses = result.get("statuses") if isinstance(result, dict) else None
        if not isinstance(statuses, dict):
            raise RuntimeError("OpenHands Docker replay omitted test statuses")
        expected = set(self.fail_to_pass) | set(self.pass_to_pass)
        if set(statuses) != expected:
            raise RuntimeError("OpenHands Docker replay did not report every configured test")
        return {
            "statuses": statuses,
            "failed_to_pass": sorted(t for t in self.fail_to_pass if statuses[t] in {"FAILED", "ERROR"}),
            "passed_to_pass": sorted(t for t in self.pass_to_pass if statuses[t] == "PASSED"),
            "test_exit_code": result.get("test_exit_code"),
        }


def failure_oracle(
    expected_failed_to_pass: Sequence[str],
    pass_to_pass: Sequence[str],
):
    """Build an exact-signature oracle for an observed unresolved task run."""
    if isinstance(expected_failed_to_pass, (str, bytes)) or isinstance(pass_to_pass, (str, bytes)):
        raise ValueError("Failure and control tests must be arrays of test identifiers")
    if any(not isinstance(test, str) or not test for test in expected_failed_to_pass):
        raise ValueError("Expected failure signature must contain test identifiers")
    if any(not isinstance(test, str) or not test for test in pass_to_pass):
        raise ValueError("Control signature must contain test identifiers")
    expected = frozenset(expected_failed_to_pass)
    controls = frozenset(pass_to_pass)
    if not expected:
        raise ValueError("Expected failure signature must not be empty")

    def oracle(result: Any) -> bool:
        if not isinstance(result, Mapping):
            return False
        actual = frozenset(result.get("failed_to_pass", ()))
        passed = frozenset(result.get("passed_to_pass", ()))
        return actual == expected and passed == controls

    return oracle


def benchmark_openhands_trace(
    row: Mapping[str, Any],
    task: Mapping[str, Any],
    *,
    repeat: int = 5,
    max_checks: int = 1000,
    runner_factory=OpenHandsDockerRunner,
) -> dict[str, Any]:
    """Replay and minimize one recorded trace against its SWE-rebench tests.

    ``runner_factory`` is injectable for unit tests. The default starts Docker;
    callers should treat the emitted reduced event payloads as sensitive data.
    """
    if type(repeat) is not int or repeat < 1:
        raise ValueError("repeat must be a positive integer")
    if type(max_checks) is not int or max_checks < 1:
        raise ValueError("max_checks must be a positive integer")
    if not isinstance(task, Mapping):
        raise ValueError("task metadata must be an object")
    required = ("image", "fail_to_pass", "pass_to_pass", "test_command")
    missing = [name for name in required if name not in task]
    if missing:
        raise ValueError("task metadata is missing: " + ", ".join(missing))
    events = parse_events(extract_actions(row))
    runner_options = {key: task[key] for key in (
        "image", "fail_to_pass", "pass_to_pass", "test_command", "workdir",
        "docker_executable", "action_timeout", "test_timeout", "total_timeout",
        "memory", "cpus", "pids_limit", "source_workspace",
    ) if key in task}
    runner = runner_factory(**runner_options)

    baseline = runner(list(events))
    if not isinstance(baseline, Mapping):
        raise RuntimeError("OpenHands baseline runner returned invalid result data")
    expected_failures = baseline.get("failed_to_pass")
    expected_controls = baseline.get("passed_to_pass")
    if not isinstance(expected_failures, list) or not expected_failures:
        raise ValueError("Baseline did not reproduce a FAIL_TO_PASS failure")
    if set(expected_controls or ()) != set(task["pass_to_pass"]):
        raise ValueError("Baseline failed one or more PASS_TO_PASS controls")
    oracle = failure_oracle(expected_failures, task["pass_to_pass"])

    repeats = []
    for _ in range(repeat):
        try:
            repeats.append(bool(oracle(runner(list(events)))))
        except Exception:
            repeats.append(False)

    result = minimize(events, runner, oracle, max_checks=max_checks)
    try:
        minimized_result = runner(list(result.events))
        preserved = bool(oracle(minimized_result))
    except Exception:
        minimized_result = None
        preserved = False

    fix_sensitivity_rate = None
    failure_after_reference_patch = None
    if isinstance(task.get("reference_patch"), str) and task["reference_patch"].strip():
        fixed_runner = runner_factory(**runner_options, reference_patch=task["reference_patch"])
        fixed_repeats: list[bool | None] = []
        for _ in range(repeat):
            try:
                fixed_repeats.append(bool(oracle(fixed_runner(list(result.events)))))
            except Exception:
                fixed_repeats.append(None)
        valid_fixed = [value for value in fixed_repeats if value is not None]
        fix_sensitivity_rate = (
            sum(value is False for value in valid_fixed) / len(valid_fixed)
            if valid_fixed else None
        )
        failure_after_reference_patch = fixed_repeats[-1] if fixed_repeats else None
    original_count = len(events)
    minimized_count = len(result.events)
    return {
        "format": "chrolo.openhands-benchmark.v1",
        "instance_id": row.get("instance_id"),
        "repo": row.get("repo"),
        "image": task["image"],
        "repetitions": repeat,
        "reproduction_rate": sum(repeats) / repeat,
        "baseline_failed_to_pass": sorted(expected_failures),
        "baseline_passed_to_pass": sorted(expected_controls),
        "failure_signature_preserved": preserved,
        "failure_after_reference_patch": failure_after_reference_patch,
        "fix_sensitivity_rate": fix_sensitivity_rate,
        "minimized_result": minimized_result,
        "original_events": original_count,
        "remaining_events": minimized_count,
        "shrink_percent": round(100 * (original_count - minimized_count) / original_count, 2) if original_count else 0.0,
        "checks": result.checks,
        "one_minimal_under_tested_cascade_deletions": result.one_minimal,
        "accepted_deletions": result.accepted_deletions,
        "candidate_rejections": result.candidate_rejections,
        "events": [event.to_dict() for event in result.events],
        "limitation": "Recorded actions and their arguments are untrusted data; this benchmark does not validate the agent's hidden reasoning or unrecorded side effects.",
    }
