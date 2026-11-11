"""Actual Anthropic SDK + LangGraph using local HTTP/tool fixtures only.

The application bug selects a closed ticket; its fix preserves boundary calls.
"""
from __future__ import annotations

import json
from typing import Any, TypedDict

from chrolo.anthropic_adapter import BoundaryAnthropicMessages

CALLS = {"record_http": 0, "record_tool": 0, "replay_http": 0, "replay_tool": 0}
FIXTURE_ERROR: str | None = None


def reset_calls():
    for name in CALLS:
        CALLS[name] = 0


class State(TypedDict, total=False):
    events: list[dict[str, Any]]
    selected_closed_ticket: bool


def _build(boundaries, *, fixed=False):
    import anthropic
    import httpx
    from langgraph.graph import END, START, StateGraph

    tool_definition = {
        "name": "fetch_ticket", "description": "Fetch a ticket by ID",
        "input_schema": {"type": "object", "properties": {"ticket_id": {"type": "integer"}},
                         "required": ["ticket_id"], "additionalProperties": False},
    }

    def http_handler(request):
        CALLS[boundaries.mode + "_http"] += 1
        if boundaries.mode != "record":
            raise AssertionError("Replay attempted an SDK HTTP request")
        assert request.method == "POST" and request.url.path == "/v1/messages"
        assert json.loads(request.content)["tools"] == [tool_definition]
        if FIXTURE_ERROR == "provider":
            return httpx.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "Fixture request rejected"}})
        return httpx.Response(200, json={
            "id": "msg_fixture", "type": "message", "role": "assistant", "model": "fixture-model",
            "content": [{"type": "text", "text": "Fetching ticket."},
                        {"type": "tool_use", "id": "toolu_fixture", "name": "fetch_ticket", "input": {"ticket_id": 42}}],
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 12, "output_tokens": 8},
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
        with anthropic.Anthropic(api_key="fixture-only", base_url="https://chrolo-fixture.invalid", max_retries=0,
                                 http_client=httpx.Client(transport=httpx.MockTransport(http_handler))) as client:
            provider = BoundaryAnthropicMessages(client, boundaries, "fixture.anthropic.messages")
            for event in state["events"]:
                if event["kind"] == "probe":
                    boundaries.call("tool", "fixture.diagnostic", {"ticket_id": 0}, lambda: tool(0))
                elif event["kind"] == "trigger":
                    response = provider.create(model="fixture-model", max_tokens=128,
                                               messages=[{"role": "user", "content": "Find ticket 42"}], tools=[tool_definition])
                    call = next(block for block in response.response["content"] if block["type"] == "tool_use")
                    arguments = call["input"]
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
