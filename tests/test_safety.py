import json
import subprocess
import sys
from pathlib import Path

import pytest

from chrolo.adapters_langgraph import validate_capture
from chrolo.cassette import FORMAT as CASSETTE_FORMAT, ReplaySession
from chrolo.benchmark import run_benchmarks, run_dataset
from chrolo.web import PAGE, handle_api
from chrolo.safety import REDACTED, filter_trace, redact_trace, scan_trace, summarize_trace

ROOT = Path(__file__).resolve().parents[1]


def sample_trace():
    return {
        "format": "chrolo.langgraph.v1",
        "graph": "examples.agent:build_graph",
        "event_key": "events",
        "initial_state": {"api_token": "secret-123"},
        "events": [{"id": "e1", "kind": "message", "payload": {"text": "private prompt"}}],
        "observations": {"state_snapshots": [{"api_token": "secret-123"}]},
        "interactions": {
            "format": CASSETTE_FORMAT,
            "header": {
                "schema_version": 2,
                "chrolo_version": "0.2.0",
                "captured_at": "2026-11-02T00:00:00Z",
                "execution_id": "00000000-0000-4000-8000-000000000001",
                "adapters": {"chrolo.boundary": "1"},
            },
            "interactions": [{
                "id": "call-000001", "sequence": 0, "boundary": "model",
                "target": "fake.chat", "request": {"authorization": "Bearer secret-123"},
                "response": {"text": "private answer"},
            }],
        },
    }


def test_redaction_supports_json_pointer_and_wildcards_without_mutating_input():
    original = sample_trace()
    redacted = redact_trace(original, [
        "/initial_state/api_token",
        "/interactions/interactions/*/request/authorization",
        "/observations/state_snapshots/*/api_token",
    ])

    assert original["initial_state"]["api_token"] == "secret-123"
    assert redacted["initial_state"]["api_token"] == REDACTED
    assert redacted["interactions"]["interactions"][0]["request"]["authorization"] == REDACTED
    assert redacted["observations"]["state_snapshots"][0]["api_token"] == REDACTED
    assert redacted["redaction"]["field_count"] == 3
    assert redacted["redaction"]["replayable"] is False
    serialized = json.dumps(redacted)
    assert "secret-123" not in serialized


def test_redaction_path_must_match_and_pointer_escapes_are_validated():
    with pytest.raises(ValueError, match="matched no fields"):
        redact_trace(sample_trace(), ["/initial_state/missing"])
    with pytest.raises(ValueError, match="Invalid JSON Pointer escape"):
        redact_trace(sample_trace(), ["/initial_state/bad~2key"])


def test_redacted_capture_is_rejected_before_replay():
    sanitized = redact_trace(sample_trace(), ["/interactions/interactions/0/request/authorization"])
    assert sanitized["interactions"]["redaction"]["fields"] == ["/interactions/0/request/authorization"]
    with pytest.raises(ValueError, match="cannot be replayed safely"):
        validate_capture(sanitized)
    with pytest.raises(ValueError, match="Redacted cassette cannot be replayed safely"):
        ReplaySession.from_dict(sanitized["interactions"])


def test_replay_rejects_redaction_marker_even_if_it_claims_replayable():
    cassette = sample_trace()["interactions"] | {
        "redaction": {"format": "chrolo.redaction.v1", "replayable": True},
    }
    with pytest.raises(ValueError, match="Redacted cassette cannot be replayed safely"):
        ReplaySession.from_dict(cassette)

    capture = sample_trace() | {
        "redaction": {"format": "chrolo.redaction.v1", "replayable": True},
    }
    with pytest.raises(ValueError, match="cannot be replayed safely"):
        validate_capture(capture)

    malformed_marker = sample_trace()["interactions"] | {"redaction": None}
    with pytest.raises(ValueError, match="redaction report is invalid"):
        ReplaySession.from_dict(malformed_marker)


def test_filtering_supports_include_exclude_and_wildcard_paths():
    original = {
        "initial_state": {"keep": "safe", "drop": "private"},
        "events": [
            {"id": "a", "payload": {"public": "one", "token": "secret-a"}},
            {"id": "b", "payload": {"public": "two", "token": "secret-b"}},
        ],
    }
    filtered = filter_trace(
        original,
        include_paths=["/initial_state", "/events"],
        exclude_paths=["/initial_state/drop", "/events/*/payload/token"],
    )

    assert original["initial_state"]["drop"] == "private"
    assert filtered["initial_state"] == {"keep": "safe"}
    assert filtered["events"] == [
        {"id": "a", "payload": {"public": "one"}},
        {"id": "b", "payload": {"public": "two"}},
    ]
    assert filtered["filtering"]["replayable"] is False
    serialized = json.dumps(filtered)
    for secret in ("private", "secret-a", "secret-b"):
        assert secret not in serialized


