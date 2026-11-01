# Chrolo — Rewind failures. Prevent regressions.

**Chrolo turns a *reproducible* failed agent workflow into a smaller input fixture and a generated pytest regression test.** This is an early open-source prototype, not a production incident replay system.

The first meaningful capability is dependency-preserving failure minimization: given a known failure predicate and a safe, deterministic workflow runner, Chrolo tries to delete input events while retaining the *same* failure. The result is **1-minimal under tested cascade deletions when the budget suffices**, not guaranteed globally smallest.

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

The adapter accepts a graph factory `module:function` returning a compiled LangGraph graph. The graph consumes **recorded input events** from a state key (default `events`). Chrolo executes the graph and saves its per-step state snapshots, then runs the same actual graph from fresh initial state for each candidate subset of events. M2-aware graph factories can also receive a boundary context with `--record-interactions` to capture explicitly wrapped model/tool calls and replay their responses offline. This is *real graph execution*, not a mocked judgment by an LLM.

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

Minimize the recorded input event sequence and generate a regression test:

```bash
chrolo minimize results/failure.json \
  --oracle examples.langgraph_issue_agent:wrong_issue \
  --output results/minimal.json \
  --test results/test_chrolo_regression.py

pytest -q results/test_chrolo_regression.py
```

Expected demo reduction: 5 events → 2 events (`search`, `cache`). The generated test *fails intentionally* on the vulnerable graph, and should pass after the graph's `choose` node is corrected.

If LangGraph is not installed, you can exercise the same adapter interface against the included **test-only shim**:

```bash
chrolo capture \
  --graph examples.fake_graph:build_graph \
  --input examples/langgraph_input.json \
  --output results/failure.json
chrolo minimize results/failure.json \
  --oracle examples.fake_graph:wrong_issue \
  --output results/minimal.json \
  --test results/test_chrolo_regression.py
```

## M2: Boundary cassette core (experimental)

Chrolo now includes a framework-neutral recorder and strict offline replay session for explicit model/tool wrapper calls. The versioned `chrolo.interactions.v1` format stores JSON-compatible requests and responses in order. Replay rejects an unexpected call, wrong boundary or target, changed request, or unconsumed interaction. It has no live-call fallback. The cassette composes with the existing minimizer through the LangGraph adapter: each candidate gets a fresh replay session and must consume the full cassette before its result reaches the oracle. For capture, pass `--record-interactions`; the graph factory must accept a `BoundaryContext` and route calls through `boundaries.call(boundary, target, request, live_callable)`. In replay, the live callable is never invoked. Existing no-argument factories retain the M1 input-only behavior, and unwrapped services are not intercepted.

A small Python harness can use it directly:

```python
from chrolo.cassette import CassetteRecorder, ReplaySession

recorder = CassetteRecorder()
answer = recorder.call("model", "my_model", {"prompt": "..."}, lambda: client.invoke(...))
trace = recorder.to_dict()  # persist as JSON after reviewing sensitive contents

replay = ReplaySession.from_dict(trace)
answer = replay.call("model", "my_model", {"prompt": "..."})
replay.finish()  # verifies every recorded interaction was consumed
```

Recording invokes the supplied callable and may therefore perform live calls or side effects. Replay accepts no callable and never invokes a provider or tool. Trace payloads may contain confidential data; sanitize them before saving or sharing. Only JSON-compatible values are supported in this first cassette version.

## Contract & scope

- **Original failure:** the developer supplies a narrow, executable `module:function` oracle returning `True` *only* for the original failure signature. Generic exceptions or any failing state are insufficient evidence. The runner must return a result for the oracle; exceptions from the baseline runner abort minimization, and exceptions from candidate runs are rejected as non-reproductions. Exceptions raised by the oracle are reported separately.
- **Dependencies:** events contain `id`, `kind`, `payload`, and optional ordered `requires` IDs. `requires` must be an array of string event IDs; dependencies must refer to earlier events. Deletions cascade to dependent events. Missing dependencies aren't invented.
- **Replay model:** each candidate rebuilds the graph and calls `graph.invoke()` on the original initial state with a subset of recorded **input** events. It does *not* replay arbitrary node updates as inputs.
- **Replay scope:** interaction capture records only model/tool calls explicitly routed through `BoundaryContext`. It does not capture databases, files, checkpoint state, unwrapped services, or nondeterministic execution. Without `--record-interactions`, M1 remains input-event rerun. For correct minimization, graph code must still be safe to re-execute.
- **Model-provider calls are not permitted** in the offline demo; a production adapter must record and mock model/tool boundaries before promoting this to general-purpose agent traces.
- **No secret redaction yet.** Captures contain full initial state and graph snapshots. Use **only synthetic or carefully sanitized data**, do not ingest private production traces, and do not publish generated fixtures containing sensitive information.
- **Trusted code only:** `module:function` imports execute arbitrary Python, so do not run fixtures or generated tests from untrusted sources.
- **Original code:** generated tests import the developer's *actual* graph implementation and assert that the known failure no longer occurs, rather than testing a text summary.

## Roadmap

- **M0:** Dependency-preserving minimizer + test generator, using trusted deterministic harness. Implemented and unit-tested.
- **M1 (in progress):** LangGraph-compatible graph invocation and capture. Shim and core tested; actual LangGraph integration must be exercised with the optional dependency.
- **M2 (in progress):** Versioned cassette recording and strict replay for explicitly wrapped model/tool calls, including LangGraph integration. General side-effect virtualization, redaction, and arbitrary real-world trace support remain future work.
- **M3:** Minimize stateful trajectories using verified dependency inference and reusable failure assertions.
- **M4:** Benchmark against naïve minimization on labeled real agent failures; publish reproducibility data and integration guides.

## Measuring value

Use reproduction reliability on repeated runs, preserved failure signature, shrink percentage, number of candidate executions, regression sensitivity before/after a fix, and actual developer time saved. A short trace is *not* proof of correct minimization if it reproduces a different failure.

**Naming note:** Chrolo is the project working name; verify GitHub, package registry, domain, and trademark availability before public release.
