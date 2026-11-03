"""Cassette adapter for buffered synchronous Anthropic Messages API calls.

The adapter records JSON-safe message envelopes and returns the same normalized
result during replay. Streaming, async calls, and SDK-typed response
reconstruction are intentionally unsupported.
"""
from __future__ import annotations

import json
import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .cassette import BoundaryContext


@dataclass(frozen=True)
class AnthropicMessageResult:
    """Stable JSON view of an Anthropic Messages API response."""

    response: dict[str, Any]
    output_text: str


class BoundaryAnthropicMessages:
    """Wrap ``client.messages.create`` in Chrolo's explicit model boundary."""

    def __init__(
        self,
        client: Any,
        boundaries: BoundaryContext,
        target: str,
        *,
        context: Mapping[str, Any] | None = None,
        sdk_version: str | None = None,
    ) -> None:
        messages = getattr(client, "messages", None)
        create = getattr(messages, "create", None)
        if not callable(create):
            raise TypeError("client must provide a callable messages.create method")
        if not isinstance(boundaries, BoundaryContext):
            raise TypeError("boundaries must be a BoundaryContext")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("target must be a non-empty stable model identifier")
        if sdk_version is None:
            try:
                sdk_version = version("anthropic")
            except PackageNotFoundError as exc:
                raise ImportError(
                    "Install chrolo-starter[anthropic] to use BoundaryAnthropicMessages"
                ) from exc
        if not isinstance(sdk_version, str) or not sdk_version.strip():
            raise ValueError("sdk_version must be a non-empty string")
        self.client = client
        self.boundaries = boundaries
        self.target = target
        self.context = context
        boundaries.register_adapter("chrolo.anthropic_messages", "1")
        boundaries.register_adapter("anthropic", sdk_version)

    @staticmethod
    def _response_data(response: Any) -> tuple[dict[str, Any], str]:
        if isinstance(response, Mapping):
            data = dict(response)
        elif callable(getattr(response, "to_dict", None)):
            data = response.to_dict(mode="json")
        elif callable(getattr(response, "model_dump", None)):
            data = response.model_dump(mode="json", by_alias=True)
        else:
            raise TypeError("Anthropic response must be a mapping or expose to_dict/model_dump")
        if not isinstance(data, dict):
            raise TypeError("Anthropic response serialization must produce an object")
        normalized = json.loads(json.dumps(data, ensure_ascii=False, allow_nan=False))
        content = normalized.get("content")
        if not isinstance(content, list):
            raise TypeError("Anthropic response content must be an array")
        output_text = "".join(
            item["text"] for item in content
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
        )
        return normalized, output_text

    def create(self, **request: Any) -> AnthropicMessageResult:
        """Create a buffered message or return its recorded JSON during replay."""
        if request.get("stream") is True:
            raise ValueError("streaming is not supported by BoundaryAnthropicMessages")

        def live() -> dict[str, Any]:
            sdk_response = self.client.messages.create(**request)
            if inspect.isawaitable(sdk_response):
                close = getattr(sdk_response, "close", None)
                if callable(close):
                    close()
                raise TypeError("async calls are not supported by BoundaryAnthropicMessages")
            response, output_text = self._response_data(sdk_response)
            return {"response": response, "output_text": output_text}

        recorded = self.boundaries.call("model", self.target, request, live, context=self.context)
        if not isinstance(recorded, dict) or not isinstance(recorded.get("response"), dict):
            raise ValueError("Recorded Anthropic Messages result has an invalid shape")
        output_text = recorded.get("output_text")
        if not isinstance(output_text, str):
            raise ValueError("Recorded Anthropic output_text must be a string")
        return AnthropicMessageResult(response=recorded["response"], output_text=output_text)
