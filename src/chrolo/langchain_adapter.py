"""Explicit, cassette-backed boundary for LangChain chat models.

Calls are buffered through ``BaseChatModel.generate``. Streaming is not
supported by this adapter; use it around non-streaming model invocations.
"""
from __future__ import annotations

from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .cassette import BoundaryContext


class BoundaryChatModel:
    """Wrap a LangChain chat model so calls can be recorded and replayed offline.

    LangChain is an optional dependency. The wrapped model is invoked only
    while recording; a replay context has no provider fallback.
    """

    def __init__(
        self,
        model: Any,
        boundaries: BoundaryContext,
        target: str,
        *,
        context: Mapping[str, Any] | None = None,
    ):
        try:
            from langchain_core.language_models.chat_models import BaseChatModel
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ImportError("Install chrolo-starter[langchain] to use BoundaryChatModel") from exc
        if not isinstance(model, BaseChatModel):
            raise TypeError("model must be a LangChain BaseChatModel")
        if not isinstance(boundaries, BoundaryContext):
            raise TypeError("boundaries must be a BoundaryContext")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("target must be a non-empty stable model identifier")
        self.model = model
        self.boundaries = boundaries
        self.target = target
        self.context = context
        try:
            core_version = version("langchain-core")
        except PackageNotFoundError:  # pragma: no cover - malformed optional install
            core_version = "unknown"
        boundaries.register_adapter("chrolo.langchain_chat", "1")
        boundaries.register_adapter("langchain-core", core_version)

    def invoke(self, messages: Any, *, stop: list[str] | None = None, **kwargs: Any) -> Any:
        """Invoke once while recording, or return the matching offline response."""
        from langchain_core.messages import message_to_dict, messages_from_dict
        from langchain_core.outputs import ChatGeneration, ChatResult

        if not isinstance(messages, (list, tuple)):
            raise TypeError("messages must be a list of LangChain messages")
        request = {
            "messages": [message_to_dict(message) for message in messages],
            "stop": stop,
            "kwargs": kwargs,
        }

        def live() -> dict[str, Any]:
            result = self.model.generate([list(messages)], stop=stop, **kwargs)
            return {
                "generations": [
                    {
                        "message": message_to_dict(generation.message),
                        "generation_info": generation.generation_info,
                    }
                    for generation in result.generations[0]
                ],
                "llm_output": result.llm_output,
            }

        response = self.boundaries.call("model", self.target, request, live, context=self.context)
        generations = [
            ChatGeneration(
                message=messages_from_dict([item["message"]])[0],
                generation_info=item["generation_info"],
            )
            for item in response["generations"]
        ]
        return ChatResult(generations=generations, llm_output=response["llm_output"]).generations[0].message
