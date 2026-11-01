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


def test_baseline_runner_exception_is_distinguished():
    def broken_runner(events):
        raise LookupError("unexpected failure")

    with pytest.raises(RuntimeError, match="Baseline runner raised"):
        minimize(sample(), broken_runner, is_failure)


def test_baseline_oracle_exception_is_distinguished():
    def broken_oracle(result):
        raise LookupError("oracle bug")

    with pytest.raises(RuntimeError, match="Baseline failure oracle raised"):
        minimize(sample(), run, broken_oracle)


def test_candidate_runner_exception_is_not_accepted_as_original_failure():
    events = parse_events([
        {"id": "bug", "kind": "signal"},
        {"id": "noise", "kind": "telemetry"},
    ])

    def runner(candidate):
        if not any(event.id == "bug" for event in candidate):
            raise LookupError("different failure")
        return {"bug": True}

    result = minimize(events, runner, lambda value: value.get("bug") is True)
    assert [event.id for event in result.events] == ["bug"]


def test_event_dependencies_must_be_an_array_of_ids():
    with pytest.raises(ValueError, match="requires must be an array"):
        parse_events([{"id": "a", "kind": "x", "requires": "previous"}])
    with pytest.raises(ValueError, match="requires must be an array"):
        parse_events([{"id": "a", "kind": "x", "requires": [1]}])


def test_event_source_and_entries_must_be_structured():
    with pytest.raises(ValueError, match="Events must be an array"):
        parse_events("not events")
    with pytest.raises(ValueError, match="must be an object"):
        parse_events(["not an event"])
    with pytest.raises(ValueError, match="must contain id and kind"):
        parse_events([{"id": "missing-kind"}])
