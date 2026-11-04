import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _inspect(trace_path):
    return subprocess.run(
        [sys.executable, "-m", "chrolo", "inspect", str(trace_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_inspect_reports_invalid_json_without_traceback(tmp_path):
    trace = tmp_path / "broken.json"
    trace.write_text('{"format":', encoding="utf-8")

    result = _inspect(trace)

    assert result.returncode == 2
    assert f"{trace}: invalid JSON at line 1, column 11" in result.stderr
    assert "Traceback" not in result.stderr


def test_inspect_requires_top_level_json_object(tmp_path):
    trace = tmp_path / "array.json"
    trace.write_text("[]", encoding="utf-8")

    result = _inspect(trace)

    assert result.returncode == 2
    assert f"{trace}: expected a JSON object at the top level" in result.stderr
    assert "Traceback" not in result.stderr


def test_scan_cli_reports_only_secret_paths(tmp_path):
    trace = tmp_path / "credentials.json"
    secret = "Bearer never-print-this-credential-123456"
    trace.write_text(
        json.dumps({"events": [{"payload": {"authorization": secret}}]}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-m", "chrolo", "scan", str(trace)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert '"path": "/events/0/payload/authorization"' in result.stdout
    assert "bearer-token" in result.stdout
    assert secret not in result.stdout


def test_capture_missing_events_reports_actionable_error(tmp_path):
    source = tmp_path / "input.json"
    source.write_text('{"initial_state": {}}', encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable, "-m", "chrolo", "capture",
            "--graph", "examples.fake_graph:build_graph",
            "--input", str(source),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert f"{source}: required field 'events' is missing" in result.stderr
    assert "Traceback" not in result.stderr


def test_minimize_missing_events_reports_actionable_error(tmp_path):
    scenario = tmp_path / "scenario.json"
    scenario.write_text('{"initial_state": {}}', encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable, "-m", "chrolo", "minimize", str(scenario),
            "--run", "examples.fake_graph:build_graph",
            "--oracle", "examples.fake_graph:build_graph",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert f"{scenario}: required field 'events' is missing" in result.stderr
    assert "Traceback" not in result.stderr


def test_serve_cli_forwards_advertise_ip_to_trace_desk(monkeypatch):
    from chrolo import cli

    calls = []
    monkeypatch.setattr(cli, "serve_web", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(
        sys,
        "argv",
        ["chrolo", "serve", "--host", "0.0.0.0", "--advertise-ip", "100.71.4.2"],
    )

    cli.main()

    assert calls == [(("0.0.0.0", 8765, None, None, None), {"advertise_ip": "100.71.4.2"})]


def test_replay_mismatch_does_not_print_recorded_target(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace

    from chrolo import cli
    from chrolo.cassette import RecordedCallError, ReplayMismatch

    secret_target = "provider-token-never-print"
    monkeypatch.setattr(cli, "_read_json", lambda _path: {"interactions": {}})
    monkeypatch.setattr(cli, "validate_capture", lambda _capture: [])
    monkeypatch.setattr(
        cli, "replay_recorded",
        lambda *_args: (_ for _ in ()).throw(ReplayMismatch(0, f"expected target {secret_target!r}")),
    )

    with pytest.raises(ValueError, match="Replay did not complete"):
        cli.replay_cmd(SimpleNamespace(
            trace=tmp_path / "trace.json", allow_live_side_effects=False, oracle=None,
        ))

    output = capsys.readouterr().out
    assert "target differs from the recorded interaction" in output
    assert secret_target not in output


def test_replay_recorded_error_does_not_print_trace_error_fields(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace

    from chrolo import cli
    from chrolo.cassette import RecordedCallError

    secret_type = "provider-key-should-not-print"
    secret_message = "bearer-value-should-not-print"
    monkeypatch.setattr(cli, "_read_json", lambda _path: {"interactions": {}})
    monkeypatch.setattr(cli, "validate_capture", lambda _capture: [])

    def fail_replay(*_args):
        raise RecordedCallError(0, "model", "ignored-target", secret_type, secret_message)

    monkeypatch.setattr(cli, "replay_recorded", fail_replay)

    with pytest.raises(ValueError, match="Replay did not complete"):
        cli.replay_cmd(SimpleNamespace(
            trace=tmp_path / "trace.json", allow_live_side_effects=False, oracle=None,
        ))

    output = capsys.readouterr().out
    assert "recorded model error" in output
    assert "details omitted" in output
    assert secret_type not in output
    assert secret_message not in output


def test_replay_require_failure_turns_false_oracle_into_cli_error(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace

    from chrolo import cli

    monkeypatch.setattr(cli, "_read_json", lambda _path: {"interactions": {}})
    monkeypatch.setattr(cli, "validate_capture", lambda _capture: [])
    monkeypatch.setattr(cli, "replay_recorded", lambda *_args: {"ok": True})
    monkeypatch.setattr(cli, "resolve", lambda _target: lambda _result: False)

    with pytest.raises(ValueError, match="Original failure did not recur"):
        cli.replay_cmd(SimpleNamespace(
            trace=tmp_path / "trace.json", allow_live_side_effects=False,
            oracle="example:failure", require_failure=True,
        ))

    assert "Original failure detected: no" in capsys.readouterr().out


def test_replay_require_failure_needs_oracle(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from chrolo import cli

    monkeypatch.setattr(cli, "_read_json", lambda _path: {})
    monkeypatch.setattr(cli, "validate_capture", lambda _capture: [])

    with pytest.raises(ValueError, match="--require-failure needs --oracle"):
        cli.replay_cmd(SimpleNamespace(
            trace=tmp_path / "trace.json", allow_live_side_effects=False,
            oracle=None, require_failure=True,
        ))
