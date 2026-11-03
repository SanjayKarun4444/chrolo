from __future__ import annotations

import pytest

from chrolo.anthropic_adapter import BoundaryAnthropicMessages
from chrolo.cassette import BoundaryContext, CassetteRecorder, RecordedCallError, ReplayMismatch, ReplaySession


class FakeMessage:
    def __init__(self, content=None):
        self.content = content or [
            {"type": "text", "text": "hello"},
            {"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {"id": "T-1"}},
        ]

    def to_dict(self, *, mode="python"):
        assert mode == "json"
        return {"id": "msg_1", "role": "assistant", "content": self.content, "stop_reason": "tool_use"}


class FakeMessages:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result or FakeMessage()
        self.error = error

    def create(self, **request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return self.result


class FakeClient:
    def __init__(self, **kwargs):
        self.messages = FakeMessages(**kwargs)


def test_anthropic_messages_record_and_replay_without_sdk_call():
    live_client = FakeClient()
    recorder = CassetteRecorder()
    live = BoundaryAnthropicMessages(
        live_client, BoundaryContext.recording(recorder), "anthropic:messages:stable-model", sdk_version="test-sdk"
    )
    result = live.create(model="stable-model", max_tokens=128, messages=[{"role": "user", "content": "hello"}])

    replay_client = FakeClient()
    replay = BoundaryAnthropicMessages(
        replay_client,
        BoundaryContext.replaying(ReplaySession.from_dict(recorder.to_dict())),
        "anthropic:messages:stable-model",
        sdk_version="test-sdk",
    )
    replayed = replay.create(model="stable-model", max_tokens=128, messages=[{"role": "user", "content": "hello"}])
    assert replayed == result
    assert replayed.output_text == "hello"
    assert replayed.response["content"][1]["input"] == {"id": "T-1"}
    assert len(live_client.messages.calls) == 1
    assert replay_client.messages.calls == []


def test_anthropic_request_mismatch_fails_without_live_call():
    recorder = CassetteRecorder()
    BoundaryAnthropicMessages(
        FakeClient(), BoundaryContext.recording(recorder), "anthropic:model", sdk_version="test-sdk"
    ).create(model="stable-model", max_tokens=128, messages=[])
    client = FakeClient()
    model = BoundaryAnthropicMessages(
        client,
        BoundaryContext.replaying(ReplaySession.from_dict(recorder.to_dict())),
        "anthropic:model",
        sdk_version="test-sdk",
    )
    with pytest.raises(ReplayMismatch, match="request differs"):
        model.create(model="stable-model", max_tokens=128, messages=[{"role": "user", "content": "changed"}])
    assert client.messages.calls == []


def test_anthropic_streaming_rejected_before_live_call():
    client = FakeClient()
    model = BoundaryAnthropicMessages(
        client, BoundaryContext.recording(CassetteRecorder()), "anthropic:model", sdk_version="test-sdk"
    )
    with pytest.raises(ValueError, match="streaming is not supported"):
        model.create(model="stable-model", max_tokens=128, messages=[], stream=True)
    assert client.messages.calls == []


def test_anthropic_recorded_error_replays_without_calling_sdk():
    recorder = CassetteRecorder()
    with pytest.raises(RuntimeError, match="provider error"):
        BoundaryAnthropicMessages(
            FakeClient(error=RuntimeError("provider error")),
            BoundaryContext.recording(recorder),
            "anthropic:model",
            sdk_version="test-sdk",
        ).create(model="stable-model", max_tokens=128, messages=[])
    client = FakeClient()
    with pytest.raises(RecordedCallError):
        BoundaryAnthropicMessages(
            client,
            BoundaryContext.replaying(ReplaySession.from_dict(recorder.to_dict())),
            "anthropic:model",
            sdk_version="test-sdk",
        ).create(model="stable-model", max_tokens=128, messages=[])
    assert client.messages.calls == []