def test_filtered_capture_and_cassette_are_rejected_for_replay():
    filtered_capture = filter_trace(sample_trace(), exclude_paths=["/events/*/payload/text"])
    with pytest.raises(ValueError, match="filtered data"):
        validate_capture(filtered_capture)

    cassette = sample_trace()["interactions"] | {
        "filtering": {"format": "chrolo.filtering.v1", "replayable": False},
    }
    with pytest.raises(ValueError, match="Filtered cassette cannot be replayed safely"):
        ReplaySession.from_dict(cassette)


def test_safe_summary_contains_counts_but_no_trace_payloads():
    summary = summarize_trace(sample_trace())
    serialized = json.dumps(summary)
    assert summary["event_count"] == 1
    assert summary["interaction_count"] == 1
    assert summary["interaction_format"] == CASSETTE_FORMAT
    assert summary["interaction_provenance"] == {
        "chrolo_version": "0.2.0",
        "captured_at": "2026-11-02T00:00:00Z",
        "adapters": {"chrolo.boundary": "1"},
    }
    assert summary["interactions_by_boundary"] == {"model": 1, "tool": 0}
    assert summary["state_snapshot_count"] == 1
    assert summary["trace_size_bytes"] > 0
    for secret in ("secret-123", "private prompt", "private answer", "Bearer"):
        assert secret not in serialized


