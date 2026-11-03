from __future__ import annotations

import pytest

from chrolo.cassette import (
    BoundaryContext,
    CassetteRecorder,
    RecordedCallError,
    ReplayMismatch,
    ReplaySession,
)
from chrolo.openai_adapter import BoundaryOpenAIResponses


class FakeResponse:
    def __init__(self, content="hello"):
        self.output_text = content

    def to_dict(self, *, mode="python"):
        assert mode == "json"
        return {
            "id": "resp_test",
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": self.output_text}]}],
        }


class FakeResponses:
    def __init__(self, *, result=None, error=None):
        self.calls = []
        self.result = result or FakeResponse()
        self.error = error

    def create(self, **request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return self.result


class FakeClient:
    def __init__(self, **kwargs):
        self.responses = FakeResponses(**kwargs)


def test_record_then_replay_returns_same_normalized_result_without_sdk_call():
    client = FakeClient()
    recorder = CassetteRecorder()
    live = BoundaryOpenAIResponses(
        client,
        BoundaryContext.recording(recorder),
        "openai:responses:stable-model",
        context={"node": "planner"},
        sdk_version="test-sdk",
    )
    recorded = live.create(model="stable-model", input="hello")
    trace = recorder.to_dict()

    replay_client = FakeClient()
    replay = BoundaryOpenAIResponses(
        replay_client,
        BoundaryContext.replaying(ReplaySession.from_dict(trace)),
        "openai:responses:stable-model",
        context={"node": "planner"},
        sdk_version="test-sdk",
    )
    replayed = replay.create(model="stable-model", input="hello")
    assert replayed == recorded
    assert replayed.output_text == "hello"
    assert replayed.response["output"][0]["content"][0]["text"] == "hello"
    assert len(client.responses.calls) == 1
    assert replay_client.responses.calls == []


def test_replay_request_mismatch_fails_without_calling_sdk():
    recorder = CassetteRecorder()
    BoundaryOpenAIResponses(
        FakeClient(), BoundaryContext.recording(recorder), "openai:model", sdk_version="test-sdk"
    ).create(model="stable-model", input="hello")
    client = FakeClient()
    replay = BoundaryOpenAIResponses(
        client,
        BoundaryContext.replaying(ReplaySession.from_dict(recorder.to_dict())),
        "openai:model",
        sdk_version="test-sdk",
    )
    with pytest.raises(ReplayMismatch, match="request differs"):
        replay.create(model="stable-model", input="different")
    assert client.responses.calls == []


def test_recorded_sdk_error_replays_without_calling_sdk():
    recorder = CassetteRecorder()
    live = BoundaryOpenAIResponses(
        FakeClient(error=RuntimeError("provider error")),
        BoundaryContext.recording(recorder),
        "openai:model",
        sdk_version="test-sdk",
    )
    with pytest.raises(RuntimeError, match="provider error"):
        live.create(model="stable-model", input="hello")
    client = FakeClient()
    replay = BoundaryOpenAIResponses(
        client,
        BoundaryContext.replaying(ReplaySession.from_dict(recorder.to_dict())),
        "openai:model",
        sdk_version="test-sdk",
    )
    with pytest.raises(RecordedCallError, match="provider error"):
        replay.create(model="stable-model", input="hello")
    assert client.responses.calls == []


def test_streaming_is_rejected_before_live_call():
    client = FakeClient()
    recorder = CassetteRecorder()
    model = BoundaryOpenAIResponses(
        client, BoundaryContext.recording(recorder), "openai:model", sdk_version="test-sdk"
    )
    with pytest.raises(ValueError, match="streaming is not supported"):
        model.create(model="stable-model", input="hello", stream=True)
    assert client.responses.calls == []


def test_json_incompatible_response_is_rejected_during_recording():
    client = FakeClient(result={"id": "bad", "value": object()})
    model = BoundaryOpenAIResponses(
        client, BoundaryContext.recording(CassetteRecorder()), "openai:model", sdk_version="test-sdk"
    )
    with pytest.raises(TypeError):
        model.create(model="stable-model", input="hello")


def test_openai_sdk_response_serializes_and_replays_without_losing_output():
    openai = pytest.importorskip("openai")
    from openai.types.responses import Response

    response = Response.model_validate({
        "id": "resp_123",
        "created_at": 1735689600,
        "model": "gpt-4.1",
        "object": "response",
        "output": [{
            "id": "msg_123",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hello", "annotations": []}],
        }],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "status": "completed",
    })
    client = FakeClient(result=response)
    recorder = CassetteRecorder()
    live = BoundaryOpenAIResponses(
        client,
        BoundaryContext.recording(recorder),
        "openai:responses:gpt-4.1",
        sdk_version=openai.__version__,
    )
    recorded = live.create(model="gpt-4.1", input="hello")
    assert recorded.output_text == "hello"

    offline_client = FakeClient()
    replay = BoundaryOpenAIResponses(
        offline_client,
        BoundaryContext.replaying(ReplaySession.from_dict(recorder.to_dict())),
        "openai:responses:gpt-4.1",
        sdk_version=openai.__version__,
    )
    replayed = replay.create(model="gpt-4.1", input="hello")
    assert replayed == recorded
    assert replayed.response == response.to_dict(mode="json")
    assert offline_client.responses.calls == []
