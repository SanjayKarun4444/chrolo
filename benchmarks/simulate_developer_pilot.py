"""Simulate a new developer's invoice-retry regression using an installed wheel.

All payment/provider data is synthetic. No real payments or provider calls run.
Run with the wheel environment's Python and --output-dir for retained evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


AGENT = '''"""Synthetic invoice processing project with a retry-idempotency bug."""
def apply_receipt(ledger, receipt):
    ledger.append(receipt)  # BUG: the same provider receipt can arrive twice.


class InvoiceAgent:
    def __init__(self, boundaries):
        self.boundaries = boundaries

    def invoke(self, state):
        ledger = []

        def recorded(value):
            if self.boundaries.mode != "record":
                raise AssertionError("Live callback executed during replay")
            with open("recording_callbacks.log", "a", encoding="utf-8") as stream:
                stream.write("callback\\n")
            return value

        for event in state["events"]:
            if event["kind"] == "health":
                self.boundaries.call("tool", "health", {}, lambda: recorded({"ok": True}))
            elif event["kind"] == "receipt":
                receipt = self.boundaries.call(
                    "tool", "payments.lookup_receipt", event["payload"],
                    lambda: recorded({"receipt_id": "receipt-17", "invoice": "invoice-8", "cents": 2500}))
                apply_receipt(ledger, receipt)
        return {"ledger": ledger}


def build_graph(boundaries):
    return InvoiceAgent(boundaries)


def original_failure(result):
    ledger = result.get("ledger", [])
    return sum(row["cents"] for row in ledger if row["invoice"] == "invoice-8") == 5000
'''

FIXED_BODY = '''if not any(row["receipt_id"] == receipt["receipt_id"] for row in ledger):
        ledger.append(receipt)'''
BUGGY_BODY = 'ledger.append(receipt)  # BUG: the same provider receipt can arrive twice.'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--event-context", action="store_true", help="Disambiguate repeated receipt calls by input event ID")
    args = parser.parse_args()
    agent_source = AGENT
    if args.event_context:
        agent_source = agent_source.replace('"cents": 2500}))', '"cents": 2500}), context={"event_id": event["id"]})')
    import chrolo
    package = Path(chrolo.__file__).resolve()
    if not package.is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError("Use a non-editable installed wheel environment")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {"format": "chrolo.simulated-developer-pilot.v1", "complete": False, "passed": False,
              "scope": "Synthetic invoice retry; installed wheel; no external developer or real payments",
              "package_path": str(package), "event_context": args.event_context, "steps": []}
    report_path = output / "report.json"

    def checkpoint():
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    checkpoint()
    project = Path(tempfile.mkdtemp(prefix="chrolo-invoice-project-"))
    report["project"] = str(project)
    if project.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        raise RuntimeError("Simulation project must be outside the checkout")
    agent = project / "invoice_agent.py"
    agent.write_text(agent_source, encoding="utf-8")
    (project / "sitecustomize.py").write_text(
        'import socket\ndef blocked(*args, **kwargs):\n    raise AssertionError("Network disabled in pilot")\n'
        'socket.socket.connect = blocked\nsocket.create_connection = blocked\n', encoding="utf-8")
    (project / ".gitignore").write_text(".chrolo-local/\n", encoding="utf-8")
    source = {"initial_state": {}, "events": [
        {"id": "health", "kind": "health", "payload": {}},
        {"id": "first", "kind": "receipt", "payload": {"invoice": "invoice-8"}},
        {"id": "retry", "kind": "receipt", "payload": {"invoice": "invoice-8"}, "requires": ["first"]},
        {"id": "note", "kind": "annotation", "payload": {}},
    ]}
    (project / "input.json").write_text(json.dumps(source, indent=2), encoding="utf-8")
    environment = dict(os.environ, PYTHONPATH=str(project), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")

    def run(label, arguments, expected=0, message=None):
        completed = subprocess.run([sys.executable, *arguments], cwd=project, env=environment,
                                   capture_output=True, text=True, timeout=120)
        (output / f"{label}.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
        report["steps"].append({"label": label, "command": arguments, "exit_code": completed.returncode})
        checkpoint()
        if completed.returncode != expected or (message and message not in completed.stdout + completed.stderr):
            raise RuntimeError(f"Unexpected {label} outcome; see retained log")

    capture = ".chrolo-local/capture.json"
    bundle = ".chrolo-local/repro"
    test = ["-m", "pytest", "-q", f"{bundle}/test_regression.py"]
    prepare = ["-m", "chrolo", "prepare", capture, "--oracle", "invoice_agent:original_failure", "--output-dir", bundle]
    run("capture", ["-m", "chrolo", "capture", "--graph", "invoice_agent:build_graph", "--input", "input.json",
                    "--record-interactions", "--output", capture])
    callback_log = project / "recording_callbacks.log"
    recorded_callbacks = callback_log.read_bytes()
    assert len(recorded_callbacks.splitlines()) == 3
    capture_bytes = (project / capture).read_bytes()
    run("prepare", prepare)
    fixture = project / bundle / "minimal.json"
    fixture_bytes = fixture.read_bytes()
    summary = json.loads((project / bundle / "summary.json").read_text())
    assert summary["complete"] and summary["strict_replay_verified"]
    assert (summary["original_events"], summary["remaining_events"]) == (4, 2 if args.event_context else 3)
    assert (summary["original_interactions"], summary["remaining_interactions"]) == (3, 2 if args.event_context else 3)
    expected_events = ["first", "retry"] if args.event_context else ["health", "first", "retry"]
    assert [event["id"] for event in json.loads(fixture_bytes)["events"]] == expected_events
    run("bug_reproduced", test, expected=1, message="Original failure recurred")
    agent.write_text(agent_source.replace(BUGGY_BODY, FIXED_BODY), encoding="utf-8")
    run("application_fixed", test)
    # A changed boundary request must fail instead of silently using a new response.
    agent.write_text(agent_source.replace(BUGGY_BODY, FIXED_BODY).replace('event["payload"],', '{"invoice": "different"},'), encoding="utf-8")
    run("changed_request_rejected", test, expected=1, message="Replay mismatch")
    # A different application error is not evidence that the original bug was fixed.
    agent.write_text(agent_source.replace(BUGGY_BODY, 'raise RuntimeError("unrelated application error")'), encoding="utf-8")
    run("unrelated_error_rejected", test, expected=1, message="unrelated application error")
    agent.write_text(agent_source.replace(BUGGY_BODY, FIXED_BODY), encoding="utf-8")
    run("fixed_again", test)
    run("overwrite_rejected", prepare, expected=2, message="not overwritten")
    assert callback_log.read_bytes() == recorded_callbacks
    assert (project / capture).read_bytes() == capture_bytes
    assert fixture.read_bytes() == fixture_bytes
    for name, data in {"buggy_invoice_agent.py": agent_source.encode(), "fixed_invoice_agent.py": agent.read_bytes(),
                       "capture.json": capture_bytes, "minimal.json": fixture_bytes,
                       "test_regression.py": (project / bundle / "test_regression.py").read_bytes()}.items():
        (output / name).write_bytes(data)
    report.update(complete=True, passed=True, bundle=summary, recording_callbacks=3, replay_callbacks=0,
                  capture_unchanged=True, minimal_fixture_unchanged=True,
                  capture_sha256=hashlib.sha256(capture_bytes).hexdigest(),
                  minimal_sha256=hashlib.sha256(fixture_bytes).hexdigest())
    checkpoint()
    print("Invoice developer simulation passed; report:", report_path)


if __name__ == "__main__":
    main()
