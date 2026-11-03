# Chrolo progress checkpoint

Checkpoint: 2026-11-02

This note preserves the current state and resume point for the Chrolo roadmap. The working tree contains a broad set of uncommitted feature and documentation changes; inspect `git status` and review diffs before committing or reverting anything.

## Implemented and verified

- M0/M1 event minimization, dependency handling, exception classification, LangGraph capture/replay, and CLI workflows are documented with runnable examples.
- M2 has versioned interaction cassettes, strict offline replay, explicit model/tool wrappers, LangGraph integration, and candidate-only ordered-subsequence replay during minimization. Normal replay remains strict; minimized cassettes receive a final strict replay.
- Privacy controls include payload-free inspect summaries, heuristic scan, path redaction/filtering, non-replayable sanitized traces, opt-in state snapshots, and authenticated remote Trace Desk access with optional TLS.
- M3 provides experimental `TrackedState` transitions and dependency inference.
- The mobile Trace Desk prints discovered private IPv4 URLs and its fixed Basic Auth username on all-interface binds. Phone access from outside the LAN requires the workstation and phone to share a private VPN; TLS certificates must be trusted by the phone and match the address used.
- Latest full-suite verification: `87 passed` (`.venv\Scripts\python.exe -m pytest -q`). `git diff --check` reports only line-ending conversion warnings for existing changed files.
- The synthetic repeat-20 benchmark verifies that cassette minimization reduces 3 interactions to 2, strict replay preserves the signature, and replay makes zero live model/tool calls.
- AgentTelemetry bridge is reproducible at source commit `33feec2d434ed4a77862fd1698f09bc23926df1b`. It regenerates all 170 source rows, validates target-detector labels and control false-alarm lists, and includes 78 reproducible positive cases. At 20 repetitions: 1,560/1,560 full-trace replays preserved the detector signature; 78/78 results were 1-minimal; the mean event reduction was 74.23%; 78/78 paired fault-disabled controls cleared the signature. The naïve baseline had the same mean reduction with fewer checks. Source detector metrics: 78 TP, 62 FN, 0 FP, 30 TN (55.71% recall, 100% specificity on this generated sample).
- The report is in ignored output `results\agenttelemetry_chrolo_report_repeat20.json`; dataset `results\agenttelemetry_chrolo_dataset.json`.
- A Docker SWE-bench task-oracle smoke check passed its base/reference-patch sensitivity check: base had 3 expected test failures and 30 passing controls; the reference patch passed all 33. Docker Desktop is healthy, but sandboxed Docker commands cannot open its named pipe and require elevated access. This task does not include an agent trace, so it is not a Chrolo minimization benchmark.

## Still open

1. **Task-level reliability benchmark:** the available MAD/MAST rows have labels but no deterministic runner/oracle. AgentTelemetry measures telemetry detector preservation, not whether the underlying agent task failed. SWE-bench verifies a code/test oracle but has no paired agent trace. A real task-level minimization score still needs traces plus repeatable runners and stable failure signatures.
2. **Phone visual QA:** HTTP/TLS integration tests pass, but a physical-phone or browser visual check has not been completed. The saved desktop browser policy rejected access to the local `127.0.0.1` preview even after the user approved it; do not retry through another browser or an indirect preview path. Resume only if a permitted preview method becomes available.
3. **Provider coverage:** the LangChain adapter has fake-model/integration coverage; provider-specific SDK, streaming, and arbitrary unwrapped side-effect guarantees are not established. Keep M2 experimental.
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

Next useful work: find a permissioned trace corpus with executable deterministic task runners and failure oracles, or implement a new data collector that creates genuinely paired agent-task failures and fixes. Keep the mobile visual QA and provider-coverage gaps explicit while proceeding.
