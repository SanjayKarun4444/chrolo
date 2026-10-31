import pytest
from chrolo.core import minimize, parse_events, remove_cascade
from examples.buggy_agent import run, is_failure


def sample():
    return parse_events([
        {"id": "noise1", "kind": "telemetry", "payload": {}},
        {"id": "search", "kind": "issue_search", "payload": {"correct_issue_id": 42}},
        {"id": "stale", "kind": "cached_selection", "payload": {"issue_id": 99}, "requires": ["search"]},
        {"id": "noise2", "kind": "telemetry", "payload": {}}
    ])


def test_minimizer_keeps_dependency_and_signature():
    reduced = minimize(sample(), run, is_failure)
    assert [e.id for e in reduced.events] == ["search", "stale"]
    assert is_failure(run(reduced.events))


def test_cascade_removal():
    assert remove_cascade(sample(), {"search"}) == [sample()[0], sample()[3]]


def test_baseline_must_fail():
    with pytest.raises(ValueError, match="Baseline does not reproduce"):
        minimize(sample(), run, lambda x: x["selected_issue_id"] == -1)


def test_dependency_validation():
    with pytest.raises(ValueError, match="absent or later"):
        parse_events([{"id": "A", "kind": "x", "requires": ["B"]}])


def test_duplicate_ids():
    with pytest.raises(ValueError, match="Duplicate"):
        parse_events([{"id": "a", "kind": "x"}, {"id": "a", "kind": "x"}])
