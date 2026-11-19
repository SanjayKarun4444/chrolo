# Chrolo reliability benchmarks

## Repeatable local real-agent regression checks

From the repository root, with the retained local fixtures and Docker running:

```powershell
.\.venv\Scripts\python.exe -m benchmarks.real_regressions
```

The default runs each verified minimal failure, its empty-action control, and its explicitly passing edit-removal control three times. It also runs the real compatible Vermouth comparison once: 19 checks across both repositories. A passing suite verifies Chrolo's replay behavior: the known captured bugs must reproduce and every passing control must pass all configured tests. It does not rerun minimization or imply that the original agent bugs are fixed. The Vermouth check retains the deterministic legacy-tool fixture limitation documented below.

`benchmarks/real_regressions.json` names the two cases and binds their task/oracle definitions, minimized events, and comparison fixture to SHA-256 fingerprints. Source actions must remain an unchanged ordered subset of the retained public trace, and images must have full SHA-256 digests. Changed evaluators or fixtures require review and an explicit manifest update; they cannot silently replace a known case. Raw trace payloads remain ignored under `results/`.

The command validates every fixture before Docker starts. Missing/invalid fixtures produce exit 2 and a failed report; failed or inconclusive checks produce exit 1; every check passing produces exit 0. A new report starts incomplete and unsuccessful, then is updated as each case finishes. Default local report: `results/real-regression-checks.json`. Exception messages, commands, and test payloads are omitted from that summary. Use `--repeat N` and `--output PATH` to adjust the run; comparisons run once.

The ordinary test suite skips these two Docker checks. To run them through pytest, with one repetition per case plus the comparison:

```powershell
$env:CHROLO_RUN_REAL_REGRESSIONS = '1'
.\.venv\Scripts\python.exe -m pytest -q tests/test_real_regression_integration.py
Remove-Item Env:CHROLO_RUN_REAL_REGRESSIONS
```

The checked-in manifest contains local paths and fingerprints only. It does not download datasets or publish fixtures, and the default CI test job does not execute recorded actions. Existing raw inputs/reports are prerequisites; the case-specific input builders and evidence instructions below describe their preparation. The manifest's PyHOCON input is the retained pinned cohort input `results/pyhocon-cohort-1-1097.json`.

Verified on 2026-11-07: the default command passed all 19 checks; both opt-in pytest cases passed; the regular suite passed 194 tests with these two Docker cases skipped by default.

`python -m chrolo benchmark` runs deterministic synthetic cases. It reports reproduction reliability, preserved failure signatures, event reduction and check counts against a naïve single-pass deletion baseline, interaction records pruned when reduced executions no longer make those calls, strict replay of the minimized cassette, replay timing, fixture sizes, cassette consumption, safety rejection cases, and simulated-fix sensitivity.

For a reviewed dataset, run:

```bash
python -m chrolo benchmark --dataset path/to/benchmark_dataset.json --repeat 20
```

The dataset schema is demonstrated in [`../examples/benchmark_dataset.json`](../examples/benchmark_dataset.json), which includes both a stateful issue-selection case and a captured exception failure with a simulated fix. Each case has a stable ID, ordered event fixture, trusted runner or state transition, and a specific failure oracle; optional `fixed_runner` or `fixed_step` functions measure whether the oracle clears after a simulated fix. Dataset code is imported into the current Python process and is not sandboxed. Use only reviewed code and permissioned, sanitized traces. Keep real trace payloads out of source control; use synthetic fixtures where possible.

Compare runs from the same environment. Replay timing and fixture sizes are descriptive measurements; they do not by themselves establish that a minimized trace preserves the intended failure. Review the oracle, repeated reproduction rate, accepted result, and post-fix sensitivity for each case.

## Multi-repository cohort screening

