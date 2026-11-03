import json

import pytest

from chrolo.openhands import extract_actions


def _call(name, arguments):
    return {"type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def test_extract_actions_preserves_order_and_only_action_arguments():
    row = {"trajectory": [
        {"role": "assistant", "content": "private model prose", "tool_calls": [
            _call("think", {"thought": "omitted"}),
            _call("execute_bash", {"command": "echo safe"}),
        ]},
        {"role": "tool", "name": "execute_bash", "content": "output is not replay input", "tool_calls": None},
        {"role": "assistant", "content": "", "tool_calls": [
            _call("str_replace_editor", {"command": "str_replace", "path": "a.py", "old_str": "x", "new_str": "y"}),
        ]},
    ]}

    assert extract_actions(row) == [
        {"id": "action-0000", "kind": "execute_bash", "payload": {"arguments": {"command": "echo safe"}}},
        {"id": "action-0001", "kind": "str_replace_editor", "payload": {"arguments": {
            "command": "str_replace", "path": "a.py", "old_str": "x", "new_str": "y",
        }}},
    ]


@pytest.mark.parametrize("arguments", ["not json", "[]", "null"])
def test_rejects_malformed_action_arguments(arguments):
    row = {"trajectory": [{"tool_calls": [{
        "function": {"name": "execute_bash", "arguments": arguments},
    }]}]}
    with pytest.raises(ValueError, match="Action arguments"):
        extract_actions(row)


def test_rejects_invalid_trajectory_shape():
    with pytest.raises(ValueError, match="trajectory must be an array"):
        extract_actions({"trajectory": "[]"})
