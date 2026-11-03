# Chrolo roadmap: reliable agent failure replay

Current implementation and resume checkpoint: [progress.md](progress.md).

## Product goal

Chrolo turns a failed agent execution into a small, reviewable, deterministic regression case. A developer should be able to identify the failure, determine which inputs and external interactions were necessary, replay it without contacting live providers or repeating tool side effects, and keep a test that detects the bug after the agent code changes.

The product claim should remain precise: Chrolo minimizes a captured execution when it can replay relevant external responses deterministically and verify that the same failure remains. It does not promise perfect capture of arbitrary agent programs.

## Engineering invariants

- Accept a candidate only when it reproduces the original failure signature.
- A different exception, replay mismatch, missing interaction, timeout, or oracle error is not reproduction.
- Replay never calls live model providers or tools unless the user explicitly starts a new recording run.
- Trace files contain validated, versioned data and never executable trace content.
- Minimized traces retain enough provenance to explain what was removed and why.
- Side-effect isolation and sensitive-data handling are product behavior, not just documentation.
- Keep M0 and M1 workflows working unless a documented, versioned change is necessary.

## Current foundation

M0 provides dependency-preserving input event minimization and pytest generation using a caller-supplied deterministic runner and failure oracle. M1 adds experimental LangGraph-compatible input-event capture and replay: it invokes a fresh graph with a reduced input event list; full state snapshots are opt-in. M2 adds a framework-neutral cassette for explicitly wrapped model/tool calls and LangGraph integration. Provider SDKs and arbitrary side effects remain outside capture.

The M1 contract stabilization work clarifies malformed event rejection and distinguishes baseline runner errors from baseline oracle errors. Candidate exceptions are rejected as non-reproductions. The M1 suite has been exercised with the real LangGraph integration when that optional dependency is installed.

## Phase 0: Stabilize M1 contracts

### Work

1. Document event structure, ordered `requires` dependencies, cascade deletion, runner/oracle responsibilities, and what the `checks` count includes.
2. Validate event arrays, event objects, required fields, payload objects, and dependency arrays of string IDs.
3. Distinguish baseline runner exceptions from oracle exceptions. Reject all candidate exceptions as non-reproductions.
4. Keep the existing result API compatible where possible; explain budget limits and the scope of the 1-minimality claim.
5. Keep CLI errors concise and actionable.
6. Preserve current M0 and M1 fixture behavior and document the boundary between snapshots and replayable inputs.
7. Report accepted deletion history, candidate rejection categories, and whether 1-minimality was verified within budget.

### Acceptance criteria

- Core and adapter tests pass in a clean supported environment.
- A candidate that raises a different exception is never counted as reproducing the original failure.
- Malformed dependencies are rejected with useful validation errors.
- The optional real LangGraph integration test passes when LangGraph is installed and skips cleanly when absent.
- Existing M0 and M1 examples continue to work.

## Phase 1: Specify the M2 interaction trace

### Current status

New interaction cassettes use `chrolo.interactions.v2` with a schema version, Chrolo version, UTC capture timestamp, execution UUID, adapter-version map, and optional per-call JSON context that is checked during replay. Replay remains backward compatible with v1 cassettes. The enclosing graph capture continues to hold initial state and input events. With opt-in `--capture-exceptions`, Chrolo writes a `chrolo.langgraph.v2` capture containing a versioned `chrolo.outcome.v1` status and exception class (never message or stack); replay returns that outcome as structured data for the caller-supplied oracle, and the default capture behavior still writes `chrolo.langgraph.v1` and raises on exceptions. Environment fingerprints and project metadata are not yet recorded; M2 therefore remains experimental.

### Objective

Agree on the trace data model and replay semantics before implementing provider integrations.

### Trace contents

Introduce a new versioned format rather than silently extending `chrolo.langgraph.v1`. The initial JSON format should contain:

- Header: schema version, Chrolo version, adapter names and versions, capture timestamp, execution ID, and optional project metadata.
- Initial input: initial state and input events, subject to explicit include/exclude policy.
- Ordered interactions: model/tool boundary, stable interaction ID, sequence number, identity, normalized request and recorded response or error, and optional node/parent/event context.
- Outcome: final result or structured failure signature.
- Provenance: explicitly selected model settings, relevant package versions, and user-selected environment fingerprints.
- Redaction report: removed/transformed paths and whether replay-critical data was affected.

