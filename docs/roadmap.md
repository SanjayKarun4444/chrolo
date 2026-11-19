# Chrolo roadmap: reliable agent failure replay

Current implementation and resume checkpoint: [progress.md](progress.md).

## Active priorities (2026-11-18)

The three supported SDK/LangGraph capture-to-generated-test workflows and a separate installed-wheel workflow are verified locally. Two independent action-dependent real-agent cases have a repeatable 19-check gate. Historical checkpoints below retain the sequence of that work; pending statements in those checkpoints do not override this status.

1. Build and try the pilot workflow: one documented path from a developer's captured failure to a minimal case and a regression test, with useful failure messages and no manual trace editing. Normal wheel dependency installation and both installed-wheel checks now pass locally; hosted CI remains unverified until publication is authorized.
2. Validate the new PyFilesystem installed-opener oracle on the actual image/full trajectories; only synthetic contract checks have run. Prepare task-specific oracles for JWST, SOSW, and CEKit. The frozen-selection multi-repository screening harness is implemented and the 11 existing inputs pass preflight; new task execution remains open. Retain negative/inconclusive cases and require passing empty controls before minimization.
3. Keep experimental scope explicit. Publishing and physical-phone QA remain deferred; commit completed work locally without pushing.

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

M0 provides dependency-preserving input event minimization and pytest generation using a caller-supplied deterministic runner and failure oracle. M1 adds experimental LangGraph-compatible input-event capture and replay: it invokes a fresh graph with a reduced input event list; full state snapshots are opt-in. M2 adds a framework-neutral cassette for explicitly wrapped model/tool calls and LangGraph integration. Explicit wrappers cover buffered synchronous OpenAI Responses, OpenAI Chat Completions, and Anthropic Messages; arbitrary unwrapped side effects remain outside capture.

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

New interaction cassettes use `chrolo.interactions.v2` with a schema version, Chrolo version, UTC capture timestamp, execution UUID, adapter-version map, and optional per-call JSON context that is checked during replay. Replay remains backward compatible with v1 cassettes. The enclosing graph capture continues to hold initial state and input events. With opt-in `--capture-exceptions`, Chrolo writes a `chrolo.langgraph.v2` capture containing a versioned `chrolo.outcome.v1` status and exception class (never message or stack); replay returns that outcome as structured data for the caller-supplied oracle, and the default capture behavior still writes `chrolo.langgraph.v1` and raises on exceptions. Opt-in project metadata and explicitly supplied environment fingerprints use `chrolo.langgraph.v3`; no environment values are collected automatically, and `inspect` omits fingerprint values. M2 remains experimental because provider and side-effect coverage is still limited.

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

An optional LangChain `BaseChatModel` wrapper routes buffered `.invoke()` calls through the cassette boundary. The reusable `BoundaryTool` wraps JSON-argument tools and proves replay avoids its live side effect. `BoundaryOpenAIResponses` and `BoundaryOpenAIChatCompletions` wrap buffered synchronous calls to the OpenAI Responses and Chat Completions APIs; `BoundaryAnthropicMessages` wraps buffered synchronous Anthropic Messages calls. They record JSON-safe response envelopes, preserve provider tool-call blocks, and return Chrolo result objects on replay. Tests cover offline replay, request mismatch, recorded errors, and streaming rejection; OpenAI additionally has real SDK serialization fixtures. These adapters do not return SDK-typed responses and do not support async or streaming; calls outside the wrappers remain uncaptured. The LangGraph integration test covers LangChain model and tool calls together.

`chrolo serve` provides a responsive, in-memory trace desk for inspect/scan/redact/filter operations. Scan results expose only paths and can be added to the redaction list with one tap. Selecting another file clears the previous summary and scan findings. A **Clear data from this page** control drops the selected file, loaded trace, findings, and path inputs; generation checks prevent in-flight responses from restoring cleared results. Mobile controls use a 44px minimum target, safe-area-aware page padding, and visible keyboard focus. The content security policy blocks embedding in other sites. Unauthenticated loopback access rejects non-loopback `Host` headers to block DNS-rebinding access from hostile sites. Loopback is the default; remote binding requires Basic authentication. For all-interface binds, startup lists discovered private IPv4 and Tailscale-range `100.64.0.0/10` addresses; `--advertise-ip` can explicitly add a private or VPN IPv4 when auto-discovery misses it. Startup prints the required username and password. Optional HTTPS accepts a user-supplied PEM certificate and key; without TLS, remote credentials and uploads are unencrypted at the app layer and the server warns at startup. Access from outside the LAN requires a private VPN and a certificate trusted by the phone. HTTP/TLS behavior is integration-tested; visual QA on a physical phone remains outstanding.

