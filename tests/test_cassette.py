import pytest

from chrolo.cassette import (
    FORMAT,
    CassetteRecorder,
    RecordedCallError,
    ReplayMismatch,
    ReplaySession,
)


def test_record_then_replay_returns_recorded_values_without_live_calls():
    live_calls = {"model": 0, "tool": 0}
    recorder = CassetteRecorder()

    def live_model():
        live_calls["model"] += 1
        return {"text": "chosen", "tokens": [1, 2]}

    def live_tool():
        live_calls["tool"] += 1
        return {"issue": 42}

    recorder.call("model", "fake.chat", {"prompt": "choose"}, live_model)
    recorder.call("tool", "search_issue", {"query": "bug"}, live_tool)
    trace = recorder.to_dict()
    assert live_calls == {"model": 1, "tool": 1}
    assert trace["format"] == FORMAT

    replay = ReplaySession.from_dict(trace)
    assert replay.call("model", "fake.chat", {"prompt": "choose"}) == {
        "text": "chosen", "tokens": [1, 2]
    }
    assert replay.call("tool", "search_issue", {"query": "bug"}) == {"issue": 42}
    replay.finish()
    assert live_calls == {"model": 1, "tool": 1}


def test_replay_returns_independent_response_copies():
    recorder = CassetteRecorder()
    recorder.call("tool", "lookup", {}, lambda: {"items": [1]})
    trace = recorder.to_dict()
    trace["interactions"][0]["response"]["items"].append(2)
    replay = ReplaySession.from_dict(recorder.to_dict())
    first = replay.call("tool", "lookup", {})
    first["items"].append(3)
    replay2 = ReplaySession.from_dict(recorder.to_dict())
    assert replay2.call("tool", "lookup", {}) == {"items": [1]}


@pytest.mark.parametrize(
    "boundary,target,request_data,reason",
    [
        ("tool", "model", {"x": 1}, "expected model boundary"),
        ("model", "other", {"x": 1}, "expected target"),
        ("model", "chat", {"x": 2}, "request differs"),
    ],
)
def test_replay_rejects_boundary_target_or_request_mismatch(boundary, target, request_data, reason):
    recorder = CassetteRecorder()
    recorder.call("model", "chat", {"x": 1}, lambda: "answer")
    replay = ReplaySession.from_dict(recorder.to_dict())
    with pytest.raises(ReplayMismatch, match=reason):
        replay.call(boundary, target, request_data)
    assert replay.consumed == 0


def test_replay_rejects_unexpected_and_unconsumed_calls():
    replay = ReplaySession.from_dict({"format": FORMAT, "interactions": []})
    with pytest.raises(ReplayMismatch, match="cassette is exhausted"):
        replay.call("tool", "lookup", {})

    recorder = CassetteRecorder()
    recorder.call("tool", "lookup", {}, lambda: 1)
    replay = ReplaySession.from_dict(recorder.to_dict())
    with pytest.raises(ReplayMismatch, match="not consumed"):
        replay.finish()


def test_recorded_boundary_error_replays_as_data_without_live_invocation():
    calls = []
    recorder = CassetteRecorder()

    def live_failure():
        calls.append("live")
        raise TimeoutError("provider timed out")

    with pytest.raises(TimeoutError):
        recorder.call("model", "fake.chat", {"prompt": "x"}, live_failure)

    replay = ReplaySession.from_dict(recorder.to_dict())
    with pytest.raises(RecordedCallError, match="provider timed out") as caught:
        replay.call("model", "fake.chat", {"prompt": "x"})
    assert caught.value.recorded_type == "builtins.TimeoutError"
    replay.finish()
    assert calls == ["live"]


def test_recording_validates_before_invoking_and_rejects_non_json_responses():
    calls = []
    recorder = CassetteRecorder()
    with pytest.raises(ValueError, match="JSON-compatible"):
        recorder.call("tool", "lookup", {"bad": object()}, lambda: calls.append(1))
    assert calls == []

    with pytest.raises(ValueError, match="JSON-compatible"):
        recorder.call("tool", "lookup", {}, lambda: object())
    with pytest.raises(ValueError, match="incomplete"):
        recorder.to_dict()


def test_trace_validation_rejects_bad_version_sequence_and_ambiguous_outcome():
    with pytest.raises(ValueError, match="Unsupported cassette format"):
        ReplaySession.from_dict({"format": "old", "interactions": []})
    with pytest.raises(ValueError, match="sequence"):
        ReplaySession.from_dict({"format": FORMAT, "interactions": [{
            "id": "call-000001", "sequence": 1, "boundary": "tool",
            "target": "lookup", "request": {}, "response": None,
        }]})
    with pytest.raises(ValueError, match="exactly one"):
        ReplaySession.from_dict({"format": FORMAT, "interactions": [{
            "id": "call-000001", "sequence": 0, "boundary": "tool",
            "target": "lookup", "request": {}, "response": None, "error": {},
        }]})
