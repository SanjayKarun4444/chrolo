"""A deliberately buggy agent-like decision function for the M0 demo.

This is a deterministic harness, not a live LLM or third-party framework.
"""
from chrolo.core import Event


def run(events: list[Event]) -> dict:
    """Incorrectly trusts a stale cached selection over fresh search results."""
    selected = None
    for e in events:
        if e.kind == "issue_search":
            selected = e.payload["correct_issue_id"]
        elif e.kind == "cached_selection":
            selected = e.payload["issue_id"]  # BUG: stale selection overrides search
    return {"selected_issue_id": selected}


def is_failure(result: dict) -> bool:
    """A precise signature: wrong issue 99, not just any error or empty result."""
    return result["selected_issue_id"] == 99