Continue reading generic M0 scenarios and `chrolo.langgraph.v1`. Write new captures in the new format. Clearly distinguish M1 graph re-execution from M2 offline interaction replay. Provide migration only where semantics are preserved; otherwise explain what old traces cannot recover.

### Acceptance criteria

- Existing documented workflows work.
- CLI output distinguishes graph re-execution from cassette replay.
- Invalid traces and replay failures explain actionable causes without dumping sensitive payloads.

## Phase 7: Reliability benchmark and release gates

### Current status

`chrolo benchmark` runs offline synthetic cases and compares Chrolo with a naïve single-pass deletion baseline. Its cassette case removes an irrelevant event and prunes the associated tool call from the output cassette, then verifies strict replay of the minimized cassette without live calls. `--dataset` accepts a versioned, user-supplied labeled case dataset and reports reproduction rate, signature preservation, shrink percentage, candidate checks, replay timing, fixture sizes, and optional simulated-fix sensitivity.

The AgentTelemetry bridge covers 78 reproducible positive cases over 20 repetitions (1,560/1,560 full-trace replays): every included detector signature reproduced, mean event reduction was 74.23%, all results were 1-minimal under tested cascade deletions, and all 78 paired fault-disabled controls cleared the injected signature. The full 170-row source set regenerates as 78 true positives, 62 false negatives, 0 false positives, and 30 true negatives; the 78 detected cases alone feed minimization. The naïve baseline reached the same mean reduction with fewer checks, so this run demonstrates signal preservation rather than a reduction advantage. This fault-disabled control measures detector sensitivity, not a source-code patch or task-level agent failure reproduction.

The AgentFailureBench adapter compares recorded synthetic failed and corrected trajectories. On the held-out 1,000-row test split, 562 cases have tool-call or tool-output differences; 283 clean rows and 155 label-only failures are excluded. Across 20 repetitions, all 562 included differences reproduced and survived minimization; the paired corrected-trace intervention cleared them all. Mean shrink was 16.00% versus 34.09% for the naïve baseline (10.60 versus 4.20 candidate checks per case). The benchmark replays observations and requires exact witness preservation; it does not execute tools or independently verify task outcomes.

`examples/task_oracle_dataset.json` adds three deterministic synthetic tasks with an in-memory environment runner that executes each retained tool call, carries outputs into later arguments, and evaluates an exact state-based failure oracle. Across 20 repetitions, all three failures reproduced and survived minimization, all were 1-minimal, and each fixed runner cleared the original failure. Mean shrink was 50.00%, matching the naïve baseline, with 15 versus 6 mean checks. This proves the Chrolo runner/oracle path against executable synthetic task state, but not against production agent traces or external task environments. The remaining reliability gap is an independently executable benchmark over real agent traces.

A local copy of the public MAD/MAST corpus contains trace strings and LLM-generated labels without deterministic runners or failure oracles; it is not a valid minimization benchmark input. A separate SWE-bench Multimodal task-oracle smoke check verified one real test runner and reference-patch sensitivity (3 expected failures plus 30 passing controls on base; all 33 passing with the reference fix), but those tasks do not include agent traces and therefore are not Chrolo minimization cases. The SWE-bench harness returned success even when Jest reported the expected failures, so its test-summary parser—not shell exit status—was used. Provider-specific release claims remain future work.

### Remaining external work