def test_summary_size_counts_utf8_bytes_and_validates_explicit_size():
    trace = {"events": [{"payload": "雪"}]}
    encoded = json.dumps(trace, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert summarize_trace(trace)["trace_size_bytes"] == len(encoded)
    assert summarize_trace(trace, trace_size_bytes=123)["trace_size_bytes"] == 123
    with pytest.raises(ValueError, match="non-negative integer"):
        summarize_trace(trace, trace_size_bytes=-1)


def test_cli_inspect_and_redact_write_payload_free_output(tmp_path):
    source = tmp_path / "trace.json"
    output = tmp_path / "sanitized.json"
    source.write_text(json.dumps(sample_trace()), encoding="utf-8")
    cli = [sys.executable, "-m", "chrolo"]

    inspected = subprocess.run(
        cli + ["inspect", str(source)], cwd=ROOT, capture_output=True, text=True
    )
    assert inspected.returncode == 0, inspected.stderr
    assert "secret-123" not in inspected.stdout
    assert '"interaction_count": 1' in inspected.stdout
    assert f'"trace_size_bytes": {source.stat().st_size}' in inspected.stdout

    redacted = subprocess.run(
        cli + [
            "redact", str(source), "--path", "/initial_state/api_token",
            "--path", "/interactions/interactions/*/request/authorization",
            "--path", "/observations/state_snapshots/*/api_token",
            "--output", str(output),
        ], cwd=ROOT, capture_output=True, text=True,
    )
    assert redacted.returncode == 0, redacted.stderr
    contents = output.read_text(encoding="utf-8")
    assert "secret-123" not in contents
    assert "Bearer" not in contents
    assert "non-replayable" in redacted.stdout


def test_cli_capture_filter_flags_write_non_replayable_projection(tmp_path):
    source = ROOT / "examples" / "langgraph_input.json"
    output = tmp_path / "filtered.json"
    result = subprocess.run(
        [
            sys.executable, "-m", "chrolo", "capture",
            "--graph", "examples.fake_graph:build_graph",
            "--input", str(source), "--output", str(output),
            "--include-path", "/events",
            "--exclude-path", "/events/*/payload/message",
        ],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    filtered = json.loads(output.read_text(encoding="utf-8"))
    assert "initial_state" not in filtered
    assert filtered["filtering"]["replayable"] is False
    assert "non-replayable" in result.stdout


def test_synthetic_benchmark_reports_reduction_and_safety_contracts():
    report = run_benchmarks(repeat=4)
    scenarios = report["scenarios"]
    assert report["synthetic_only"] is True
    assert scenarios["irrelevant_event_minimization"]["failure_signature_preserved"] is True
    assert scenarios["irrelevant_event_minimization"]["remaining_events"] == 1
    assert scenarios["irrelevant_event_minimization"]["naive_single_pass_events"] == 1
    assert scenarios["model_tool_cassette_minimization"]["failure_signature_preserved"] is True
    assert scenarios["model_tool_cassette_minimization"]["live_boundary_calls_during_replay"] == {
        "model": 0, "tool": 0,
    }
    assert scenarios["model_tool_cassette_minimization"]["original_interactions"] == 3
    assert scenarios["model_tool_cassette_minimization"]["remaining_interactions"] == 2
    assert scenarios["model_tool_cassette_minimization"]["pruned_interaction_ids"] == ["call-000001"]
    assert scenarios["model_tool_cassette_minimization"]["minimized_cassette_strict_replay_preserved_signature"] is True
    assert scenarios["safety_and_failure_classification"] == {
        "changed_request_rejected": True,
        "unrelated_exception_rejected": True,
        "redacted_cassette_refused": True,
        "redacted_field_count": 3,
    }
    assert scenarios["unwrapped_nondeterminism"]["reproduction_rate"] == 0.5
    assert scenarios["simulated_fix"]["regression_test_detects_fix"] is True


def test_user_dataset_benchmark_runs_stateful_and_exception_cases_against_baseline():
    dataset = json.loads((ROOT / "examples" / "benchmark_dataset.json").read_text(encoding="utf-8"))
    dataset["metadata"] = {"source": "example", "source_commit": "abc123", "private_note": "do not echo"}
    report = run_dataset(dataset, repeat=3)
    assert report["dataset_code_is_sandboxed"] is False
    assert report["dataset_metadata"] == {"source": "example", "source_commit": "abc123"}
    assert report["aggregate"]["case_count"] == 2
    assert report["aggregate"]["mean_reproduction_rate"] == 1.0
    assert report["aggregate"]["fix_sensitivity_coverage"] == 2
    assert report["aggregate"]["fix_sensitivity_rate"] == 1.0
    case = report["scenarios"]["synthetic_stateful_issue_selection"]
    assert case["failure_signature_preserved"] is True
    assert case["remaining_events"] == 2
    assert case["naive_single_pass_events"] == 2
    assert case["regression_sensitivity"] is True
    assert case["failure_after_simulated_fix"] is False

    exception_case = report["scenarios"]["captured_exception_failure"]
    assert exception_case["reproduction_rate"] == 1.0
    assert exception_case["failure_signature_preserved"] is True
    assert exception_case["remaining_events"] == 1
    assert exception_case["one_minimal_under_tested_cascade_deletions"] is True
    assert exception_case["regression_sensitivity"] is True
    assert exception_case["failure_after_simulated_fix"] is False


def test_web_api_returns_metadata_and_sanitized_copies_without_mutating_input():
    trace = sample_trace()
    summary = handle_api("/api/inspect", {"trace": trace, "size_bytes": 321})
    assert summary["trace_size_bytes"] == 321
    assert "secret-123" not in json.dumps(summary)

    filtered = handle_api("/api/filter", {"trace": trace, "exclude_paths": ["/initial_state/api_token"]})
    assert "api_token" not in filtered["initial_state"]
    assert trace["initial_state"]["api_token"] == "secret-123"
    assert filtered["filtering"]["replayable"] is False


def test_secret_scan_reports_paths_and_reasons_without_values():
    token = "AKIA1234567890ABCDEF"
    bearer = "Bearer abcdefghijklmnopqrstuvwxyz012345"
    trace = {
        "initial_state": {"api/token": token, "prompt": f"Use {bearer} for this request"},
        "events": [{"id": "e1", "kind": "config", "payload": {"password": "local-pass"}}],
    }

    report = scan_trace(trace)

    assert report["candidate_count"] == 3
    findings = {item["path"]: item["reasons"] for item in report["findings"]}
    assert findings["/initial_state/api~1token"] == ["aws-access-key", "sensitive-field-name"]
    assert findings["/initial_state/prompt"] == ["bearer-token"]
    assert findings["/events/0/payload/password"] == ["sensitive-field-name"]
    serialized = json.dumps(report)
    assert token not in serialized
    assert bearer not in serialized
    assert "local-pass" not in serialized


def test_web_page_has_responsive_upload_and_sanitization_controls():
    assert 'name="viewport"' in PAGE
    assert 'type="file"' in PAGE
    assert "max-width:560px" in PAGE
    assert "Redact and download" in PAGE
    assert "Filter and download" in PAGE
    assert "Scan for likely secrets" in PAGE
    assert "Add to redaction list" in PAGE
    assert 'id="clearData"' in PAGE
    assert "trace=null;file.value=''" in PAGE
    assert "pageGeneration++" in PAGE
    assert "Page data cleared." in PAGE
