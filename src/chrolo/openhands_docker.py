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

from .core import Event


_RESULT_PATTERN = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED)\s+(.+?)(?:\s+-\s+.*)?$")

# This program runs inside an ephemeral task image. It receives only JSON on
# stdin; no Chrolo source or host directory is mounted into the container.
_CONTAINER_PROGRAM = r'''
import json, os, re, signal, subprocess, sys
from pathlib import Path

request = json.load(sys.stdin)
root = Path(request["workdir"]).resolve()

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
        path = in_workspace(args.get("path", ""))
        try:
            if command == "view":
                continue
            if command == "create":
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(args["file_text"], encoding="utf-8")
            elif command == "str_replace":
                source = path.read_text(encoding="utf-8")
                old = args["old_str"]
                if old not in source:
                    continue
                path.write_text(source.replace(old, args["new_str"]), encoding="utf-8")
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
                    "str_replace": ("old_str", "new_str"),
                }
                if not isinstance(command, str) or command not in required:
                    raise ValueError(f"OpenHands action {event.id!r} uses unsupported editor command")
                if not isinstance(arguments.get("path"), str) or any(
                    not isinstance(arguments.get(key), str) for key in required[command]
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
            "result_pattern": _RESULT_PATTERN,
        }
        # Regex objects are not JSON serializable; transmit the equivalent
        # pattern string and compile it in the container process.
        request["result_pattern"] = _RESULT_PATTERN.pattern
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
