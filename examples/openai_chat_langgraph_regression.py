"""Actual OpenAI Chat Completions SDK + LangGraph, with deterministic HTTP/tool fixtures.

No provider request or external tool is contacted. Replay callbacks fail closed.
The application bug selects a closed ticket; the fixed graph checks its status.
"""
from __future__ import annotations

import json
from typing import Any, TypedDict

from chrolo.openai_adapter import BoundaryOpenAIChatCompletions

CALLS = {"record_http": 0, "record_tool": 0, "replay_http": 0, "replay_tool": 0}
FIXTURE_ERROR: str | None = None


def reset_calls():
    for name in CALLS:
        CALLS[name] = 0


class State(TypedDict, total=False):
    events: list[dict[str, Any]]
    selected_closed_ticket: bool


def _build(boundaries, *, fixed=False):
    import httpx
    import openai
    from langgraph.graph import END, START, StateGraph

    tool_definition = {"type": "function", "function": {
        "name": "fetch_ticket", "description": "Fetch a ticket by ID",
        "parameters": {"type": "object", "properties": {"ticket_id": {"type": "integer"}},
                       "required": ["ticket_id"], "additionalProperties": False},
        "strict": True,
    }}

    def http_handler(request):
        CALLS[boundaries.mode + "_http"] += 1
        if boundaries.mode != "record":
            raise AssertionError("Replay attempted an SDK HTTP request")
        assert request.method == "POST" and request.url.path == "/v1/chat/completions"
        assert json.loads(request.content)["tools"] == [tool_definition]
        if FIXTURE_ERROR == "provider":
            return httpx.Response(400, json={"error": {"message": "Fixture request rejected", "type": "invalid_request_error"}})
        return httpx.Response(200, json={
            "id": "chatcmpl_fixture", "created": 1735689600, "object": "chat.completion", "model": "fixture-model",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{"id": "lookup_42", "type": "function",
                    "function": {"name": "fetch_ticket", "arguments": '{"ticket_id":42}'}}]}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        })

    def tool(value):
        CALLS[boundaries.mode + "_tool"] += 1
        if boundaries.mode != "record":
            raise AssertionError("Replay executed a tool callback")
        if FIXTURE_ERROR == "tool" and value == 42:
            raise RuntimeError("Fixture ticket lookup failed")
        return {"ticket_id": value, "status": "closed"}

    def workflow(state):
        selected = False
        with openai.OpenAI(api_key="fixture-only", base_url="https://chrolo-fixture.invalid/v1", max_retries=0,
                           http_client=httpx.Client(transport=httpx.MockTransport(http_handler))) as client:
            provider = BoundaryOpenAIChatCompletions(client, boundaries, "fixture.openai.chat_completions")
            for event in state["events"]:
                if event["kind"] == "probe":
                    boundaries.call("tool", "fixture.diagnostic", {"ticket_id": 0}, lambda: tool(0))
                elif event["kind"] == "trigger":
                    response = provider.create(model="fixture-model", messages=[{"role": "user", "content": "Find ticket 42"}], tools=[tool_definition])
                    call = response.response["choices"][0]["message"]["tool_calls"][0]
                    arguments = json.loads(call["function"]["arguments"])
                    ticket = boundaries.call("tool", "fixture.fetch_ticket", arguments,
                                             lambda: tool(arguments["ticket_id"]))
                    choose = ticket["status"] == "open" if fixed else bool(ticket)
                    selected = choose and ticket["status"] == "closed"
        return {"selected_closed_ticket": selected}

    graph = StateGraph(State)
    graph.add_node("workflow", workflow)
    graph.add_edge(START, "workflow")
    graph.add_edge("workflow", END)
    return graph.compile()


def build_graph(boundaries):
    return _build(boundaries)


def build_fixed_graph(boundaries):
    return _build(boundaries, fixed=True)


def original_failure(result):
    return isinstance(result, dict) and result.get("selected_closed_ticket") is True