Reviewed 2026-11-18: raw-byte hashes still freeze each artifact, while canonical JSON identities prevent formatting-only duplicates from inflating the selected-case count. Both freeze and execution preflight reject these duplicates. The retained 11-input retrospective selection passes preflight; it has not gained any new real-task execution evidence in this review.

`benchmarks.run_task_cohort` freezes prepared OpenHands inputs before repeated screening. Each input must declare its repository/task and `evaluation.kind`, use a full image digest, contain actions, and specify nonempty disjoint target/control tests. The manifest binds raw input bytes with SHA-256 and records the selection reason. Freeze validates configuration without calling Docker and refuses to overwrite a prior selection:

```powershell
.\.venv\Scripts\python.exe -m benchmarks.run_task_cohort freeze results/case-a.json results/case-b.json --reason "All cases selected by the documented cohort rule" --output results/task-selection.json
```

With prepared images and Docker available, execute that exact selection:

```powershell
.\.venv\Scripts\python.exe -m benchmarks.run_task_cohort run results/task-selection.json --repeat 3 --output results/task-cohort-report.json
```

Every input is validated before any execution. Each repetition uses a fresh runner, checks an empty-action control, then executes the full trace only if all empty targets/controls pass. Reports preserve the full selection and each completed attempt, group results by repository, task, and declared failure mode, and retain pending cases if interrupted. Counts distinguish action-dependent failure, no target failure, rejected empty control, inconclusive execution, and unstable repeated outcomes. An inconsistent repetition never counts as a confirmed failure. Errors expose their class rather than private exception messages. Atomic report updates invalidate old success before fixture validation. Output paths cannot overwrite inputs or the manifest.

Exit 0 means a completed decisive screen (failures or passing traces); 1 means an inconclusive, unstable, or rejected-control case; 2 means invalid configuration/fixtures. A completed screen is not a passing regression suite. This command does not minimize new positive cases or prove fix sensitivity; use `benchmark-openhands --require-action-dependence` and the paired regression gates for that additional evidence.

Local preparation on 2026-11-12: `results/task-cohort-retrospective-selection.json` freezes all nine previously screened PyHOCON inputs and the two previously evaluated Vermouth failure/comparison inputs. All 11 pass preflight without Docker. Their outcomes were already known: this is retrospective infrastructure validation, not new independent benchmark evidence. Separately, `results/cross-repository-candidate-plan.json` retains every one of the historical index's 61 candidates across six repository tasks, including candidates still lacking prepared oracles. That index was filtered for unresolved runs/nonempty patches and is not a random sample. Four additional tasks (JWST, SOSW, CEKit, and PyFilesystem) still need validated task-specific runners/oracles and passing empty controls before broadening reliability claims. No new Docker screening was run for this milestone.

## PyFilesystem candidate oracle (not task-image validated)

`benchmarks/build_pyfilesystem_entrypoint_input.py` defines an independent property for `PyFilesystem__pyfilesystem2-232`: an installed `osfs` opener's load or constructor failure must remain an `EntryPointError` caused by the injected marker, rather than silently selecting a built-in opener. The two parametrized targets fail only when the expected fixture calls occur and a different valid `Opener` is returned. Different exceptions, wrong causes, missing calls, and invalid results become `SKIPPED`/inconclusive. A healthy installed-opener control verifies fresh instances without opening filesystems.

The candidate came from static inspection of the retained fallback patches, not a reproduced container failure. All three indexed PyFilesystem candidates are retained in local `results/pyfilesystem-oracle/preparation.json`, with patch fingerprints. Full traces are unavailable locally, network retrieval failed, the image has no verified digest, and Docker is unavailable. Synthetic registry fixtures validate the oracle's classification behavior; they do not execute real PyFilesystem code or establish a third real-agent regression case.

Write the standalone oracle without executing repository code:

```powershell
.\.venv\Scripts\python.exe -m benchmarks.build_pyfilesystem_entrypoint_input oracle --output results/pyfilesystem-oracle/test_chrolo_entrypoint_errors.py
```