Traces remain data-only. Do not serialize Python objects, callables, or code. Defer binary and multimodal payload storage until a clear storage model exists.

### Strict replay rules

- Match boundary type and model/tool identity.
- Match normalized requests using explicitly documented, conservative normalization.
- Consume records in order in the first version.
- Fail on missing, unexpected, reordered, or mismatched calls.
- Never fall back to live providers or tools.
- Report unconsumed records at completion.

Do not normalize away prompts, tool arguments, model settings, or other semantically relevant values. Only remove fields an adapter identifies as volatile, such as known request IDs or timestamps.

### Failure signature

Keep the user's executable oracle authoritative. Where possible, also record stable structured details such as exception class, code, normalized message fields, and a user-defined signature label. Do not rely on stack paths or line numbers as the sole identity of a failure.

### Acceptance criteria

- Schema, validation rules, ordering, matching, and compatibility policy are documented.
- Malformed traces and unsupported versions are rejected clearly.
- M0 and M1 formats remain readable, with their limitations stated.

## Phase 2: Build a framework-neutral cassette engine

### Components

1. **Recorder:** captures normalized request, response/error, sequence, identity, and available context at an explicit boundary.
2. **Replay cassette:** validates the trace and returns stored responses or errors for matching calls.
3. **Boundary protocol:** small interface for adapters to record, replay, and normalize calls; provider/framework code stays outside the minimizer.
4. **Replay session:** owns a per-execution cursor, matching state, mismatches, and unused-record report.

Every minimization candidate receives a fresh replay session. The trace is immutable input; consumed cursor state must never leak between candidates.

### Acceptance criteria

- Unit tests cover call order, request identity, mismatches, missing/extra records, recorded errors, and unused records.
- Repeated sessions over one trace return identical outcomes.
- Replay code has no path that invokes external services.

## Phase 3: Integrate model, tool, and LangGraph boundaries

### Integration sequence

1. Build a deterministic fake model and fake tool with call counters and a visible side-effect counter.
2. Wrap one supported model client at its invocation boundary; record requests, responses, and provider errors; replay stored data offline.
3. Wrap registered tools; record arguments and results; do not invoke the underlying tool during replay.
4. Add LangGraph integration at supported model/tool invocation hooks and associate calls with node/run context where available.
5. Test streaming and multimodal behavior only when explicitly supported; document unsupported modes.

Do not claim capture of arbitrary HTTP calls, direct unwrapped SDK use, subprocesses, databases, or custom functions in the first M2 release. These may remain nondeterministic unless explicitly instrumented.

### Acceptance criteria

- A LangGraph test graph records one model call and one tool call.
- Replay returns saved outputs with no live calls; fake call counters and side-effect counters remain unchanged.
- The graph result and original failure oracle match between recording and replay.
- Optional-dependency tests skip cleanly when integrations are unavailable.

## Phase 4: Minimize against a recorded cassette

### Current status

Cassette-aware `minimize` now replays each candidate against an exact ordered subsequence of recorded boundary calls. Calls the reduced graph no longer makes are pruned from the output cassette; calls still made must match a recorded boundary, target, request, and context exactly. The final fixture is checked once more with ordinary full-consumption replay, and that verification consumes one check from the shared `--max-checks` budget. This prunes unused recorded calls associated with reduced inputs; it does not edit graph code to remove calls the graph still makes.

### Candidate lifecycle

For each candidate, create a fresh graph/agent and replay session, execute against the reduced inputs, collect result/error/mismatches/consumption, and evaluate the original oracle only if execution is valid under the declared replay policy. In normal replay, require the complete cassette. During minimization, allow only exact ordered-subsequence matching: a candidate may skip recorded interactions that its reduced execution no longer calls, but it cannot invent a response, change a request, or reorder calls. Retain only records actually consumed by the accepted candidate, reindex their sequence numbers, and verify the minimized fixture with ordinary strict replay.

Normal replay uses strict ordered matching and reports missing, unexpected, reordered, mismatched, and unused interactions. The minimizer's explicit deletion-only subsequence mode skips a record only when the current exact boundary, target, request, and context uniquely match a later record; duplicate future matches are rejected as ambiguous. This policy is covered separately from normal strict replay.

### Result reporting

