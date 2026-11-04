# Chrolo — Rewind failures. Prevent regressions.

**Chrolo turns a *reproducible* failed agent workflow into a smaller input fixture and a generated pytest regression test.** This is an early open-source prototype, not a production incident replay system.

The first meaningful capability is dependency-preserving failure minimization: given a known failure predicate and a safe, deterministic workflow runner, Chrolo tries to delete input events while retaining the *same* failure. Results include accepted deletion history, candidate rejection counts, and whether the fixture is 1-minimal under tested cascade deletions within budget. It is not guaranteed globally smallest.

## Installation

Python 3.10+:

```bash
python3 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
pytest -q
```

## First example (no LangGraph dependency)

```bash
chrolo minimize examples/scenario.json \
  --run examples.buggy_agent:run \
  --oracle examples.buggy_agent:is_failure \
  --output examples/minimal.json \
  --test examples/test_generated_regression.py

pytest -q examples/test_generated_regression.py
```

The **last command is expected to fail** because the sample implementation deliberately reproduces the bug. Once you fix the buggy agent, the generated regression test should pass.

## M1: LangGraph-compatible state replay (experimental)

The adapter accepts a graph factory `module:function` returning a compiled LangGraph graph. The graph consumes **recorded input events** from a state key (default `events`). Chrolo executes the graph and saves the input fixture; full per-step state snapshots are omitted by default to limit trace exposure. Add `--include-snapshots` when you need snapshots for debugging. Minimization runs the same graph from fresh initial state for each candidate subset of events. M2-aware graph factories can also receive a boundary context with `--record-interactions` to capture explicitly wrapped model/tool calls and replay their responses offline. This is *real graph execution*, not a mocked judgment by an LLM.

By default, a graph exception aborts capture as before. Add `--capture-exceptions` to write a `chrolo.langgraph.v2` capture with a versioned `outcome` containing only the exception class; exception messages and stack traces are omitted. Existing `chrolo.langgraph.v1` captures remain supported. Replay returns a structured `{ "status": "exception", "exception_type": "..." }` result for the user's failure oracle, or `{ "status": "success" }` if the exception no longer occurs. Input-only captures still require `--allow-live-side-effects` for replay and minimization.

Capture can also record explicitly selected provenance, which writes `chrolo.langgraph.v3` while preserving the same replay behavior. Use `--project-name`, `--project-commit`, and repeatable `--fingerprint NAME=VALUE`. Fingerprint values are stored verbatim, so pass only non-secret values you intend to retain. Chrolo does not collect environment values automatically. `inspect` reports project metadata and fingerprint names, but omits fingerprint values.

```bash
chrolo capture \
  --graph examples.failing_graph:build_graph \
  --input examples/failing_graph_input.json \
  --capture-exceptions \
  --output results/failure.json

chrolo replay results/failure.json \
  --allow-live-side-effects \
  --oracle examples.failing_graph:original_failure

chrolo minimize results/failure.json \
  --allow-live-side-effects \
  --oracle examples.failing_graph:original_failure \
  --output results/minimal.json \
  --test results/test_failure_regression.py
```

The generated regression test fails while the exception signature remains and passes after changing the fixture's graph target to `examples.failing_graph:build_fixed_graph`.

Install the optional dependency:

```bash
pip install -e '.[dev,langgraph]'
```

Capture a deterministic failed graph run:

```bash
chrolo capture \
  --graph examples.langgraph_issue_agent:build_graph \
  --input examples/langgraph_input.json \
  --output results/failure.json
```

Add `--include-snapshots` to retain full per-step graph states when debugging; snapshots may contain sensitive data.

Minimize the recorded input event sequence and generate a regression test:

```bash
chrolo minimize results/failure.json \
  --oracle examples.langgraph_issue_agent:wrong_issue \
  --allow-live-side-effects \
  --output results/minimal.json \
  --test results/test_chrolo_regression.py

pytest -q results/test_chrolo_regression.py
```

