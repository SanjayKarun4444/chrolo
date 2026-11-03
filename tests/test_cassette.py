import pytest
import uuid

from chrolo.core import minimize, parse_events
from chrolo.cassette import (
    FORMAT,
    LEGACY_FORMAT,
    BoundaryContext,
    BoundaryTool,
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
    assert trace["header"]["schema_version"] == 2
    assert trace["header"]["chrolo_version"]
    assert uuid.UUID(trace["header"]["execution_id"])
    assert trace["header"]["adapters"] == {"chrolo.boundary": "1"}

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


def test_replay_rejects_changed_or_missing_context_without_consuming_record():
    recorder = CassetteRecorder()
    recorder.call("tool", "lookup", {"id": 1}, lambda: "value", context={"node": "search"})
    trace = recorder.to_dict()
    assert trace["interactions"][0]["context"] == {"node": "search"}

    replay = ReplaySession.from_dict(trace)
    with pytest.raises(ReplayMismatch, match="context differs"):
        replay.call("tool", "lookup", {"id": 1})
    assert replay.consumed == 0

    with pytest.raises(ReplayMismatch, match="context differs"):
        replay.call("tool", "lookup", {"id": 1}, context={"node": "other"})
    assert replay.consumed == 0

    assert replay.call("tool", "lookup", {"id": 1}, context={"node": "search"}) == "value"
    replay.finish()


def test_context_is_validated_before_live_boundary_call():
    calls = []
    recorder = CassetteRecorder()
    with pytest.raises(ValueError, match="context must be a JSON object"):
        recorder.call("tool", "lookup", {}, lambda: calls.append(1), context=["not", "object"])
    assert calls == []


def test_replay_rejects_unexpected_and_unconsumed_calls():
    replay = ReplaySession.from_dict({"format": LEGACY_FORMAT, "interactions": []})
    with pytest.raises(ReplayMismatch, match="cassette is exhausted"):
        replay.call("tool", "lookup", {})

    recorder = CassetteRecorder()
    recorder.call("tool", "lookup", {}, lambda: 1)
    replay = ReplaySession.from_dict(recorder.to_dict())
    with pytest.raises(ReplayMismatch, match="not consumed"):
        replay.finish()


def test_minimization_replay_skips_only_unused_ordered_records_and_reports_used_calls():
    recorder = CassetteRecorder()
    recorder.call("tool", "diagnostic", {"query": "unused"}, lambda: {"ok": True})
    recorder.call("model", "answer", {"prompt": "failure"}, lambda: {"failure": True})
    trace = recorder.to_dict()

    candidate = ReplaySession.from_dict(trace, allow_skips=True)
    assert candidate.call("model", "answer", {"prompt": "failure"}) == {"failure": True}
    candidate.finish()
    assert [item["id"] for item in candidate.used_interactions] == ["call-000002"]

    # Ordinary replay keeps its strict head-of-cassette behavior.
    strict = ReplaySession.from_dict(trace)
    with pytest.raises(ReplayMismatch, match="expected tool boundary"):
        strict.call("model", "answer", {"prompt": "failure"})


def test_minimization_replay_rejects_ambiguous_duplicate_future_calls():
    recorder = CassetteRecorder()
    recorder.call("tool", "diagnostic", {}, lambda: {"ok": True})
    recorder.call("model", "answer", {"prompt": "same"}, lambda: {"answer": 1})
    recorder.call("model", "answer", {"prompt": "same"}, lambda: {"answer": 2})
    candidate = ReplaySession.from_dict(recorder.to_dict(), allow_skips=True)

    with pytest.raises(ReplayMismatch, match="ambiguous repeated interaction"):
        candidate.call("model", "answer", {"prompt": "same"})
    assert candidate.used_interactions == []


def test_minimization_replay_still_rejects_changed_requests_and_reordered_calls():
    recorder = CassetteRecorder()
    recorder.call("tool", "diagnostic", {}, lambda: {"ok": True})
    recorder.call("model", "answer", {"prompt": "recorded"}, lambda: {"answer": 1})
    changed = ReplaySession.from_dict(recorder.to_dict(), allow_skips=True)
    with pytest.raises(ReplayMismatch, match="no matching recorded interaction"):
        changed.call("model", "answer", {"prompt": "changed"})

    recorder.call("tool", "lookup", {"id": 1}, lambda: {"value": 1})
    reordered = ReplaySession.from_dict(recorder.to_dict(), allow_skips=True)
    assert reordered.call("tool", "lookup", {"id": 1}) == {"value": 1}
    with pytest.raises(ReplayMismatch, match="cassette is exhausted|no matching recorded interaction"):
        reordered.call("model", "answer", {"prompt": "recorded"})


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
        ReplaySession.from_dict({"format": LEGACY_FORMAT, "interactions": [{
            "id": "call-000001", "sequence": 1, "boundary": "tool",
            "target": "lookup", "request": {}, "response": None,
        }]})


def test_v2_header_is_required_and_legacy_v1_remains_readable():
    with pytest.raises(ValueError, match="header must be an object"):
        ReplaySession.from_dict({"format": FORMAT, "interactions": []})

    legacy = {
        "format": LEGACY_FORMAT,
        "interactions": [{
            "id": "call-000001", "sequence": 0, "boundary": "tool",
            "target": "lookup", "request": {"id": 1}, "response": {"ok": True},
        }],
    }
    session = ReplaySession.from_dict(legacy)
    assert session.call("tool", "lookup", {"id": 1}) == {"ok": True}
    session.finish()

    legacy_with_context = {
        "format": LEGACY_FORMAT,
        "interactions": [{
            "id": "call-000001", "sequence": 0, "boundary": "tool",
            "target": "lookup", "request": {}, "context": {"node": "tool"},
            "response": {"ok": True},
        }],
    }
    with pytest.raises(ValueError, match="not supported by cassette v1"):
        ReplaySession.from_dict(legacy_with_context)
    with pytest.raises(ValueError, match="exactly one"):
        ReplaySession.from_dict({"format": LEGACY_FORMAT, "interactions": [{
            "id": "call-000001", "sequence": 0, "boundary": "tool",
            "target": "lookup", "request": {}, "response": None, "error": {},
        }]})



def test_minimizer_replays_fresh_cassette_per_candidate_without_live_calls():
    events = parse_events([
        {"id": "boot", "kind": "telemetry", "payload": {}},
        {"id": "search", "kind": "issue_search", "payload": {"query": "permission bug"}},
        {"id": "cache", "kind": "cached_selection", "payload": {"issue_id": 99}, "requires": ["search"]},
        {"id": "log", "kind": "telemetry", "payload": {}},
        {"id": "done", "kind": "telemetry", "payload": {}},
    ])
    live_calls = {"model": 0, "tool": 0}
    recorder = CassetteRecorder()

    def live_model():
        live_calls["model"] += 1
        return {"issue_id": 42}

    def live_tool():
        live_calls["tool"] += 1
        return {"issue_id": 99}

    recorder.call("model", "demo.issue_search", {"query": "permission bug"}, live_model)
    recorder.call("tool", "demo.fetch_issue", {"issue_id": 99}, live_tool)
    trace = recorder.to_dict()
    recorded_call_counts = dict(live_calls)

    def replay_candidate(candidate):
        # A fresh cursor per candidate prevents one minimization check from
        # consuming responses needed by another check.
        replay = ReplaySession.from_dict(trace)
        search = next((event for event in candidate if event.id == "search"), None)
        query = search.payload["query"] if search is not None else ""
        model_result = replay.call(
            "model", "demo.issue_search", {"query": query}
        )
        cache = next((event for event in candidate if event.id == "cache"), None)
        selected_id = (
            cache.payload["issue_id"] if cache is not None else model_result["issue_id"]
        )
        tool_result = replay.call(
            "tool", "demo.fetch_issue", {"issue_id": selected_id}
        )
        replay.finish()
        return {"expected_issue_id": 42, "selected_issue_id": tool_result["issue_id"]}

    result = minimize(
        events,
        replay_candidate,
        lambda value: value["expected_issue_id"] == 42 and value["selected_issue_id"] == 99,
    )

    assert [event.id for event in result.events] == ["search", "cache"]
    assert live_calls == recorded_call_counts


def test_boundary_tool_replays_recorded_result_without_side_effect():
    side_effects = []
    recorder = CassetteRecorder()
    live_tool = BoundaryTool(
        lambda args: side_effects.append(args["id"]) or {"ok": True},
        BoundaryContext.recording(recorder),
        "demo.mutate",
        context={"node": "execute_tool"},
    )
    assert live_tool({"id": 7}) == {"ok": True}
    trace = recorder.to_dict()
    assert side_effects == [7]

    offline_tool = BoundaryTool(
        lambda args: side_effects.append("unexpected"),
        BoundaryContext.replaying(ReplaySession.from_dict(trace)),
        "demo.mutate",
        context={"node": "execute_tool"},
    )
    assert offline_tool({"id": 7}) == {"ok": True}
    assert side_effects == [7]