Report original/reduced event and interaction counts, oracle/replay checks, accepted deletion history, candidate rejection counts by reason, pruned unused interaction records, and whether the result is 1-minimal under the cascade-deletion strategy and available budget.

### Acceptance criteria

- A known bug reduces with fixed model/tool responses.
- A different exception or changed request/order is rejected with a clear reason.
- Generic M0 minimization continues to work.

## Stateful trajectory milestone (M3)

### Current status

An experimental `TrackedState` transition API records top-level read-after-write relations. `infer-dependencies` verifies each edge by removing its producer and checking the consumer's output state or failure. `minimize --trajectory-step` then applies those inferred `requires` edges with the existing caller-supplied oracle. It does not infer dependencies inside arbitrary graph frameworks, nested in-place mutations, or unwrapped side effects.

### Acceptance criteria

- A known state-dependent failure infers a dependency and remains reproducible after minimization.
- A read that does not affect consumer state does not create a dependency.
- Baseline oracle/runner failures are distinct from candidate failures.
- Generated regression tests replay the stateful transition from the fixture.

## Phase 5: Trace safety and privacy

### Current status

The CLI provides payload-free `inspect` summaries with file size, explicit JSON Pointer redaction, capture-time include/exclude projections with `*` wildcards, and `scan` findings that expose paths and reasons without matched values. The heuristic checks sensitive field names and selected high-signal credential formats; free-text and unsupported formats may be missed. The trace desk offers the same scan before redaction. Sanitized or filtered traces are marked non-replayable and refused by replay. Capture omits full state snapshots by default; `--include-snapshots` opts in.

### Controls

- Capture only data required for replay by default; make full graph snapshots opt-in.
- Support path-based field inclusion/exclusion and redaction for known sensitive keys.
- Report redacted paths and fail clearly when a redaction removes replay-critical data.
- Summarize trace counts and sizes without printing prompts, credentials, or full tool results by default.
- Explain that redaction cannot guarantee detection of secrets embedded in free text.
- Recommend local-only storage and source-control exclusions for traces.
- Continue treating `module:function` targets as trusted executable code.

Consider encryption only after trace format and key-management needs are understood.

### Acceptance criteria

- Tests verify redaction, exclusion, safe summaries, and replay refusal for incomplete critical data.
- Trace validation remains separate from importing trusted user code.

## Phase 6: CLI, compatibility, and developer experience

Keep `minimize` backward compatible. Candidate commands/workflows are `capture`, offline `replay`, cassette-aware `minimize`, and metadata-only `inspect`; finalize names after the schema and adapter lifecycle are settled.

### Current status

`chrolo replay` and `chrolo minimize` label cassette-backed replay versus input-only graph re-execution, offer an optional replay failure oracle, hide recorded provider error text, and require `--allow-live-side-effects` before input-only graph code can be re-executed. `capture --capture-exceptions` opts into the versioned v2 capture with a privacy-preserving exception-class outcome so callers can replay and minimize exception failures without storing message or stack text; v1 capture behavior remains the default.

An optional LangChain `BaseChatModel` wrapper routes buffered `.invoke()` calls through the cassette boundary. The reusable `BoundaryTool` wraps JSON-argument tools and proves replay avoids its live side effect. `BoundaryOpenAIResponses` now wraps buffered synchronous calls to the official OpenAI Responses API, records a JSON-safe response envelope, and returns the same Chrolo result type on replay. Its tests cover real SDK response serialization, offline replay, request mismatch, recorded errors, and streaming rejection. It does not return the SDK's typed response and does not support async or streaming; calls outside the wrapper remain uncaptured. The LangGraph integration test covers LangChain model and tool calls together.

`chrolo serve` provides a responsive, in-memory trace desk for inspect/scan/redact/filter operations. Scan results expose only paths and can be added to the redaction list with one tap. Loopback is the default; remote binding requires Basic authentication. For all-interface binds, startup lists discoverable private IPv4 URLs and prints the required username and password. Optional HTTPS accepts a user-supplied PEM certificate and key; without TLS, remote credentials and uploads are unencrypted at the app layer and the server warns at startup. Access from outside the LAN requires a private VPN and a certificate trusted by the phone. HTTP/TLS behavior is integration-tested; visual QA on a physical phone remains outstanding.

