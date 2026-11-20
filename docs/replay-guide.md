# Replay and integration guide

Start with the [README quickstart](../README.md#quickstart) or the
[project walkthrough](pilot-workflow.md). This guide covers the lower-level
interfaces and advanced capture options.

## Choose an execution path

| Path | Input | Execution behavior |
| --- | --- | --- |
| Generic minimization | JSON events and trusted `run(events)` | Executes the supplied deterministic runner for each candidate |
| Input-only graph capture | Initial state, events, graph target | Rebuilds and invokes the graph; requires `--allow-live-side-effects` |
| Cassette-backed graph capture | Graph inputs and wrapped interactions | Rebuilds the graph; wrapped calls match saved requests and return recorded values |
| OpenHands task replay | Recorded shell/editor actions and task metadata | Executes actions in fresh network-isolated Docker containers |

A graph factory returns a compiled LangGraph graph or another compatible
object with `invoke(state)`. Input events are provided in `state["events"]`
unless `--event-key` selects another key. Every minimization candidate starts
from the original initial state and a fresh graph. Chrolo does not replay
arbitrary intermediate node updates as input events.

## Record explicit boundaries

Graph factories using `--record-interactions` accept a `BoundaryContext` and
route calls through `boundaries.call(boundary, target, request, live_callable)`.
Only these wrapped calls are captured. Existing no-argument factories remain
available for input-only graph capture.

The framework-neutral recorder can also be used directly:

```python
from chrolo.cassette import CassetteRecorder, ReplaySession

recorder = CassetteRecorder()
result = recorder.call("tool", "inventory.lookup", {"sku": "demo"},
                       lambda: {"available": True})
cassette = recorder.to_dict()

replay = ReplaySession.from_dict(cassette)
result = replay.call("tool", "inventory.lookup", {"sku": "demo"})
replay.finish()
```

Requests, responses, and optional call context must be JSON-compatible.
Normal replay checks the boundary type, target, request, context, and order.
Missing, unexpected, mismatched, or unconsumed interactions fail replay. There
is no live-call fallback. During minimization, candidate replay can consume an
exact in-order subsequence; unused calls are pruned, then the reduced cassette
is strictly verified. Ambiguous repeated requests can restrict reduction; add
stable event context when recording them.

`BoundaryTool(function, boundaries, "module.tool", context={"node": "search"})`
wraps a JSON-argument tool. Replay returns the recorded value without calling
the function.

## Framework and provider adapters

| Import | Usage | Optional extra |
| --- | --- | --- |
| `chrolo.langchain_adapter.BoundaryChatModel` | Wrap a chat model and call `.invoke(messages, **kwargs)` | `langchain` |
| `chrolo.openai_adapter.BoundaryOpenAIResponses` | Wrap an OpenAI client and call `.create(model=..., input=...)` | `openai` |
| `chrolo.openai_adapter.BoundaryOpenAIChatCompletions` | Wrap an OpenAI client and call `.create(model=..., messages=...)` | `openai` |
| `chrolo.anthropic_adapter.BoundaryAnthropicMessages` | Wrap an Anthropic client and call `.create(...)` | `anthropic` |

Construct these wrappers with the underlying client/model, a recording or
replaying `BoundaryContext`, and a stable target name. Optional context must
match in both modes. SDK wrappers preserve JSON response content including
tool calls and return Chrolo result objects with `response` and `output_text`;
they do not reconstruct SDK response objects. LangChain preserves message and
generation metadata. These adapters are synchronous and buffered; streaming
is unsupported. See the [provider examples](../examples/README.md) for complete
SDK/LangGraph workflows using local HTTP fixtures.

## Capture exceptions and provenance

Capture normally aborts if the graph raises. `--capture-exceptions` instead
records a versioned outcome containing the exception class, omitting the
message and stack trace. Replay passes a structured exception or success
outcome to the supplied oracle. Choose an oracle precise enough to reject
unrelated errors.

`--include-snapshots` retains full graph snapshots; they are omitted by default.
Snapshots may contain confidential state and are separate from replay inputs.

Provenance is opt-in: `--project-name`, `--project-commit`, and repeatable
`--fingerprint NAME=VALUE`. Values are retained verbatim; Chrolo does not
automatically collect environment variables. `inspect` shows fingerprint names
without exposing their values.

Graph captures use `chrolo.langgraph.v1`, exception-aware
`chrolo.langgraph.v2`, or provenance-aware `chrolo.langgraph.v3` according to
the selected options. New interaction cassettes use
`chrolo.interactions.v2`, with capture timestamp, execution ID, Chrolo version,
adapter versions, and optional per-call context. Legacy interaction v1
cassettes remain readable.

## Stateful dependencies

The experimental transition API uses `TrackedState` to identify top-level
read-after-write dependencies and verifies each edge by removing its producer.
Nested values are returned as copies: assign an edited value back to its
top-level key so the write is tracked. Transitions must be deterministic and
safe to execute repeatedly.

```bash
python -m chrolo infer-dependencies examples/stateful_scenario.json --step examples.stateful_agent:step --oracle examples.stateful_agent:wrong_issue --output results/stateful_dependencies.json
python -m chrolo minimize results/stateful_dependencies.json --trajectory-step examples.stateful_agent:step --oracle examples.stateful_agent:wrong_issue --output results/stateful_minimal.json --test results/test_stateful_regression.py
```

This API does not infer arbitrary dependencies inside graph nodes. Explicit
event `requires` dependencies must refer to earlier event IDs; deletion
cascades through dependent events. A baseline runner exception aborts reduction;
candidate runner errors and oracle errors are rejected and counted. Check
counts include evaluated baselines and candidates, including errors.

## Inspect and prepare review copies

```bash
python -m chrolo inspect results/quickstart/capture.json
python -m chrolo scan results/quickstart/capture.json
python -m chrolo redact results/quickstart/capture.json --path /initial_state/api_token --output results/review-copy.json
```

The redaction path above is illustrative; choose paths actually present in
your trace. JSON Pointer paths support `*` to match entries. Capture also
supports repeatable `--include-path` and `--exclude-path` projections. Redacted
and filtered outputs are marked non-replayable; retain the original privately.
Scanning is heuristic and cannot guarantee that a trace is safe to share.

## Trace Desk

`python -m chrolo serve` starts the local inspection UI at the printed address.
It inspects, scans, redacts, and filters uploaded JSON without executing the
graph or saving uploads to server storage. Clear data from the page to remove
its selected file and findings.

For remote access on a trusted private network, use TLS and a private/VPN IP:

```bash
python -m chrolo serve --host 0.0.0.0 --tls-certfile cert.pem --tls-keyfile key.pem --advertise-ip 100.72.5.4
```

The supplied address is an example. Remote binds use Basic Auth and print the
username and generated password. Without TLS, credentials and uploaded traces
are unencrypted at the application layer. Use a certificate trusted by the
device and matching its URL; keep the service on a trusted private network.
Private IPv4 and CGNAT VPN addresses are supported. Physical-phone visual QA
remains open.