Use `chrolo replay results/failure.json --oracle examples.langgraph_issue_agent:wrong_issue` to rerun it. A capture with a cassette replays wrapped model/tool calls offline. An input-only capture re-executes the graph and requires `--allow-live-side-effects`; its output labels that mode because unwrapped calls can still reach live services.

Expected demo reduction: 5 events → 2 events (`search`, `cache`). The generated test *fails intentionally* on the vulnerable graph, and should pass after the graph's `choose` node is corrected.

If LangGraph is not installed, you can exercise the same adapter interface against the included **test-only shim**:

```bash
chrolo capture \
  --graph examples.fake_graph:build_graph \
  --input examples/langgraph_input.json \
  --output results/failure.json
chrolo minimize results/failure.json \
  --oracle examples.fake_graph:wrong_issue \
  --allow-live-side-effects \
  --output results/minimal.json \
  --test results/test_chrolo_regression.py
```

## M2: Boundary cassette core (experimental)

Chrolo now includes a framework-neutral recorder and strict offline replay session for explicit model/tool wrapper calls. The versioned `chrolo.interactions.v2` format stores JSON-compatible requests and responses in order, with a capture timestamp, execution ID, Chrolo version, and adapter versions. It can also store optional JSON call context such as a stable node name; replay requires the same context. Chrolo can still read legacy `chrolo.interactions.v1` cassettes. Normal replay rejects an unexpected call, wrong boundary or target, changed request/context, or unconsumed interaction. It has no live-call fallback. During `minimize`, each candidate gets a fresh replay session and may consume only an exact, in-order subsequence of the original cassette; records for calls the reduced graph no longer makes are pruned from the output. The generated regression test then replays the reduced cassette strictly and requires full consumption. For capture, pass `--record-interactions`; the graph factory must accept a `BoundaryContext` and route calls through `boundaries.call(boundary, target, request, live_callable)`. In replay, the live callable is never invoked. Existing no-argument factories retain the M1 input-only behavior, and unwrapped services are not intercepted.

A small Python harness can use it directly:

```bash
chrolo capture \
  --graph examples.interaction_minimization:build_graph \
  --input examples/interaction_minimization_input.json \
  --record-interactions \
  --output results/interaction-failure.json

chrolo replay results/interaction-failure.json \
  --oracle examples.interaction_minimization:original_failure

chrolo minimize results/interaction-failure.json \
  --oracle examples.interaction_minimization:original_failure \
  --output results/interaction-minimal.json \
  --test results/test_interaction_regression.py

chrolo replay results/interaction-minimal.json \
  --oracle examples.interaction_minimization:original_failure
```

The example's unnecessary diagnostic event and its tool call are pruned together. Both the captured and minimized fixtures replay offline; the final replay still requires every retained cassette call to match and be consumed.

```python
from chrolo.cassette import CassetteRecorder, ReplaySession

recorder = CassetteRecorder()
answer = recorder.call("model", "my_model", {"prompt": "..."}, lambda: client.invoke(...))
trace = recorder.to_dict()  # persist as JSON after reviewing sensitive contents

replay = ReplaySession.from_dict(trace)
answer = replay.call("model", "my_model", {"prompt": "..."})
replay.finish()  # verifies every recorded interaction was consumed
```

`BoundaryTool(function, boundaries, "module.tool", context={"node": "search"})` provides the same explicit boundary for a JSON-argument tool; replay returns the recorded value without calling `function`. An optional buffered LangChain chat adapter is available with `pip install -e '.[langchain]'`. Wrap the model with `BoundaryChatModel(model, boundaries, "provider:model-version", context={"node": "planner"})` and call `.invoke(messages, **kwargs)`. Record with `BoundaryContext.recording(recorder)` and replay with `BoundaryContext.replaying(session)`. The adapter preserves LangChain messages and generation metadata in the cassette; streaming is not supported. Only calls made through these wrappers are captured.

