# Chrolo progress checkpoint

Checkpoint: 2026-11-03

Latest local change after this checkpoint: Trace Desk mobile usability now honors device safe-area insets, gives controls a 44px minimum touch target, and adds visible keyboard focus styling. The page-level static test covers these guarantees. Full suite: 104 passed.

This note preserves the current state and resume point for the Chrolo roadmap. The main feature set and documentation were committed and pushed to `origin/master` in commits `a784787`, `2cf587e`, and `b84e478`. The AgentFailureBench, OpenAI adapter, executable synthetic task-oracle, and Trace Desk privacy milestones are committed locally on `master`, six commits ahead of `origin/master`. The last push failed because GitHub returned `Repository not found` for the configured origin `https://github.com/SanjayKarun4444/chrolo.git`; the intended remote URL needs confirmation before retrying.

## Implemented and verified

- M0/M1 event minimization, dependency handling, exception classification, LangGraph capture/replay, and CLI workflows are documented with runnable examples.
- M2 has versioned interaction cassettes, strict offline replay, explicit model/tool wrappers, LangGraph integration, and candidate-only ordered-subsequence replay during minimization. Normal replay remains strict; minimized cassettes receive a final strict replay.
- Privacy controls include payload-free inspect summaries, heuristic scan, path redaction/filtering, non-replayable sanitized traces, opt-in state snapshots, authenticated remote Trace Desk access with optional TLS, and a clear-page-state control that invalidates pending UI responses.
- M3 provides experimental `TrackedState` transitions and dependency inference.
- The mobile Trace Desk prints discovered private IPv4 URLs and its fixed Basic Auth username on all-interface binds. Phone access from outside the LAN requires the workstation and phone to share a private VPN; TLS certificates must be trusted by the phone and match the address used.
- Latest full-suite verification: `104 passed` (`.venv\Scripts\python.exe -m pytest -q`) with OpenAI SDK 2.54.0 installed. Nine OpenAI adapter tests cover both real SDK response types, tool-call serialization, fake-client record/replay, mismatch rejection, recorded errors, JSON compatibility, and streaming rejection. The task-oracle tests also cover fresh state, causal chains, and paired fixes. The suite needed elevated access for its existing localhost HTTP/TLS integration tests. No provider requests were made.
- The synthetic repeat-20 benchmark verifies that cassette minimization reduces 3 interactions to 2, strict replay preserves the signature, and replay makes zero live model/tool calls.
- AgentTelemetry bridge is reproducible at source commit `33feec2d434ed4a77862fd1698f09bc23926df1b`. It regenerates all 170 source rows, validates target-detector labels and control false-alarm lists, and includes 78 reproducible positive cases. At 20 repetitions: 1,560/1,560 full-trace replays preserved the detector signature; 78/78 results were 1-minimal; the mean event reduction was 74.23%; 78/78 paired fault-disabled controls cleared the signature. The naïve baseline had the same mean reduction with fewer checks. Source detector metrics: 78 TP, 62 FN, 0 FP, 30 TN (55.71% recall, 100% specificity on this generated sample).
- The report is in ignored output `results\agenttelemetry_chrolo_report_repeat20.json`; dataset `results\agenttelemetry_chrolo_dataset.json`.
- AgentFailureBench now has a deterministic paired trace-difference adapter and builder in `src/chrolo/agentfailurebench.py` and `benchmarks/build_agentfailurebench_dataset.py`. It accepts only synthetic, QC-passed records and only includes exact differences in tool calls or outputs. On the held-out 1,000-row test split (dataset revision `93257fb`, CC BY 4.0), it included 562 cases, skipped 283 clean rows, and skipped 155 labeled failures with no execution-visible difference. At 20 repetitions, all 562/562 reproduced and preserved the exact witness; all 562 were 1-minimal under tested deletion cascades; all 562 paired corrected-trace interventions cleared the witness. Mean shrink was 16.00% vs 34.09% for the naïve baseline, with 10.60 vs 4.20 mean candidate checks. This is a synthetic **recorded-trace differential** benchmark, not independent task execution or production-agent reliability. Dataset/report outputs remain in ignored `results/`.
- AgentFailureBench unit and integration tests contributed six tests to the 93-test suite before the OpenAI adapter was added.
- `BoundaryOpenAIResponses` and `BoundaryOpenAIChatCompletions` add optional synchronous, buffered adapters for `client.responses.create(...)` and `client.chat.completions.create(...)` in `src/chrolo/openai_adapter.py`. They record JSON-safe response content, preserve tool-call items, and return stable Chrolo result objects in recording and replay. Streaming, async, and SDK typed-response reconstruction are unsupported. Install with `pip install -e '.[openai]'`.
- The executable task-oracle example now runs retained tool calls against fresh deterministic in-memory task state. Three cases cover wrong ticket routing, duplicate invoice payment, and protected-file deletion; earlier tool outputs can feed later arguments through dependency edges. At 20 repetitions all three exact task-state failures reproduced and were preserved, all three minimized results were 1-minimal, all three simulated fixes cleared the signature, and mean reduction was 50.00% (equal to the naïve baseline, with 15 vs 6 mean candidate checks). `run_dataset` now propagates `metadata.synthetic_only` into its report. This is synthetic runner/oracle validation, not a real-agent task benchmark.
- The SWE-agent trajectory source is licensed CC BY 4.0 and has 80,036 rows. The local Parquet shard does not contain the Docker-smoke task, and the public dataset search endpoint timed out; no additional trajectory was downloaded. See [the source dataset card](https://huggingface.co/datasets/ElenaFu/SWE-agent-trajectories).
- A previous Docker SWE-bench task-oracle smoke check passed its base/reference-patch sensitivity check: base had 3 expected test failures and 30 passing controls; the reference patch passed all 33. The current session cannot reach the Docker engine: `docker info` hangs or reports named-pipe access errors, `docker desktop status` cannot retrieve status, and the Docker Desktop CLI's “already running” response conflicts with the missing engine. The UI window is listed but could not be activated (`failed to activate captured window`). No new Docker task run was completed. This task still has no paired agent trace, so it is not a Chrolo minimization benchmark.

## Still open

1. **Real-agent task-level reliability benchmark:** Chrolo now has executable synthetic task oracles, plus AgentFailureBench's paired recorded-trace differential set. AgentTelemetry measures telemetry detector preservation. SWE-bench verifies a code/test oracle but has no paired agent trace. A real-agent task-level score still needs permissioned agent traces plus repeatable environment runners and stable failure signatures.
2. **Phone visual QA:** HTTP/TLS integration tests pass, but a physical-phone or browser visual check has not been completed. The saved desktop browser policy rejected access to the local `127.0.0.1` preview even after the user approved it; do not retry through another browser or an indirect preview path. Resume only if a permitted preview method becomes available.
3. **Provider coverage:** OpenAI Responses and Chat Completions now have buffered synchronous adapters with JSON-safe replay. Async, streaming, Realtime, other SDKs, and arbitrary unwrapped side effects remain outside coverage. Keep M2 experimental.
4. **Benchmark tradeoff:** AgentTelemetry results show signal preservation, but no reduction advantage over the naïve baseline (same 74.23% mean shrink, with 21.33 vs 14.77 mean candidate checks).

## Resume commands

Run the Chrolo suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Rebuild and rerun the AgentTelemetry bridge when its checkout is present:

```powershell
$env:PYTHONPATH = "AgentTelemetry\src;AgentTelemetry"
.\AgentTelemetry\.venv\Scripts\python.exe benchmarks\build_agenttelemetry_dataset.py --agenttelemetry-root AgentTelemetry --input AgentTelemetry\src\agenttelemetry_inspect\data\traces_v1.jsonl --output results\agenttelemetry_chrolo_dataset.json
$env:PYTHONPATH = "src;AgentTelemetry\src;AgentTelemetry"
$env:AGENTTELEMETRY_ROOT = "AgentTelemetry"
.\AgentTelemetry\.venv\Scripts\python.exe -m chrolo benchmark --dataset results\agenttelemetry_chrolo_dataset.json --repeat 20
```

Next substantive step: add a permissioned executable real-agent trace corpus and runner, or collect paired traces from a deterministic environment. The generated dataset and benchmark reports are ignored and should remain uncommitted. Main open product gaps are real-agent task-level reliability, physical-phone visual QA through an allowed preview path, and provider coverage beyond synchronous buffered OpenAI calls. Confirm the intended GitHub remote before publishing the five local commits; the last push returned `Repository not found`. Do not retry local browser preview through another surface while the saved policy forbids it.
