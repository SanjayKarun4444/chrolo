# Chrolo progress checkpoint

Checkpoint: 2026-11-03

This note preserves the current state and resume point for the Chrolo roadmap. The main feature set and documentation were committed and pushed to `origin/master` in commits `a784787`, `2cf587e`, and `b84e478`. The AgentFailureBench integration is committed locally as `9c3dee6`. Its push failed because GitHub returned `Repository not found` for the configured origin `https://github.com/SanjayKarun4444/chrolo.git`; the intended remote URL needs confirmation before retrying.

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
- AgentFailureBench now has a deterministic paired trace-difference adapter and builder in `src/chrolo/agentfailurebench.py` and `benchmarks/build_agentfailurebench_dataset.py`. It accepts only synthetic, QC-passed records and only includes exact differences in tool calls or outputs. On the held-out 1,000-row test split (dataset revision `93257fb`, CC BY 4.0), it included 562 cases, skipped 283 clean rows, and skipped 155 labeled failures with no execution-visible difference. At 20 repetitions, all 562/562 reproduced and preserved the exact witness; all 562 were 1-minimal under tested deletion cascades; all 562 paired corrected-trace interventions cleared the witness. Mean shrink was 16.00% vs 34.09% for the naïve baseline, with 10.60 vs 4.20 mean candidate checks. This is a synthetic **recorded-trace differential** benchmark, not independent task execution or production-agent reliability. Dataset/report outputs remain in ignored `results/`.
- AgentFailureBench unit and integration tests were added; the latest full suite is `93 passed`.
- A Docker SWE-bench task-oracle smoke check passed its base/reference-patch sensitivity check: base had 3 expected test failures and 30 passing controls; the reference patch passed all 33. Docker Desktop is healthy, but sandboxed Docker commands cannot open its named pipe and require elevated access. This task does not include an agent trace, so it is not a Chrolo minimization benchmark.

## Still open

1. **Independent task-level reliability benchmark:** AgentFailureBench adds paired synthetic recorded traces, but its tools are not run and its corrected trace is a reference witness, not an independently executed repair. AgentTelemetry measures telemetry detector preservation. SWE-bench verifies a code/test oracle but has no paired agent trace. A real task-level score still needs agent traces plus repeatable environment runners and stable failure signatures.
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

Next resume step: confirm the intended GitHub remote and push local commit `9c3dee6` if this repository should still publish to GitHub. The dataset and generated reports are ignored and should remain uncommitted. Main open product gaps are independently executable task-level agent traces, physical-phone visual QA through an allowed preview path, and provider-specific coverage. Do not retry local browser preview through another surface while the saved policy forbids it.