For the official OpenAI Python SDK, the optional `BoundaryOpenAIResponses` and `BoundaryOpenAIChatCompletions` wrappers support buffered synchronous `client.responses.create(...)` and `client.chat.completions.create(...)` calls. Install with `pip install -e '.[openai]'`. Both store a JSON representation of the full response, including tool-call items, and return a stable Chrolo result type in record and replay modes. The result's `output_text` is a convenience for plain text; inspect `result.response` for normalized API fields. These wrappers return Chrolo result objects instead of SDK-typed responses. Streaming is rejected before the call; async clients and awaitable results fail clearly without being awaited. See the [OpenAI Python SDK reference](https://developers.openai.com/api/reference/python) for the underlying request shapes.

```python
from openai import OpenAI

from chrolo.cassette import BoundaryContext, CassetteRecorder
from chrolo.openai_adapter import BoundaryOpenAIResponses

client = OpenAI()
recorder = CassetteRecorder()
model = BoundaryOpenAIResponses(
    client,
    BoundaryContext.recording(recorder),
    "openai:responses:my-model",
)
result = model.create(model="my-model", input="...", store=False)
text = result.output_text
trace = recorder.to_dict()
```

Use `BoundaryOpenAIChatCompletions` similarly around an OpenAI client; its `.create(model=..., messages=...)` returns `OpenAIChatCompletionsResult`.

