"""Cassette adapter for buffered synchronous OpenAI Responses API calls.

This adapter stores the JSON response envelope and exposes the same normalized
``OpenAIResponsesResult`` during recording and replay. It does not implement
streaming, asynchronous calls, or reconstruction of the SDK's typed response.
"""
from __future__ import annotations

import json
import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .cassette import BoundaryContext


def _require_sync_response(response: Any, label: str) -> Any:
    if inspect.isawaitable(response):
        close = getattr(response, "close", None)
        if callable(close):
            close()
        raise TypeError(f"async calls are not supported by {label}")
    return response


@dataclass(frozen=True)
class OpenAIResponsesResult:
    """Stable JSON view of a Responses API result for recording and replay."""

    response: dict[str, Any]
    output_text: str | None


class BoundaryOpenAIResponses:
    """Wrap ``client.responses.create`` in Chrolo's explicit model boundary.

    The caller supplies a stable model target, such as ``openai:responses:gpt-x``.
    The wrapped client's live method is called only in recording mode. Replay
    returns normalized JSON data and never contacts the provider.
    """

    def __init__(
        self,
        client: Any,
        boundaries: BoundaryContext,
        target: str,
        *,
        context: Mapping[str, Any] | None = None,
        sdk_version: str | None = None,
    ) -> None:
        responses = getattr(client, "responses", None)
        create = getattr(responses, "create", None)
        if not callable(create):
            raise TypeError("client must provide a callable responses.create method")
        if not isinstance(boundaries, BoundaryContext):
            raise TypeError("boundaries must be a BoundaryContext")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("target must be a non-empty stable model identifier")
        if sdk_version is None:
            try:
                sdk_version = version("openai")
            except PackageNotFoundError as exc:
                raise ImportError("Install chrolo-starter[openai] to use BoundaryOpenAIResponses") from exc
        if not isinstance(sdk_version, str) or not sdk_version.strip():
            raise ValueError("sdk_version must be a non-empty string")
        self.client = client
        self.boundaries = boundaries
        self.target = target
        self.context = context
        boundaries.register_adapter("chrolo.openai_responses", "1")
        boundaries.register_adapter("openai", sdk_version)

    @staticmethod
    def _response_data(response: Any) -> tuple[dict[str, Any], str | None]:
        if isinstance(response, Mapping):
            data = dict(response)
        elif callable(getattr(response, "to_dict", None)):
            data = response.to_dict(mode="json")
        elif callable(getattr(response, "model_dump", None)):
            data = response.model_dump(mode="json", by_alias=True)
        else:
            raise TypeError("OpenAI response must be a mapping or expose to_dict/model_dump")
        if not isinstance(data, dict):
            raise TypeError("OpenAI response serialization must produce an object")
        # Round-trip through JSON so unsupported values and NaN fail at capture
        # time rather than producing a cassette that cannot be replayed.
        normalized = json.loads(json.dumps(data, ensure_ascii=False, allow_nan=False))
        output_text = getattr(response, "output_text", normalized.get("output_text"))
        if output_text is not None and not isinstance(output_text, str):
            raise TypeError("OpenAI response output_text must be a string or None")
        return normalized, output_text

    def create(self, **request: Any) -> OpenAIResponsesResult:
        """Create a buffered response or return its exact recorded JSON during replay."""
        if request.get("stream") is True:
            raise ValueError("streaming is not supported by BoundaryOpenAIResponses")

        def live() -> dict[str, Any]:
            sdk_response = _require_sync_response(
                self.client.responses.create(**request), "BoundaryOpenAIResponses"
            )
            response_data, output_text = self._response_data(sdk_response)
            return {"response": response_data, "output_text": output_text}

        recorded = self.boundaries.call(
            "model", self.target, request, live, context=self.context
        )
        if not isinstance(recorded, dict) or not isinstance(recorded.get("response"), dict):
            raise ValueError("Recorded OpenAI Responses result has an invalid shape")
        output_text = recorded.get("output_text")
        if output_text is not None and not isinstance(output_text, str):
            raise ValueError("Recorded OpenAI output_text must be a string or None")
        return OpenAIResponsesResult(response=recorded["response"], output_text=output_text)


@dataclass(frozen=True)
class OpenAIChatCompletionsResult:
    """Stable JSON view of a Chat Completions result for record and replay."""

    response: dict[str, Any]
    output_text: str | None


class BoundaryOpenAIChatCompletions:
    """Wrap buffered ``client.chat.completions.create`` calls in a cassette.

    This returns a JSON view rather than the SDK's typed object. Tool-call data
    remains available in ``result.response``. Streaming and async are excluded.
    """

    def __init__(
        self,
        client: Any,
        boundaries: BoundaryContext,
        target: str,
        *,
        context: Mapping[str, Any] | None = None,
        sdk_version: str | None = None,
    ) -> None:
        chat = getattr(client, "chat", None)
        completions = getattr(chat, "completions", None)
        create = getattr(completions, "create", None)
        if not callable(create):
            raise TypeError("client must provide a callable chat.completions.create method")
        if not isinstance(boundaries, BoundaryContext):
            raise TypeError("boundaries must be a BoundaryContext")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("target must be a non-empty stable model identifier")
        if sdk_version is None:
            try:
                sdk_version = version("openai")
            except PackageNotFoundError as exc:
                raise ImportError("Install chrolo-starter[openai] to use BoundaryOpenAIChatCompletions") from exc
        if not isinstance(sdk_version, str) or not sdk_version.strip():
            raise ValueError("sdk_version must be a non-empty string")
        self.client = client
        self.boundaries = boundaries
        self.target = target
        self.context = context
        boundaries.register_adapter("chrolo.openai_chat_completions", "1")
        boundaries.register_adapter("openai", sdk_version)

    def create(self, **request: Any) -> OpenAIChatCompletionsResult:
        """Create a buffered completion or return its recorded JSON during replay."""
        if request.get("stream") is True:
            raise ValueError("streaming is not supported by BoundaryOpenAIChatCompletions")

        def live() -> dict[str, Any]:
            sdk_response = _require_sync_response(
                self.client.chat.completions.create(**request), "BoundaryOpenAIChatCompletions"
            )
            response_data, _ = BoundaryOpenAIResponses._response_data(sdk_response)
            output_text = None
            choices = response_data.get("choices")
            if isinstance(choices, list) and choices:
                message = choices[0].get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    output_text = message["content"]
            return {"response": response_data, "output_text": output_text}

        recorded = self.boundaries.call(
            "model", self.target, request, live, context=self.context
        )
        if not isinstance(recorded, dict) or not isinstance(recorded.get("response"), dict):
            raise ValueError("Recorded OpenAI Chat Completions result has an invalid shape")
        output_text = recorded.get("output_text")
        if output_text is not None and not isinstance(output_text, str):
            raise ValueError("Recorded OpenAI output_text must be a string or None")
        return OpenAIChatCompletionsResult(response=recorded["response"], output_text=output_text)
