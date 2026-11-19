# Pilot: a captured failure becomes a regression test

This local pilot exercises one supported path: a trusted, importable graph
factory, explicitly wrapped synchronous model/tool calls, JSON input events,
and a precise Python failure oracle. Chrolo remains experimental.

## Try it in a project directory

Activate your project's Python 3.10+ environment and install this local checkout:

```powershell
python -m pip install -e 'C:/path/to/chrolo[dev]'
```

Copy [the pilot agent](../examples/pilot_ticket_agent.py) into your project as
`ticket_agent.py`, and [the input](../examples/pilot_ticket_input.json) as
`input.json`. These fixtures call no hosted provider and require no API key or
LangGraph installation. The class implements Chrolo's graph-compatible `invoke`
protocol. Actual compiled LangGraph/SDK examples are in [the examples guide](../examples/README.md).

Before capturing, add `.chrolo-local/` to your project's `.gitignore`. Captures
and minimized cassettes can contain raw requests, responses, and secrets.
Run the following commands from the project root:

```powershell
python -m chrolo capture --graph ticket_agent:build_graph --input input.json --record-interactions --output .chrolo-local/capture.json
python -m chrolo prepare .chrolo-local/capture.json --oracle ticket_agent:original_failure --output-dir .chrolo-local/repro
python -m pytest -q .chrolo-local/repro/test_regression.py
```

Capture explicitly executes the wrapped implementations once. `prepare` uses
the existing recording: it strictly replays the original failure, minimizes
events and consumed calls, strictly verifies the reduced failure, and generates
a local bundle. Only wrapped boundaries use saved responses; trusted graph code
can still run unwrapped effects. Use factories you own and trust.

The generated test should **fail with `Original failure recurred`**. A traceback
for a missing module or replay mismatch is a different failure and does not
confirm that this regression was reproduced.

## Fix application code, then rerun the same test

The bug in `ticket_agent.py` selects a ticket merely because a result exists.
Change its `choose_ticket` function from:

```python
return bool(ticket)
```

to:

```python
return ticket["status"] == "open"
```

Rerun the exact pytest command above. It should now pass. Keep the graph factory
and oracle importable at the same names. Do not edit either capture JSON or
record another response for this fix; its model/tool calls remain unchanged.

This is a synthetic walkthrough, not additional real-agent benchmark evidence.
The automated pilot test copies this agent into a separate developer-project
directory, changes only application code, and verifies both pytest outcomes
while checking that the capture and minimized fixture bytes remain unchanged.

## What the bundle contains

| File | Purpose |
| --- | --- |
| `minimal.json` | Reduced input events, retained cassette, and deletion/check history |
| `test_regression.py` | Test resolving the current project code and the same failure oracle |
| `summary.json` | Completion, strict verification, counts, and tested minimality status |
| `README.md` | Rerun instructions and replay limits |
| `.gitignore` | Keeps the generated raw-data bundle local by default |

Only `complete: true` indicates a finished bundle. An interrupted or failed
minimization may leave an incomplete directory; retain it for local diagnosis
and choose a new output directory for a retry. Existing output paths are never
overwritten. A completed bundle preserves the supplied failure signature; it
does not guarantee a globally smallest case. Check its minimality flag when a
small check budget was used.

## Connect your own workflow

1. Expose `build_graph(boundaries)` returning your graph with `invoke(state)`.
   It receives the captured events in `state["events"]` by default.
2. Route each model/tool call through `boundaries.call(...)`, `BoundaryTool`, or
   a supported provider wrapper. Unwrapped effects are outside cassette replay.
3. Supply `initial_state` and ordered `events` in your input JSON. Use explicit
   dependencies where inputs require earlier events.
4. Write an oracle such as `original_failure(result)` that identifies the same
   specific wrong outcome. Avoid predicates accepting every exception.
5. Capture once, then run `prepare` with your factory/oracle names. Run the
   generated test before and after fixing your application code.

The pilot requires a cassette-backed capture. Input-only, filtered, redacted,
malformed, and non-reproducing captures are rejected before bundle creation.
A fix that changes the recorded boundary requests or call order may invalidate
the cassette. A mismatch is not proof of a fix: review the test design and
capture a new run explicitly when needed, checking the oracle again. Chrolo
does not silently contact a provider, rerecord, or bless a changed interaction.

Run generated tests from your project root in the same environment. Install
pytest and keep your project importable. Use `python -m chrolo scan` on raw
captures/fixtures before sharing or explicitly promoting a test and fixture to
version control. The scanner is heuristic; redaction/filtering can remove data
needed for replay. Publishing, hosted CI execution, and arbitrary side-effect
capture are outside this pilot.
