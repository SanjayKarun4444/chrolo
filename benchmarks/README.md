# Chrolo reliability benchmarks

`python -m chrolo benchmark` runs deterministic synthetic cases. It reports reproduction reliability, preserved failure signatures, event reduction and check counts against a naïve single-pass deletion baseline, interaction records pruned when reduced executions no longer make those calls, strict replay of the minimized cassette, replay timing, fixture sizes, cassette consumption, safety rejection cases, and simulated-fix sensitivity.

For a reviewed dataset, run:

```bash
python -m chrolo benchmark --dataset path/to/benchmark_dataset.json --repeat 20
```

The dataset schema is demonstrated in [`../examples/benchmark_dataset.json`](../examples/benchmark_dataset.json), which includes both a stateful issue-selection case and a captured exception failure with a simulated fix. Each case has a stable ID, ordered event fixture, trusted runner or state transition, and a specific failure oracle; optional `fixed_runner` or `fixed_step` functions measure whether the oracle clears after a simulated fix. Dataset code is imported into the current Python process and is not sandboxed. Use only reviewed code and permissioned, sanitized traces. Keep real trace payloads out of source control; use synthetic fixtures where possible.

Compare runs from the same environment. Replay timing and fixture sizes are descriptive measurements; they do not by themselves establish that a minimized trace preserves the intended failure. Review the oracle, repeated reproduction rate, accepted result, and post-fix sensitivity for each case.

## Executable synthetic task-oracle cases

Run `python -m chrolo benchmark --dataset examples/task_oracle_dataset.json --repeat 20` for three reproducible task failures backed by an in-memory tool environment. Unlike the AgentFailureBench trace-differential adapter, this runner executes selected tool calls against a fresh mutable task state on every candidate and checks the resulting task state: wrong ticket assignment, duplicate invoice payment, or deletion of a protected file. Tool outputs can feed later tool arguments through explicit event references, and those dependencies constrain minimization. Paired fixed runners correct or skip the faulty action and should clear the original signature.

The 20-repeat reference run reproduced and preserved all three exact task-state signatures, reached 1-minimal results for all three cases, and cleared all three signatures under their fixed runners. Mean event shrink was 50.00%, matching the naïve baseline; Chrolo required 15 checks per case versus 6 for the baseline. This is executable synthetic task-level evidence for the runner/oracle contract, not evidence from production-agent traces or external services. Reports mark this fixture `synthetic_only: true`.