Once a full trajectory, matching task metadata JSON, and verified image digest are available, prepare a replay input:

```powershell
.\.venv\Scripts\python.exe -m benchmarks.build_pyfilesystem_entrypoint_input prepare --trace-json results/pyfilesystem-full-trace.json --task-json results/pyfilesystem-task.json --image "TASK_IMAGE_NAME@sha256:VERIFIED_DIGEST" --output results/pyfilesystem-entrypoint-input.json
```

The placeholder image must be replaced by the metadata's exact image name plus its verified full digest. The builder rejects patches without recorded actions, wrong task/repository/image identities, and oracle-path collisions. It preserves the original actions and evaluation test patch, uses all 30 existing passing controls plus its healthy-plugin control, and removes the issue reference patch because it is not a verified fix for this independent property. The emitted test module is compatible with the task metadata's Python 3.6 environment. Image environment setup and exact control behavior remain unvalidated. Require passing empty controls before full replay, then repeated minimization and edit-removal controls before counting new evidence.

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

## OpenHands isolated action runner

### Action-dependent real-agent packaging regression (2026-11-07)

The public OpenHands run `chatcmpl-233a23562251110a85e8419c0f8791b5` for `chimpler__pyhocon-322` contains a repository-root cleanup command that removes `*.py`, including the existing `setup.py`. An independent packaging oracle checks that `python setup.py --name` still succeeds and returns `pyhocon`; this is a new evaluation check, not the dataset's original nested-substitution issue oracle. The agent actions are unchanged. Controls comprise 207 previously passing dataset tests plus package-import and source-file checks. The untouched image passes the packaging check and all 209 controls.

The full 94-action trace reproduced the packaging failure in 3/3 repeats. Chrolo reduced it to the single recorded cleanup action (`action-0089`) in 13 candidate checks, a 98.94% event reduction. The reduced trace reproduced 3/3 times while all 209 controls passed. Removing that action passed the target and every control in 3/3 paired checks. There were no runner or oracle exceptions. This is one hand-selected, action-dependent real-agent regression, not a corpus-wide reliability estimate or a result for the original issue's correctness. The official issue patch is not represented as a packaging fix; the intervention removes the destructive cleanup action.

The task image digest is `sha256:7bb5310e11201ba1c6397ac7032a1ef100df7b4813bbe43ef701c16700f5d582`. It uses its prepared Conda environment and an explicit `/testbed` Python import path; no image derivative was required. Raw traces, prepared inputs, and the detailed report remain local in ignored `results/`; report: `results/pyhocon-packaging-report.json`.

Rebuild the input from the locally retained public source row and task shard (the builder requires optional `pyarrow`):

```powershell
python -m benchmarks.build_pyhocon_packaging_input --trace-json results/pyhocon-packaging-trace.json --tasks-parquet results/swerebench-filtered.parquet --output results/pyhocon-packaging-input.json
.\.venv\Scripts\python.exe -m chrolo benchmark-openhands results/pyhocon-packaging-input.json --require-action-dependence --repeat 3 --max-checks 100 --output results/pyhocon-packaging-report.json
```

`--require-action-dependence` rejects the task before full replay or minimization unless every target and control test passes on an empty trace. `fail_to_pass` in this prepared runner input selects the independently evaluated regression target; it is not the dataset's original `FAIL_TO_PASS` list. The builder retains the official evaluation test patch and appends the packaging tests. The parser accepts result lines only for configured test IDs, preventing application `ERROR` log lines from becoming spurious test statuses.

Reference-patch sensitivity counts a check as verified only when every configured target and control is `PASSED`. An error, skipped/missing test, failed control, or runner exception is inconclusive rather than a successful fix. `reference_patch_checks` records `passed`, `failure_preserved`, or `inconclusive` per attempt. All attempts remain in the sensitivity denominator, so two successful checks and one environment error report 2/3 rather than 100%.

