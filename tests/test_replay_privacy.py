import json
import subprocess
import sys
from pathlib import Path

import pytest

from chrolo.adapters_langgraph import FORMAT, OUTCOME_CAPTURE_FORMAT, OUTCOME_FORMAT, replay_recorded
from chrolo.cassette import CassetteRecorder, ReplaySession, LEGACY_FORMAT

ROOT = Path(__file__).resolve().parents[1]
SECRET = "private-trace-value-should-never-print"


def _capture(interactions, *, outcome=None):
    return {
        "format": OUTCOME_CAPTURE_FORMAT if outcome else FORMAT,
        "graph": "examples.interaction_minimization:build_graph",
        "event_key": "events", "initial_state": {},
        "events": [{"id": "trigger", "kind": "trigger", "payload": {}}],
        "interactions": interactions,
        **({"outcome": outcome} if outcome else {}),
    }


def _duplicate_cassette():
    recorder = CassetteRecorder()
    for _ in range(2):
        recorder.call("model", "example.failure-model", {"question": "reproduce"}, lambda: {"failure": True})
    trace = recorder.to_dict()
    for call in trace["interactions"]:
        call["id"] = SECRET
    return trace


@pytest.mark.parametrize("legacy", [False, True])
def test_duplicate_id_validation_identifies_position_without_echoing_value(legacy):
    cassette = _duplicate_cassette()
    if legacy:
        cassette["format"] = LEGACY_FORMAT
        cassette.pop("header")
    with pytest.raises(ValueError) as error:
        ReplaySession.from_dict(cassette)
    assert str(error.value) == "Duplicate interaction ID at index 1"
    assert SECRET not in str(error.value)


@pytest.mark.parametrize("command", ["replay", "minimize"])
def test_cli_rejects_duplicate_ids_without_disclosing_them(tmp_path, command):
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(_capture(_duplicate_cassette())), encoding="utf-8")
    arguments = [sys.executable, "-m", "chrolo", command, str(path)]
    if command == "minimize":
        arguments += ["--oracle", "examples.interaction_minimization:original_failure",
                      "--output", str(tmp_path / "minimal.json"), "--test", str(tmp_path / "test_generated.py")]
    result = subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert SECRET not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "minimal.json").exists()


def test_exception_outcome_label_stays_available_to_oracle_but_not_cli(tmp_path):
    recorder = CassetteRecorder()

    def fail():
        raise RuntimeError("private recorded message")

    with pytest.raises(RuntimeError):
        recorder.call("model", "example.failure-model", {"question": "reproduce"}, fail)
    cassette = recorder.to_dict()
    cassette["interactions"][0]["error"]["type"] = SECRET
    capture = _capture(cassette, outcome={"format": OUTCOME_FORMAT, "status": "exception", "exception_type": SECRET})
    assert replay_recorded(capture) == {"status": "exception", "exception_type": SECRET}
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(capture), encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "chrolo", "replay", str(path)], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert "Captured exception observed" in result.stdout and "details omitted" in result.stdout
    assert SECRET not in result.stdout + result.stderr
    assert "private recorded message" not in result.stdout + result.stderr