For Anthropic's Python SDK, install `pip install -e '.[anthropic]'` and wrap buffered synchronous `client.messages.create(...)` calls with `BoundaryAnthropicMessages` from `chrolo.anthropic_adapter`. It returns `AnthropicMessageResult`, with a JSON-safe `response` (including tool-use blocks) and concatenated `output_text`. Streaming is rejected before the call; async clients and awaitable results fail clearly without being awaited. Replay returns Chrolo's normalized result rather than an Anthropic SDK object. See the [official Anthropic Python SDK example](https://github.com/anthropics/anthropic-sdk-python/blob/main/examples/messages.py).

OpenAI prompts and responses may contain sensitive data; apply Chrolo's trace review and redaction controls before sharing cassettes. Replay uses recorded output and has no provider fallback.

Recording invokes the supplied callable and may therefore perform live calls or side effects. Replay accepts no callable and never invokes a provider or tool. Trace payloads may contain confidential data. Use `chrolo inspect trace.json` for a payload-free summary, or redact selected JSON Pointer paths before sharing:

```bash
chrolo redact trace.json --path /initial_state/api_token --path "/interactions/interactions/*/request/authorization" --output trace.sanitized.json
```

Redaction replaces values with `[REDACTED]`, records a report, and marks the trace non-replayable because redacted inputs or responses may change matching or outcomes. The optional `chrolo scan` heuristic can flag likely secrets by path, but it may miss secrets in prose or unsupported formats; review the trace and choose sensitive paths before sharing. Only JSON-compatible values are supported in this first cassette version.

Capture can also project selected fields with repeatable `--include-path` and `--exclude-path` JSON Pointer options. Filtered output is marked non-replayable because removed values can affect execution or interaction matching. Use these options to prepare a review copy; retain the original capture for replay.

## Stateful trajectory minimization (experimental)

State transitions must be deterministic and side-effect safe, and must read and write through `TrackedState`. Nested values are returned as copies; after editing one, assign it back to its top-level state key so the change is tracked. Chrolo detects top-level read-after-write edges and retains an edge only if removing its producer changes the consumer's written state or makes that consumer fail. The supplied oracle remains authoritative during minimization.

```bash
chrolo infer-dependencies examples/stateful_scenario.json \
  --step examples.stateful_agent:step \
  --oracle examples.stateful_agent:wrong_issue \
  --output results/stateful_dependencies.json

chrolo minimize results/stateful_dependencies.json \
  --trajectory-step examples.stateful_agent:step \
  --oracle examples.stateful_agent:wrong_issue \
  --output results/stateful_minimal.json \
  --test results/test_stateful_regression.py
```

This is an instrumented transition API, not automatic inference inside arbitrary LangGraph nodes. The transition and oracle targets import trusted Python code and can have side effects.

## Contract & scope

Run `chrolo benchmark` for a synthetic, offline reliability report covering event reduction, cassette replay, mismatch/error rejection, replay side effects, and a deliberately nondeterministic unwrapped call. It makes no provider calls and is not a substitute for benchmarking labeled real failures.

For reviewed, labeled cases, `chrolo benchmark --dataset benchmarks.json` compares Chrolo with a naïve one-pass deletion baseline. Dataset entries reference trusted `runner` or `step` and `oracle` Python functions, with an optional fixed runner/step to measure regression sensitivity; those imports are not sandboxed. See [examples/benchmark_dataset.json](examples/benchmark_dataset.json) for the schema. Do not use untrusted datasets.

`examples/task_oracle_dataset.json` adds three synthetic executable task failures: routing a ticket to the wrong team, charging an already-paid invoice again, and deleting a protected file. Its runner executes retained tool calls against fresh in-memory task state, resolves explicit outputs from earlier tool calls, and checks an exact task-state failure signature. Run it with `python -m chrolo benchmark --dataset examples/task_oracle_dataset.json --repeat 20`. The fixed runners model a correction and must clear the original signature. These are deterministic reference tasks, not recordings from production agents or external services.

For one OpenHands trajectory joined to SWE-rebench task tests, build the input and run the dedicated fresh-container benchmark:

```powershell
python benchmarks/build_openhands_input.py `
  --trace-json results/openhands-first-row.json `
  --tasks-parquet results/swerebench-filtered.parquet `
  --output results/openhands-benchmark-input.json
python -m chrolo benchmark-openhands results/openhands-benchmark-input.json `
  --repeat 5 --output results/openhands-benchmark.json
python -m chrolo scan results/openhands-benchmark.json
```

The input builder requires `pyarrow`; Chrolo's runtime does not. Each candidate uses a new Docker task container with networking disabled. The JSON input and report contain recorded shell/editor arguments and can contain secrets; keep them local and scan before sharing. The current runner supports the sample's editor `view`, `create`, and `str_replace` commands. The command is unit-tested with a mocked runner, but its task-image behavior has not yet been validated; do not count it as real-agent benchmark evidence until that run and a verified fix intervention succeed.

For a mobile-friendly local trace desk, run `chrolo serve` and open the printed address. It can inspect, scan for likely secrets, redact, and filter uploaded JSON traces without executing graph code or saving uploads on the server. Selecting a different file clears the previous summary and scan findings; **Clear data from this page** removes the selected file, loaded trace, findings, and path inputs. It also prevents an outstanding response from repopulating cleared results. To access it from a phone, bind to a trusted private network and use HTTPS with a certificate trusted by your devices:

```bash
chrolo serve --host 0.0.0.0 --tls-certfile cert.pem --tls-keyfile key.pem --advertise-ip 100.72.5.4
```

For `--host 0.0.0.0`, startup lists discovered RFC1918 private IPv4 addresses and VPN addresses in `100.64.0.0/10`, then prints the Basic Auth username (`chrolo`) and generated password. Replace the sample `--advertise-ip` with the workstation's private VPN IPv4 address if automatic discovery misses it; only RFC1918 or CGNAT VPN addresses are accepted. From outside the local Wi-Fi, connect both devices to the same private VPN and open the workstation's VPN address; do not expose the port to the public internet. The certificate must be trusted by the phone and match the address you open. For a private LAN or VPN where TLS is not configured, the server prints a warning; Basic Auth and trace uploads are then unencrypted at the app layer. Filtered and redacted downloads cannot be replayed.

Run `chrolo scan trace.json` to list likely secret paths and detector reasons without printing matched values. The heuristic checks sensitive field names and common credential formats; it may miss secrets embedded in prose or unsupported formats. Review candidates, then use `chrolo redact` with the returned paths. A clean scan is not proof that a trace is safe to share.

- **Original failure:** the developer supplies a narrow, executable `module:function` oracle returning `True` *only* for the original failure signature. Generic exceptions or any failing state are insufficient evidence. The runner must return a result for the oracle; exceptions from the baseline runner abort minimization, and exceptions from candidate runs are rejected as non-reproductions. Exceptions raised by the oracle are reported separately.
- **Dependencies:** events contain `id`, `kind`, `payload`, and optional ordered `requires` IDs. `requires` must be an array of string event IDs; dependencies must refer to earlier events. Deletions cascade to dependent events. Missing dependencies aren't invented.
- **Check counts:** `checks` includes the baseline run and every candidate run actually evaluated; candidates that raise still count. Deletions skipped because cascade removal would change nothing do not count.
- **Replay model:** each candidate rebuilds the graph and calls `graph.invoke()` on the original initial state with a subset of recorded **input** events. It does *not* replay arbitrary node updates as inputs.
- **Replay scope:** interaction capture records only model/tool calls explicitly routed through `BoundaryContext`. It does not capture databases, files, checkpoint state, unwrapped services, or nondeterministic execution. Without `--record-interactions`, M1 remains input-event rerun. For correct minimization, graph code must still be safe to re-execute.
- **No hosted provider calls occur** in the checked-in examples or synthetic benchmark. Live model/tool access occurs only if a developer explicitly starts a recording run through an instrumented boundary. Replay uses recorded values; calls that bypass the wrappers remain outside Chrolo's control.
- **Trace privacy:** `chrolo redact` replaces user-selected JSON Pointer paths and marks the result non-replayable; `chrolo inspect` prints counts and formats without payloads. `chrolo scan` can flag likely secrets without printing their values, but scanning is heuristic and cannot guarantee detection. Captures may still contain sensitive data, so review traces before sharing.
- **Trusted code only:** `module:function` imports execute arbitrary Python, so do not run fixtures or generated tests from untrusted sources.
- **Original code:** generated tests import the developer's *actual* graph implementation and assert that the known failure no longer occurs, rather than testing a text summary.

## Roadmap

- **M0:** Dependency-preserving minimizer + test generator, using trusted deterministic harness. Implemented and unit-tested.
- **M1:** LangGraph-compatible graph invocation and capture, with full snapshots opt-in.
- **M2 (experimental):** Versioned cassette recording and strict replay for explicitly wrapped model/tool calls, including LangGraph integration and buffered synchronous adapters for OpenAI Responses, OpenAI Chat Completions, and Anthropic Messages. Streaming, async, SDK-typed response reconstruction, unwrapped side-effect capture, and arbitrary trace support remain outside coverage.
- **M3 (experimental):** Infer stateful top-level read-after-write dependencies with `TrackedState`, verify each edge with a direct intervention, then minimize using those dependencies and the supplied failure oracle. Arbitrary graph dependency inference remains unsupported.
- **M4 (in progress):** `chrolo benchmark` includes synthetic contract cases, a reproducible bridge to AgentTelemetry's mock-backed fault-injection traces, AgentFailureBench paired synthetic trace differentials, and three executable synthetic task oracles. AgentTelemetry checks all 170 generated rows (78 true positives, 62 false negatives, 0 false positives, 30 true negatives); all 1,560 faulted replays preserve the detector signature and 78/78 paired runs with fault injection disabled clear it. AgentFailureBench includes 562 exact failed-versus-corrected tool-call/output differences; all reproduce and preserve the witness, and all 562 corrected-trace interventions clear it. The synthetic task-oracle cases run tool actions against fresh state and all three paired fixes clear their task-state signatures. These results cover detector signals, recorded-trace differences, and synthetic task behavior; they do not yet establish reliability on executable real-agent traces. The MAD/MAST corpus lacks deterministic runners and failure oracles. See [benchmark instructions](benchmarks/README.md).

The **Trace Desk** offers responsive, in-memory inspection, secret-path scanning, redaction, and filtering. It includes safe-area padding, 44px mobile touch targets, keyboard focus indicators, and a clear-page control. HTTP/TLS integration tests pass; physical-phone visual QA remains open.

## Measuring value

Use reproduction reliability on repeated runs, preserved failure signature, shrink percentage, number of candidate executions, regression sensitivity before/after a fix, and actual developer time saved. A short trace is *not* proof of correct minimization if it reproduces a different failure.

**Naming note:** Chrolo is the project working name; verify GitHub, package registry, domain, and trademark availability before public release.