**Candidate corpus found; execution not yet validated.** The [SWE-rebench OpenHands trajectories](https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories) corpus is CC BY 4.0 and documents 67,074 OpenHands v0.54 runs on real GitHub issues, with recorded `resolved` outcomes and task IDs that join to [SWE-rebench](https://huggingface.co/datasets/nebius/SWE-rebench) task images and test metadata. A local metadata join verified the unresolved `PlasmaFAIR__sdf-xarray-24` trajectory against image `swerebench/sweb.eval.x86_64.plasmafair_1776_sdf-xarray-24`, eight `FAIL_TO_PASS` tests, two `PASS_TO_PASS` controls, and the task repository's BSD-3-Clause license. The trace has 185 messages and 92 tool calls (44 Bash, 44 file-editor, 3 think, 1 finish). The benchmark now requires all configured `FAIL_TO_PASS` tests to be `FAILED` on baseline and excludes pytest `ERROR`/`SKIPPED` from the original failure signature, so a test-runner failure cannot stand in for the issue bug. This is a concrete real-agent/task-oracle candidate, not yet a Chrolo result: we have not re-executed its recorded actions in the isolated task image or measured a minimized trace. Docker is currently stopped and the user asked to leave it blocked, so do not run the task until that constraint changes.

AgentFailureBench provides synthetic paired tool traces, and AgentTelemetry provides a deterministic fault-injection runner; neither substitutes for execution of this real-agent candidate. Keep the real-agent reliability score open until clean-container action replay, the test oracle, and repeated minimization are run together.

The provider adapters currently cover buffered synchronous OpenAI Responses, OpenAI Chat Completions, and Anthropic Messages calls. They preserve JSON response envelopes and tool-call data, return Chrolo result objects, and explicitly reject streaming and async calls. Real SDK response fixtures run in the GitHub Actions matrix after installing the provider extras. The LangChain wrapper is validated with a fake model and the LangGraph integration test. Realtime, other provider SDKs, SDK-typed response reconstruction, and unwrapped side effects remain unsupported; keep M2 experimental.

The checked-in synthetic scenarios measure repeated reproduction, signature preservation, shrink, candidate checks, cassette pruning and consumption, replay timing, fixture size, and sensitivity to paired fixes/controls. Their results show signal preservation but no reduction advantage over the naïve baseline in AgentTelemetry or the task-oracle suite. Do not claim real-agent reliability or M2 readiness until the actual execution boundaries, task oracle, and supported framework/provider combinations are independently exercised.

Responsive browser preview QA has been completed at 390px and 320px, including scan, redaction-path selection, and clear-page-state checks on a synthetic trace. Physical-phone QA over the private VPN and with a trusted TLS certificate remains outstanding; the in-app browser is currently disabled by Work settings, so use only a permitted route if that changes. HTTP/TLS integration tests and responsive-layout assertions pass. The current GitHub origin also returns `Repository not found`, so local commits and the CI workflow have not been published or run by GitHub Actions.

## Remaining delivery order

Latest release-check checkpoint (2026-11-10): all three supported synchronous provider interfaces (Responses, Chat Completions, Anthropic Messages) now pass the actual-SDK/LangGraph capture-to-generated-pytest matrix: 21 checks, zero replay callbacks, and all six cross-interface substitutions rejected. A fresh non-editable wheel install separately passes the core CLI workflow outside the checkout, including generated-test failure before and success after the fixture fix. The package-smoke CI job includes this check; local YAML parsing and wheel execution pass, while hosted CI execution remains unverified until an authorized push. Full suite: 215 passed, two opt-in Docker gates skipped. Next: broader independent failure/task selection and release-readiness review of replay/privacy boundaries, retaining experimental status and explicitly excluding streaming, async, hosted model quality, and unwrapped effects. Completed work is committed locally without pushing; phone QA remains deferred.

Latest provider-matrix checkpoint (2026-11-10): Anthropic Messages now joins OpenAI Responses in the actual-SDK/LangGraph capture-to-generated-regression gate. Both use deterministic HTTP/tool fixtures, preserve provider-specific tool blocks, prune three events to one and three calls to two, replay with zero callbacks, and verify the before/after application fix. Fourteen shared tests cover positive paths, recorded provider/tool errors, corrupt/missing/unused boundaries, and bidirectional wrong-provider rejection. Next: equivalent end-to-end coverage for the supported Chat Completions adapter and packaged-workflow release validation. Hosted model quality, streaming/async behavior, and unwrapped effects remain outside this evidence. Completed work is committed locally under the user's standing preference; nothing is pushed. Phone QA remains separately deferred.

2026-11-10 user-workflow checkpoint: installed OpenAI Responses SDK + actual LangGraph now pass capture -> offline replay -> minimization -> generated regression pytest using deterministic local HTTP/tool fixtures. Three events reduce to one, three calls to two; the generated test fails on the original application logic and passes on the corrected graph with no new interactions. Six tests cover positive flow, SDK function-call preservation, provider/tool error replay, and mismatch/missing/unused-record rejection with zero replay callbacks. Full suite: 200 passed, two opt-in Docker checks skipped. Reviewable artifacts and instructions are documented in `examples/README.md`. This closes that bounded synchronous Responses/LangGraph workflow gate, not hosted-model quality or all M2 integrations. Next: equivalent end-to-end coverage for additional supported provider boundaries and stronger independent failure/task selection; retain experimental status until that evidence and release validation are adequate. Publishing and phone QA remain separately deferred.

Latest regression-gate checkpoint (2026-11-07): both independent verified cases now have a one-command local gate, `python -m benchmarks.real_regressions`, with fingerprinted local fixtures and pinned images. All 19 checks pass; opt-in pytest runs both real cases successfully. Ordinary tests pass 194 with the two Docker cases skipped unless enabled. Instructions, fixture prerequisites, expected reproduction semantics, and exit codes are in `benchmarks/README.md`. The next evidence work is independent failure/task expansion and checking provider/framework replay boundaries against these release expectations; M2 remains experimental. Publishing and phone QA remain separately deferred.

Latest independent-case checkpoint (2026-11-07): added Vermouth legacy-tool compatibility beside PyHOCON packaging. Vermouth's 68-action trace reproduces 3/3 and reduces to two necessary recorded edits; minimal and empty controls repeat 3/3, all 31 controls pass, and removing the implementation change passes all 33 checks. An 82-action real comparison retaining legacy fallback passes the same checks. The compatibility evaluator uses an explicit deterministic executable fixture, not an actual legacy DSSP binary or scientific-accuracy oracle; unrelated failures cannot replace the target protocol signature. Full suite: 183 passed. The next work is to turn the verified cases into repeatable regression/release checks and expand independent task selection without treating these hand-selected examples as a general reliability estimate. M2 remains experimental; phone QA and publishing remain separately deferred.

Latest cohort checkpoint (2026-11-07): evaluated nine preselected unresolved PyHOCON traces (776 recorded actions) on the pinned task image. All empty controls pass; one reproduces the already verified packaging regression, eight pass the packaging target, and none are inconclusive. All retain 209 passing controls. Reference-patch scoring now requires every target and control to pass and retains inconclusive attempts in the denominator. The immediate next evidence gate is an independent repository/failure mode, followed by a broader task selection with explicit passing empty controls. Nine runs of one issue do not constitute nine independent task cases. Details and rerun instructions are in `benchmarks/README.md`.

2026-11-07 action-dependence update: the PyHOCON packaging case now satisfies the empty-action control gate. Its original 94-action trace reproduces 3/3 and reduces to a single recorded destructive cleanup command in 13 checks. The minimal trace reproduces 3/3 while 209 controls pass; action removal passes the target and all controls 3/3. This is a new independent packaging oracle on one real public trace, rather than the dataset's issue-fix score. The next reliability evidence work is a larger, independently selected set of action-dependent cases with explicit passing empty controls and appropriate interventions. Keep M2 experimental; phone QA and publishing remain separately deferred. Reproduction instructions are in `benchmarks/README.md`.

2026-11-07 update: the PlasmaFAIR task replay and repeated minimization completed using a locally repaired image, explicit test environment, and the dataset's evaluation test patch. All 3 full-action repeats reproduced; 88 actions reduced to zero; all 3 reference-fix checks cleared the signature. This verifies this task's executable replay/oracle path but reveals an empty-action confound: the original repository bug already fails without agent actions. Before making action-dependent real-agent reliability claims, select a case whose empty-action control clears the target failure. The older pending-execution notes below describe the pre-validation checkpoint.

1. Execute `chrolo benchmark-openhands` against the joined image's `FAIL_TO_PASS`/`PASS_TO_PASS` tests and confirm task-image replay behavior. The input builder, benchmark orchestration, workspace-path mapping, and isolated runner are now wired and unit-tested; actual task-image replay is still pending.
2. Run repeated minimization and the repeated reference-patch intervention on that corpus; the CLI now implements both, but Docker task-image validation remains outstanding. Keep results separate from synthetic and recorded-trace differential scores.
3. Complete the Trace Desk physical-phone visual check, including trusted TLS/VPN setup. The local responsive browser preview is already documented in `docs/progress.md`.
4. Publish the local commits to the confirmed repository so the corrected CI workflow executes on its Python matrix.
5. Reassess M2's experimental status after provider, framework, and real-agent runner coverage has evidence at the required scope.
