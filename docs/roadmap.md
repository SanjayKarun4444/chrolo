# Chrolo roadmap: reliable agent failure replay

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

M0 provides dependency-preserving input event minimization and pytest generation using a caller-supplied deterministic runner and failure oracle. M1 adds experimental LangGraph-compatible input-event capture and replay: it invokes a fresh graph with a reduced input event list and can save state snapshots. M1 does not record model responses, tool responses, checkpoint history, or arbitrary side effects.

The M1 contract stabilization work clarifies malformed event rejection and distinguishes baseline runner errors from baseline oracle errors. Candidate exceptions are rejected as non-reproductions. The M1 suite has been exercised with the real LangGraph integration when that optional dependency is installed.

## Phase 0: Stabilize M1 contracts

### Work

1. Document event structure, ordered `requires` dependencies, cascade deletion, runner/oracle responsibilities, and what the `checks` count includes.
2. Validate event arrays, event objects, required fields, payload objects, and dependency arrays of string IDs.
3. Distinguish baseline runner exceptions from oracle exceptions. Reject all candidate exceptions as non-reproductions.
4. Keep the existing result API compatible where possible; explain budget limits and the scope of the 1-minimality claim.
5. Keep CLI errors concise and actionable.
6. Preserve current M0 and M1 fixture behavior and document the boundary between snapshots and replayable inputs.

### Acceptance criteria

- Core and adapter tests pass in a clean supported environment.
- A candidate that raises a different exception is never counted as reproducing the original failure.
- Malformed dependencies are rejected with useful validation errors.
- The optional real LangGraph integration test passes when LangGraph is installed and skips cleanly when absent.
- Existing M0 and M1 examples continue to work.

## Phase 1: Specify the M2 interaction trace

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

### Candidate lifecycle

For each candidate, create a fresh graph/agent and replay session, execute against the reduced inputs, collect result/error/mismatches/consumption, and evaluate the original oracle only if execution is valid under the declared replay policy. Accept only if the original signature remains and replay completed without mismatch.

Initially use strict ordered matching. Report missing, unexpected, reordered, mismatched, and unused interactions rather than silently skipping or semantically guessing a match. Any later relaxed matching policy must be explicit and tested for ambiguity.

### Result reporting

Report original/reduced event counts, oracle/replay checks, accepted deletion history, candidate rejection counts by reason, unused interaction records, and whether the result is 1-minimal under the cascade-deletion strategy and available budget.

### Acceptance criteria

- A known bug reduces with fixed model/tool responses.
- A different exception or changed request/order is rejected with a clear reason.
- Generic M0 minimization continues to work.

## Phase 5: Trace safety and privacy

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

Continue reading generic M0 scenarios and `chrolo.langgraph.v1`. Write new captures in the new format. Clearly distinguish M1 graph re-execution from M2 offline interaction replay. Provide migration only where semantics are preserved; otherwise explain what old traces cannot recover.

### Acceptance criteria

- Existing documented workflows work.
- CLI output distinguishes graph re-execution from cassette replay.
- Invalid traces and replay failures explain actionable causes without dumping sensitive payloads.

## Phase 7: Reliability benchmark and release gates

Build labeled deterministic scenarios for irrelevant event removal, model-dependent and tool-dependent failures, changed requests, unrelated exceptions, mutating tools, redacted/incomplete cassettes, and nondeterminism outside wrapped boundaries.

Measure repeated reproduction rate, false acceptance/rejection of signatures, reduction ratio, candidate count, cassette calls consumed, replay overhead, fixture size, redaction counts, and regression sensitivity after a simulated fix.

Do not call M2 ready for real agent traces until replay never silently calls live services, signature preservation and tool isolation pass the benchmark, trace limitations are clear, and supported framework/provider combinations are listed precisely.

## Suggested delivery order

1. Complete M1 contract and clean-environment verification.
2. Review and freeze M2 schema and strict matching rules.
3. Implement the framework-neutral cassette engine with fake boundaries.
4. Add one model wrapper, one tool wrapper, and LangGraph integration.
5. Connect replay to minimization and failure classification.
6. Add privacy controls, inspection, and compatibility support.
7. Benchmark reliability and document supported use cases before broadening product claims.