### Independent repository: legacy-tool compatibility (2026-11-07)

The public Vermouth run `chatcmpl-8d3c6f7f02c17ba871a90fb7bf09d16c` for `marrink-lab__vermouth-martinize-279` replaces the existing DSSP `-i` command protocol with positional/modern command forms. An independent compatibility oracle supplies a deterministic executable accepting the original `-i` protocol and emitting the repository's existing DSSP output fixture. It verifies the parsed result and both savefile modes. This fixture is **not a real legacy DSSP binary** and does not validate protein-analysis accuracy or the original Windows issue fix.

Both compatibility targets and 31 existing DSSP controls pass on the untouched image. Unrelated molecule-naming tests are excluded: one already failed on the untouched image. Only the fixture's explicit unsupported-protocol marker causes a target `FAILED`; other exceptions, output errors, or savefile errors are inconclusive and cannot preserve this failure signature.

All three full-action repeats reproduced the compatibility failure. Minimization reduced 68 actions to two recorded edits (`action-0024`, the tempfile import, and `action-0025`, the command-changing implementation), a 97.06% reduction in 12 checks. Three minimal-trace repeats preserved the failure with all 31 controls passing; three empty controls passed all 33 tests. Removing the command-changing edit passed every test. Removing only the import caused a different execution failure and was rejected as inconclusive. Two candidate runner errors and four oracle-false candidates were rejected during minimization; these are not reproductions. The official issue patch is not claimed as a fix for this independent compatibility property.

The image is pinned at `sha256:741f67ef68d1f287d91f0788385e664d089501ac065b78bdba9dd05014a7cbae`. Local report: `results/vermouth-legacy-report.json`. Rebuild and rerun using optional `pyarrow` for the builder:

```powershell
python -m benchmarks.build_vermouth_legacy_input --trace-json results/vermouth-legacy-trace.json --tasks-parquet results/swerebench-filtered.parquet --output results/vermouth-legacy-input.json
.\.venv\Scripts\python.exe -m chrolo benchmark-openhands results/vermouth-legacy-input.json --require-action-dependence --repeat 3 --max-checks 100 --output results/vermouth-legacy-report.json
```

A real comparison run, `chatcmpl-0069c298d0f1b6ce337cf5dae899deae`, retains a legacy fallback. Its empty control and full 82-action replay both passed the two compatibility targets and all 31 controls, evaluated once each. This checks that the fixture accepts compatible recorded changes rather than rejecting every attempted fix. Comparison report: local `results/vermouth-legacy-comparison-report.json`. Neither run's original issue-resolution label is treated as the compatibility outcome.

`python -m benchmarks.run_packaging_cohort INPUT... --output REPORT` evaluates a preselected list without minimizing passing cases. It writes the complete selection before execution, saves each result as it completes, checks a fresh empty control per case, and keeps four outcomes distinct: action-dependent target failure, no target failure, rejected empty control, and inconclusive replay. Its report contains IDs/counts rather than action payloads or exception messages. Multiple runs of one issue are correlated evidence, not independent tasks or a general reliability estimate.

The first cohort completed on 2026-11-07: all nine indexed unresolved PyHOCON runs with nonempty patches, totaling 776 recorded actions, were selected before cohort replay. Every fresh empty control passed. Full-action replay found the existing packaging regression in one run and no packaging failure in eight; all nine retained 209 passing controls. There were zero rejected empty controls and zero inconclusive replays. Each cohort run was evaluated once; the positive case additionally has the separate three-repeat full/minimal/ablation evidence above. This did not discover an additional positive case. The retained inputs pin the image digest. Local report: `results/pyhocon-packaging-cohort-report.json`; source selection: `results/pyhocon-cohort-selection.json`.

Rerun the retained cohort without changing its selection:

```powershell
$cohortInputs = (Get-Content results/pyhocon-packaging-cohort-report.json -Raw | ConvertFrom-Json).selection | ForEach-Object { Join-Path results $_ }
.\.venv\Scripts\python.exe -m benchmarks.run_packaging_cohort @cohortInputs --output results/pyhocon-packaging-cohort-rerun.json
```

2026-11-07 result: this workflow has now executed on the locally repaired PlasmaFAIR task image described below. Three full-action repeats reproduced all eight expected failures with both controls passing; minimization reduced 88 actions to zero in eight checks; three official-reference-fix checks cleared the failure. The empty-action result means this case demonstrates an unresolved repository bug, rather than an agent-action-dependent failure. A suitable action-dependent benchmark must additionally clear its failure oracle on an empty-action control. Earlier unvalidated/pending notes below are historical.

Task metadata may include an explicit `environment` object mapping environment variable names to string values. These values are passed only to the task container. For images with a prepared Conda environment, set `environment.PATH` to include that environment's `bin` directory before the system paths so the helper, recorded commands, and test runner use the same installed dependencies. The PlasmaFAIR task image has pytest in `/opt/conda/envs/testbed/bin`, rather than its default base environment. Out-of-workspace editor requests are rejected as tool errors and later recorded actions continue.

The input builder retains the dataset's `test_patch`, which the runner applies after recorded actions and before evaluating tests. This supplies the issue's evaluation tests rather than the image's older tests. On 2026-11-07, the PlasmaFAIR image required a local derivative adding `/sdf-xarray -> /testbed` because its editable installation refers to the former path. The upstream image digest is `sha256:a5c3f9c64a4f45fdc63c8cbd75d4f8dda91f8daf49c37d951b6c6d6e897ef14e`; the local Dockerfile is retained in ignored `results/Dockerfile.openhands`. With that repair, the prepared environment, and the evaluation patch, empty-action execution produced eight expected failures and two passing controls; the official reference patch passed all ten. This validates task setup, separately from real-agent action replay and minimization.

`chrolo.openhands.extract_actions` converts a row from the OpenHands trajectory corpus into ordered Chrolo events. `OpenHandsDockerRunner` starts a new container for each candidate with networking disabled, no host bind mounts, dropped capabilities, no-new-privileges, and CPU, memory, PID, per-action, test, and overall time limits. It executes the recorded Bash commands and the sample's `view`, `create`, and `str_replace` editor operations, then reports test statuses from the configured pytest command. A runner timeout removes its named container. Only pytest `FAILED` counts toward the original failure signature; `ERROR`, `SKIPPED`, and `PASSED` do not. The benchmark requires every configured `FAIL_TO_PASS` test to fail on baseline and every `PASS_TO_PASS` control to pass. Its paired `failure_oracle` then requires that exact failure set and all passing controls for each candidate.

The CLI input joiner retains the task's official patch. The benchmark applies it in a separate fresh container before replaying the minimized trace and repeats the exact failure-oracle check, reporting fix sensitivity. This is an intervention against the original task oracle, rather than a simulated corrected trace. The runner and orchestration have unit coverage with mocked Docker/runner processes and a syntax check for the in-container helper. The editor emulates `create` without overwriting, unique-only `str_replace`, line-based `insert`, and per-file `undo_edit`; `view` is read-only. These semantics follow the [OpenHands `str_replace_editor` specification](https://github.com/togethercomputer/OpenHands/blob/main/openhands/agenthub/codeact_agent/tools/str_replace_editor.py) and are tested by running the in-container helper against a temporary fixture. The builder maps recorded `/workspace/<repo>` paths to the task image's `/testbed`, including absolute paths inside shell commands. Historical pre-validation status: Docker execution was initially deferred. Subsequent local runs validated the repaired PlasmaFAIR image and the two independent action-dependent cases documented above. PlasmaFAIR reduces to zero actions and cannot establish an agent-caused regression; use the current case-specific controls and regression gate when assessing evidence.

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
