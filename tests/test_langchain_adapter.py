from __future__ import annotations

import pytest

langchain_core = pytest.importorskip("langchain_core")
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from chrolo.cassette import BoundaryContext, CassetteRecorder, RecordedCallError, ReplaySession
from chrolo.langchain_adapter import BoundaryChatModel


class FakeModel(BaseChatModel):
    _calls: int = PrivateAttr(default=0)
    _fail: bool = PrivateAttr(default=False)

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._calls += 1
        if self._fail:
            raise RuntimeError("provider unavailable")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])


def test_record_then_replay_never_invokes_model_again():
    model = FakeModel()
    recorder = CassetteRecorder()
    live = BoundaryChatModel(
        model, BoundaryContext.recording(recorder), "fake:stable", context={"node": "planner"}
    )
    response = live.invoke([HumanMessage(content="hello")], temperature=0)
    trace = recorder.to_dict()

    replay_model = FakeModel()
    replay = BoundaryChatModel(
        replay_model,
        BoundaryContext.replaying(ReplaySession.from_dict(trace)),
        "fake:stable",
        context={"node": "planner"},
    )
    assert replay.invoke([HumanMessage(content="hello")], temperature=0) == response
    assert model._calls == 1
    assert replay_model._calls == 0


def test_request_mismatch_fails_before_provider_call():
    model = FakeModel()
    recorder = CassetteRecorder()
    BoundaryChatModel(model, BoundaryContext.recording(recorder), "fake:stable").invoke(
        [HumanMessage(content="hello")]
    )
    replay_model = FakeModel()
    replay = BoundaryChatModel(
        replay_model,
        BoundaryContext.replaying(ReplaySession.from_dict(recorder.to_dict())),
        "fake:stable",
    )
    with pytest.raises(Exception, match="request differs"):
        replay.invoke([HumanMessage(content="different")])
    assert replay_model._calls == 0


def test_recorded_provider_error_replays_without_live_call():
    model = FakeModel()
    model._fail = True
    recorder = CassetteRecorder()
    live = BoundaryChatModel(model, BoundaryContext.recording(recorder), "fake:stable")
    with pytest.raises(RuntimeError, match="provider unavailable"):
        live.invoke([HumanMessage(content="hello")])

    replay_model = FakeModel()
    replay = BoundaryChatModel(
        replay_model,
        BoundaryContext.replaying(ReplaySession.from_dict(recorder.to_dict())),
        "fake:stable",
    )
    with pytest.raises(RecordedCallError, match="provider unavailable"):
        replay.invoke([HumanMessage(content="hello")])
    assert replay_model._calls == 0
