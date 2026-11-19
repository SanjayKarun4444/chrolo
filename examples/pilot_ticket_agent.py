"""Copy into your project as ticket_agent.py for the local pilot walkthrough.

The model and tool below are deterministic fixtures; no hosted service is used.
"""


def choose_ticket(ticket):
    return bool(ticket)  # Pilot bug: ignores whether the ticket is still open.


class TicketAgent:
    def __init__(self, boundaries):
        self.boundaries = boundaries

    def invoke(self, state):
        def live(value):
            if self.boundaries.mode != "record":
                raise AssertionError("Replay must not execute model/tool callbacks")
            return value

        selected_closed = False
        for event in state["events"]:
            if event["kind"] == "diagnostic":
                self.boundaries.call("tool", "ticket.diagnostic", {}, lambda: live({"ok": True}))
            elif event["kind"] == "select":
                plan = self.boundaries.call("model", "ticket.planner", {"task": "select ticket"},
                                            lambda: live({"ticket_id": 42}))
                ticket = self.boundaries.call("tool", "ticket.fetch", plan,
                                              lambda: live({"ticket_id": 42, "status": "closed"}))
                selected_closed = choose_ticket(ticket) and ticket["status"] == "closed"
        return {"selected_closed_ticket": selected_closed}


def build_graph(boundaries):
    return TicketAgent(boundaries)


def original_failure(result):
    return isinstance(result, dict) and result.get("selected_closed_ticket") is True
