# Chrolo

**Turn reproducible agent failures into small, executable regression tests.**

Chrolo captures the inputs and explicitly wrapped model/tool interactions behind an agent failure, replays them offline, and removes unnecessary events while preserving the same failure. The result is a reviewable fixture and a pytest test that exercises your application code.

**Python 3.10+ · MIT licensed · Experimental alpha**

Alpha version: **0.2.0a1**. See the [release notes](docs/releases/0.2.0a1.md) and [changelog](CHANGELOG.md). Source installation is documented below; package-registry publication is a separate step.

[Quickstart](#quickstart) · [Documentation](#documentation) · [Examples](examples/README.md) · [Contributing](CONTRIBUTING.md)

## Why Chrolo?

A failed agent run can contain dozens of steps, external responses, and unrelated actions. Chrolo helps turn that history into something you can debug and keep in your test suite:

- **Reproduce the same failure.** Define a precise Python failure predicate and verify it during replay and reduction.
- **Reduce the case.** Remove input events while respecting declared dependencies and retaining the failure signature.
- **Replay recorded interactions.** Explicitly wrapped model/tool calls use saved responses with strict request matching and no live fallback.
- **Generate a regression test.** Run the test against your current implementation before and after a source fix.
- **Review the evidence.** Inspect deletion history, rejected candidates, check counts, and the scope of the minimality result.

```text
Captured failure → Verified replay → Reduced fixture → Regression test
```

Chrolo is useful when you can expose a deterministic runner or an instrumented graph and identify the specific failure you want to preserve. It remains an experimental tool with explicit integration boundaries.

## Installation

Install from a checkout of this repository. The core runtime uses the Python standard library; optional extras add testing, framework, and provider dependencies.

```bash
python -m venv .venv
```

Activate the environment:

```bash
# macOS / Linux
source .venv/bin/activate
```

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

Then install Chrolo and pytest:

```bash
python -m pip install -e '.[dev]'
python -m chrolo --help
python -m chrolo --version
```

For the supported SDK and LangGraph examples:

```bash
python -m pip install -e '.[dev,langgraph,openai,anthropic]'
```

The distribution is currently named `chrolo-starter`; the import and CLI names are `chrolo`. These instructions use the local checkout and do not assume a published package release.

## Quickstart

Run these commands from the repository root after installation. The included ticket agent has an intentional bug: it selects a closed ticket. Its model/tool responses are deterministic local fixtures, so this example needs no API key, hosted provider, or LangGraph installation.

### 1. Capture the failure

```bash
python -m chrolo capture --graph examples.pilot_ticket_agent:build_graph --input examples/pilot_ticket_input.json --record-interactions --output results/quickstart/capture.json
```

Recording executes the supplied callbacks. In this example they return fixture data.

### 2. Prepare the regression

```bash
python -m chrolo prepare results/quickstart/capture.json --oracle examples.pilot_ticket_agent:original_failure --output-dir results/quickstart/repro
```

`prepare` verifies the original failure with strict replay, minimizes the events and consumed interactions, and writes a local regression bundle. This example reduces **three events to one** and **three interactions to two**.

| Generated file | Purpose |
| --- | --- |
| `minimal.json` | Reduced events, retained interactions, and reduction history |
| `test_regression.py` | Pytest regression importing the current application and oracle |
| `summary.json` | Completion status, counts, verification, and minimality flags |
| `README.md` | Instructions for rerunning and reviewing the case |
| `.gitignore` | Keeps the raw-data bundle local by default |

Choose a new output directory for another run. Existing bundle directories are never overwritten; an interrupted preparation can leave an explicitly incomplete summary.

### 3. Run the test, fix the application, rerun

```bash
python -m pytest -q results/quickstart/repro/test_regression.py
```

**The first run should fail with `Original failure recurred`.** In [the example agent](examples/pilot_ticket_agent.py), change `choose_ticket` to check the ticket status:

```python
def choose_ticket(ticket):
    return ticket["status"] == "open"
```

Run the same pytest command again. It should pass with the capture and minimized fixture unchanged. This fix preserves the recorded boundary calls. A request mismatch or unrelated exception is a different failure and does not demonstrate a successful repair.

For a walkthrough in your own project directory, see the [pilot workflow guide](docs/pilot-workflow.md).

## Connect your own workflow

Chrolo needs three pieces:

1. **An executable runner.** Use a graph factory returning an object with `invoke(state)`, or a generic `run(events)` function.
2. **Recorded inputs and boundaries.** Supply JSON events and route relevant external calls through Chrolo wrappers.
3. **A failure oracle.** Provide an importable `module:function` that returns `True` only for the specific wrong outcome.

A graph factory accepting `boundaries` can wrap an external call like this:

```python
receipt = boundaries.call(
    "tool",
    "payments.lookup_receipt",
    {"invoice_id": "invoice-8"},
    lambda: payment_client.lookup_receipt("invoice-8"),
    context={"event_id": event["id"]},
)
```

The callback runs during recording. During replay, Chrolo checks the boundary, target, request, and context, then returns the saved result. Stable per-event context helps distinguish repeated identical requests during minimization.

For a deterministic runner without a graph or cassette:

```bash
python -m chrolo minimize examples/scenario.json --run examples.buggy_agent:run --oracle examples.buggy_agent:is_failure --output results/minimal.json --test results/test_regression.py
```

See the [replay and integration guide](docs/replay-guide.md) for capture modes, wrapper APIs, provenance, exception recording, and stateful dependency inference.

## Supported integrations

| Integration | Scope |
| --- | --- |
| Generic Python runner | Deterministic event minimization and pytest generation |
| Graph-compatible runner / LangGraph | Fresh graph invocation from initial state and retained input events |
| Explicit model/tool boundaries | Versioned JSON cassettes and strict synchronous replay |
| OpenAI Responses | Buffered synchronous SDK calls via `BoundaryOpenAIResponses` |
| OpenAI Chat Completions | Buffered synchronous SDK calls via `BoundaryOpenAIChatCompletions` |
| Anthropic Messages | Buffered synchronous SDK calls via `BoundaryAnthropicMessages` |
| LangChain chat models | Optional buffered `.invoke()` wrapper via `BoundaryChatModel` |
| Recorded OpenHands actions | Separate Docker task replay and minimization with executable test oracles |

Install extras as needed: `langgraph`, `langchain`, `openai`, and `anthropic`. Provider wrappers return Chrolo's normalized result objects, rather than reconstructing SDK-typed responses. Async clients, streaming, and arbitrary unwrapped side effects are outside current coverage. The three provider SDK/LangGraph workflows have end-to-end checks; coverage differs for other integrations.

## Command reference

| Command | Purpose |
| --- | --- |
| `capture` | Execute an instrumented graph and save its inputs and optional interactions |
| `prepare` | Verify a cassette-backed capture and create a minimized regression bundle |
| `replay` | Rerun a graph capture; use `--oracle` and `--require-failure` to assert reproduction |
| `minimize` | Reduce graph inputs or generic event fixtures and generate pytest |
| `infer-dependencies` | Infer and verify top-level state dependencies through `TrackedState` |
| `inspect` | Show trace metadata without payloads |
| `scan` | Report likely secret paths using heuristic detection |
| `redact` | Create a non-replayable review copy with selected values removed |
| `serve` | Open the local Trace Desk for inspection, scanning, and sanitization |
| `benchmark` | Run synthetic reliability cases or a reviewed custom dataset |
| `benchmark-openhands` | Replay and minimize recorded actions in isolated Docker task containers |

Use `python -m chrolo <command> --help` for arguments. The `chrolo` console command is also available after installation.

## Validation and evidence

The latest local validation checkpoint is **2026-11-19**:

- **306 tests passed**, with three opt-in Docker cases skipped in the standard suite.
- **28 real-action regression-gate checks passed** across three repository tasks, including repeated failure replay, passing empty controls, and paired action-removal controls.
- **21 SDK/LangGraph integration checks** cover the supported OpenAI and Anthropic interfaces; they also passed against an installed wheel outside the checkout. Provider responses use local HTTP fixtures.

| Real-action case | Original → reduced actions | Oracle scope |
| --- | ---: | --- |
| PyHOCON | 94 → 1 | Existing package metadata command remains usable |
| Vermouth | 68 → 2 | Legacy DSSP compatibility with a deterministic executable fixture |
| PyFilesystem | 59 → 4 | Installed-plugin errors retain their cause, using deterministic plugin fixtures |

The PyFilesystem screen retained all three preselected candidates: two negative results and one reproducible failure, each screened three times. These selected cases establish task-specific evidence, not corpus-wide reliability or measured developer time savings. Minimality means 1-minimal under tested cascade deletions within the check budget; it does not mean globally smallest. Existing synthetic benchmarks do not establish an efficiency advantage over a naive deletion baseline.

Full methods, controls, negative outcomes, and limitations are in the [benchmark guide](benchmarks/README.md) and [PyFilesystem evidence](docs/pyfilesystem-evidence.md). Fresh installed-wheel checks also pass the core workflow and all 44 Docker-runner contract checks outside the checkout. Hosted CI passed the preceding runtime milestone; the updated alpha-candidate workflow awaits an authorized push. An external developer trial remains open.

## Replay boundaries and trace privacy

**Wrapped calls replay offline; application code still executes.** Chrolo does not intercept arbitrary network, database, filesystem, or checkpoint activity. Input-only graph captures require `--allow-live-side-effects` to rerun. Docker action replay is a separate path with fresh containers, networking disabled, resource limits, and no host bind mounts.

**Traces can contain sensitive data.** Review captures before sharing. `scan` is heuristic, and a clean scan is not a security guarantee. Redacted and filtered copies are marked non-replayable because removed data can affect matching or execution. Keep the original replay fixture private and promote reviewed artifacts deliberately.

**Imported code must be trusted.** Factories, runners, oracles, and generated tests execute Python. Strict cassette matching rejects changed requests, missing calls, and unused records; it never silently records a replacement response. A fix that changes boundary calls needs an explicit review of the recording and test design.

For security reports, see [SECURITY.md](SECURITY.md).

## Development

```bash
python -m pip install -e '.[dev,langgraph,openai,anthropic]'
python -m pytest -q
```

Optional integrations may skip when their dependencies are absent. Real-action Docker gates require explicit opt-in, retained local fixtures, and the pinned task images; see [benchmark setup](benchmarks/README.md#repeatable-local-real-agent-regression-checks).

The repository includes a [CI workflow](.github/workflows/ci.yml) for Python 3.10 and 3.12 on Linux and Windows, source/wheel metadata and content checks, and packaged SDK workflows. See [CONTRIBUTING.md](CONTRIBUTING.md) for development practices and useful areas to contribute.

## Documentation

| Guide | Contents |
| --- | --- |
| [Pilot workflow](docs/pilot-workflow.md) | Capture-to-regression walkthrough in a project directory |
| [Replay and integration](docs/replay-guide.md) | Boundary APIs, capture modes, provenance, and advanced workflows |
| [Examples](examples/README.md) | Runnable core, framework, and provider examples |
| [Benchmarks](benchmarks/README.md) | Evaluation methods, dataset preparation, and real-action gates |
| [PyFilesystem evidence](docs/pyfilesystem-evidence.md) | Three-candidate screen, minimal case, and source-fix verification |
| [Replay privacy review](docs/replay-privacy-review.md) | Privacy behavior and verified replay diagnostics |
| [Roadmap](docs/roadmap.md) | Current priorities and implementation scope |
| [Launch runbook](docs/launch-runbook.md) | Candidate validation, release assets, and separately authorized publication steps |

## Project status

Chrolo is an experimental alpha. Near-term work focuses on a real developer pilot, broader independent task oracles, and public-alpha release preparation. Hosted CI, public distribution, and production readiness are separate milestones; see the [release-readiness review](docs/release-readiness.md).

## License

Chrolo is available under the [MIT License](LICENSE). Third-party benchmark datasets and task repositories retain their own licenses; raw benchmark corpora are not distributed with this repository.
