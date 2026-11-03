import pytest

from chrolo.agenttelemetry_benchmark import (
    analyze_trace,
    preserves_injected_fault,
    run_without_injected_fault,
)


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"detected": True, "failure_signature": "tool_failure", "expected_signature": "tool_failure"}, True),
        ({"detected": False, "failure_signature": None, "expected_signature": "tool_failure"}, False),
        ({"detected": True, "failure_signature": "timeout", "expected_signature": "tool_failure"}, False),
    ],
)
def test_agenttelemetry_oracle_requires_the_same_detected_signature(result, expected):
    assert preserves_injected_fault(result) is expected


def test_agenttelemetry_runner_rejects_candidate_without_scenario_context():
    with pytest.raises(ValueError, match="exactly one"):
        analyze_trace([])


def test_agenttelemetry_fixed_runner_rejects_candidate_without_scenario_context():
    with pytest.raises(ValueError, match="exactly one"):
        run_without_injected_fault([])