Continue reading generic M0 scenarios and `chrolo.langgraph.v1`. Write new captures in the new format. Clearly distinguish M1 graph re-execution from M2 offline interaction replay. Provide migration only where semantics are preserved; otherwise explain what old traces cannot recover.

### Acceptance criteria

- Existing documented workflows work.
- CLI output distinguishes graph re-execution from cassette replay.
- Invalid traces and replay failures explain actionable causes without dumping sensitive payloads.

## Phase 7: Reliability benchmark and release gates

### Current status

`chrolo benchmark` runs offline synthetic cases and compares Chrolo with a naïve single-pass deletion baseline. Its cassette case removes an irrelevant event and prunes the associated tool call from the output cassette, then verifies strict replay of the minimized cassette without live calls. `--dataset` accepts a versioned, user-supplied labeled case dataset and reports reproduction rate, signature preservation, shrink percentage, candidate checks, replay timing, fixture sizes, and optional simulated-fix sensitivity.

The AgentTelemetry bridge covers 78 reproducible positive cases over 20 repetitions (1,560/1,560 full-trace replays): every included detector signature reproduced, mean event reduction was 74.23%, all results were 1-minimal under tested cascade deletions, and all 78 paired fault-disabled controls cleared the injected signature. The full 170-row source set regenerates as 78 true positives, 62 false negatives, 0 false positives, and 30 true negatives; the 78 detected cases alone feed minimization. The naïve baseline reached the same mean reduction with fewer checks, so this run demonstrates signal preservation rather than a reduction advantage. This fault-disabled control measures detector sensitivity, not a source-code patch or task-level agent failure reproduction.

The AgentFailureBench adapter compares recorded synthetic failed and corrected trajectories. On the held-out 1,000-row test split, 562 cases have tool-call or tool-output differences; 283 clean rows and 155 label-only failures are excluded. Across 20 repetitions, all 562 included differences reproduced and survived minimization; the paired corrected-trace intervention cleared them all. Mean shrink was 16.00% versus 34.09% for the naïve baseline (10.60 versus 4.20 candidate checks per case). The benchmark replays observations and requires exact witness preservation; it does not execute tools or independently verify task outcomes. This gives Chrolo a synthetic paired trace-differential benchmark, while a real task-level minimization score remains open.

A local copy of the public MAD/MAST corpus contains trace strings and LLM-generated labels without deterministic runners or failure oracles; it is not a valid minimization benchmark input. A separate SWE-bench Multimodal task-oracle smoke check verified one real test runner and reference-patch sensitivity (3 expected failures plus 30 passing controls on base; all 33 passing with the reference fix), but those tasks do not include agent traces and therefore are not Chrolo minimization cases. The SWE-bench harness returned success even when Jest reported the expected failures, so its test-summary parser—not shell exit status—was used. Provider-specific release claims remain future work.

### Remaining external work

The real-failure comparison cannot be completed responsibly without stable, executable failure oracles and deterministic runners for selected traces. The OpenAI adapter currently has a synchronous Responses API scope; Chat Completions, async, streaming, realtime, and other providers need their own adapters and integration fixtures. The LangChain adapter is validated against a fake model only. Keep M2 experimental until supported combinations are precisely documented and verified.

Build labeled deterministic scenarios for irrelevant event removal, model-dependent and tool-dependent failures, changed requests, unrelated exceptions, mutating tools, redacted/incomplete cassettes, and nondeterminism outside wrapped boundaries.

Measure repeated reproduction rate, false acceptance/rejection of signatures, reduction ratio, candidate count, cassette calls consumed, replay overhead, fixture size, redaction counts, and regression sensitivity after a simulated fix.

Do not call M2 ready for real agent traces until replay never silently calls live services, signature preservation and tool isolation pass the benchmark, trace limitations are clear, and supported framework/provider combinations are listed precisely.

## Suggested delivery order

1. Complete M1 contract and clean-environment verification.
2. Review and freeze M2 schema and strict matching rules.
3. Implement the framework-neutral cassette engine with fake boundaries.
4. Add a buffered LangChain chat wrapper, JSON-argument tool wrapper, synchronous OpenAI Responses adapter, and LangGraph integration.
5. Connect replay to minimization and failure classification.
6. Add privacy controls, inspection, and compatibility support.
7. Benchmark reliability and document supported use cases before broadening product claims.