`MAD_full_dataset.json` (the MAD/MAST corpus) is present locally as an observational corpus: it contains 1,642 execution traces and 14 binary failure-mode annotations. Its annotations are LLM-produced, and each row contains a trace string rather than an executable runner or failure oracle. It therefore cannot be passed to `chrolo benchmark --dataset` or used to claim minimizer reliability. See the [upstream dataset card](https://huggingface.co/datasets/mcemri/MAST-Data) for the schema, license, and annotation provenance. The corpus file is gitignored because of its size.

## AgentTelemetry fault-trace integration

Chrolo includes an adapter for the deterministic, mock-backed fault-injection generator in the [AgentTelemetry repository](https://github.com/Krishnachaitanyakc/AgentTelemetry). It regenerates every source row, verifies that the live detector agrees with the stored label, reports detector-level classification metrics, and uses reproducible positive detections as Chrolo minimization cases. On control traces, any of the fault detectors firing counts as a false positive; the regenerated list is checked against AgentTelemetry's `oracle_false_fires`. Benchmark reports include allowlisted provenance fields. This measures how well minimization preserves a telemetry fault signal; it does **not** measure whether the original multi-agent task still fails.

With network access, clone AgentTelemetry and install its locked optional framework dependencies into its own virtual environment. Then regenerate and run the bridge in PowerShell from the Chrolo repository root:

```powershell
git clone --depth 1 https://github.com/Krishnachaitanyakc/AgentTelemetry AgentTelemetry
Set-Location AgentTelemetry
uv sync --frozen --extra all --no-default-groups --python 3.13
$env:PYTHONPATH = "src;."
.\.venv\Scripts\python.exe src\agenttelemetry_inspect\generate_dataset.py --output ..\results\agenttelemetry_traces.jsonl --quiet
Set-Location ..
$env:PYTHONPATH = "src;AgentTelemetry/src;AgentTelemetry"
$env:AGENTTELEMETRY_ROOT = "AgentTelemetry"
.\AgentTelemetry\.venv\Scripts\python.exe benchmarks\build_agenttelemetry_dataset.py --agenttelemetry-root AgentTelemetry --input results\agenttelemetry_traces.jsonl --output results\agenttelemetry_chrolo_dataset.json
.\AgentTelemetry\.venv\Scripts\python.exe -m chrolo benchmark --dataset results\agenttelemetry_chrolo_dataset.json --repeat 20
```

The generator is invoked by file path so the optional Inspect eval package is not needed; `python -m agenttelemetry_inspect.generate_dataset` imports that package first and requires AgentTelemetry's `inspect` extra.

At AgentTelemetry commit `33feec2d434ed4a77862fd1698f09bc23926df1b`, the full 170-row detector evaluation had 78 true positives, 62 false negatives, 0 false positives, and 30 true negatives (55.71% recall and 100% specificity on this generated set). Only the 78 true positives become Chrolo minimization cases. For regression sensitivity, each minimized case is rerun with the same mock framework/task and the fault injector disabled; all 78 controls clear the original detector signature. Across the faulted cases and 20 repetitions (1,560/1,560 full-trace replays reproduced), Chrolo preserved every included detector signature, reached a mean 74.23% event reduction, and marked every result 1-minimal under tested cascade deletions. The naïve baseline had the same mean reduction (74.23%) and fewer mean candidate checks (14.77 versus Chrolo's 21.33). This is detector sensitivity to a fault-disabled control, not a source-code patch or production task fix; results are descriptive for a mock-backed harness. `results/` is gitignored, and the external AgentTelemetry checkout is ignored by `.gitignore`.

## SWE-bench task-oracle smoke check

The local SWE-bench Multimodal task shards contain runnable test scripts and Docker images, but do not contain agent interaction traces. Docker access in this environment requires an approved elevated command because the sandbox cannot open Docker's Windows named pipe. The Docker Desktop engine was verified as running, then task `Automattic__wp-calypso-21409` was exercised in its published image (`swebench/sweb.eval.x86_64.automattic_1776_wp-calypso-21409:latest`). The base checkout produced the expected 3 failing `FAIL_TO_PASS` tests while all 30 `PASS_TO_PASS` tests passed; applying the dataset's reference patch made all 33 tests pass. This verifies the task runner and the test oracle's fix sensitivity. The supplied shell script exits zero even when Jest reports failing tests, so a consumer must parse the test summary or use the dataset's log parser rather than trust the process exit code alone. This is not a Chrolo trace-minimization score: the dataset has code patches and test expectations, not a recorded agent execution to replay and minimize.

Real-world minimization scores still require runners that can deterministically re-execute selected real traces with stable task-level failure oracles.

## OpenHands isolated action runner (not yet validated on a task image)

`chrolo.openhands.extract_actions` converts a row from the OpenHands trajectory corpus into ordered Chrolo events. `OpenHandsDockerRunner` starts a new container for each candidate with networking disabled, no host bind mounts, dropped capabilities, no-new-privileges, and CPU, memory, PID, per-action, test, and overall time limits. It executes the recorded Bash commands and the sample's `view`, `create`, and `str_replace` editor operations, then reports test statuses from the configured pytest command. A runner timeout removes its named container. The paired `failure_oracle` requires the same observed set of failing `FAIL_TO_PASS` tests and passing `PASS_TO_PASS` controls.

The CLI input joiner retains the task's official patch. The benchmark applies it in a separate fresh container before replaying the minimized trace and repeats the exact failure-oracle check, reporting fix sensitivity. This is an intervention against the original task oracle, rather than a simulated corrected trace. The runner and orchestration have unit coverage with mocked Docker/runner processes and a syntax check for the in-container helper. The workflow has **not** been run against the SWE-rebench image. `insert` and `undo_edit` editor calls are rejected explicitly. Keep real-agent reliability claims open until the published task image validates the replay and the reference-patch intervention. Docker was left stopped at the user's request.

## AgentFailureBench paired trace-difference integration

The [AgentFailureBench dataset](https://huggingface.co/datasets/fahadhafeezofficial/agent-failure-bench) supplies synthetic failed and corrected tool trajectories. Chrolo's builder includes only records with an exact tool-call or tool-output difference, then treats the recorded difference as a witness. It replays those recorded observations and does not execute tools, recreate an environment, or establish task success/failure independently. The upstream dataset describes its traces as procedurally generated and documents the corresponding limitations in [`LIMITATIONS.md`](https://huggingface.co/datasets/fahadhafeezofficial/agent-failure-bench/blob/main/LIMITATIONS.md).

For the held-out `test` split at revision `93257fb` (CC BY 4.0), 1,000 records produced 562 included trace-difference cases, 283 clean records skipped, and 155 labeled failures skipped because tool calls/outputs did not differ from the paired corrected trace. Run the builder and benchmark with:

```powershell
Invoke-WebRequest -Uri "https://huggingface.co/datasets/fahadhafeezofficial/agent-failure-bench/resolve/93257fb/dataset/test.jsonl?download=true" -OutFile results/agent_failure_bench_test.jsonl
python benchmarks/build_agentfailurebench_dataset.py --input results/agent_failure_bench_test.jsonl --output results/agentfailurebench_chrolo_dataset.json --source-revision 93257fb --split test
$env:PYTHONPATH = "src"
@'
import json
from pathlib import Path
from chrolo.benchmark import run_dataset
dataset = json.loads(Path("results/agentfailurebench_chrolo_dataset.json").read_text(encoding="utf-8"))
report = run_dataset(dataset, repeat=20)
Path("results/agentfailurebench_chrolo_report_repeat20.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report["aggregate"], indent=2))
'@ | .\.venv\Scripts\python.exe -
```

Observed on this run: all 562 cases reproduced on all 20 repetitions, all minimized outputs preserved the exact recorded witness, all 562 were 1-minimal under tested deletion cascades, and the supplied corrected-trace intervention cleared the witness in all cases. Mean event shrink was 16.00% (naïve single-pass baseline: 34.09%); Chrolo used 10.60 checks per case (baseline: 4.20). This is a trace-differential minimization benchmark over synthetic paired recordings, not an independent task-level or production-agent benchmark. Both generated dataset and report are ignored under `results/`.
