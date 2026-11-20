# Changelog

## 0.2.0a1 — experimental alpha candidate

This is the first explicitly marked public-alpha version. Check the repository's
releases page for availability; earlier `0.2.0` builds were local development
artifacts. `0.2.0a1` is not an upgrade over an installed
`0.2.0` under Python version ordering; use a fresh environment or explicitly
reinstall the candidate when moving from those development builds.

### Included

- Dependency-preserving event reduction with a supplied executable failure
  oracle, candidate-rejection reporting, and pytest generation.
- Graph-compatible capture and replay, strict JSON interaction cassettes, and
  `chrolo prepare` for a verified local regression bundle.
- Buffered synchronous OpenAI Responses, OpenAI Chat Completions, Anthropic
  Messages, and optional LangChain wrappers.
- Trace inspection, heuristic secret scanning, non-replayable redaction and
  filtering, and a local Trace Desk.
- Experimental stateful dependency inference through `TrackedState`.
- Network-isolated OpenHands task replay and separate application-source repair
  after recorded edits.
- Repeated regression gates for PyHOCON, Vermouth, and PyFilesystem, including
  negative PyFilesystem screening results and explicit fixture limitations.

### Validation

The candidate passed 306 tests against a fresh installed wheel, with three
opt-in Docker cases skipped; all 28 real-action gate checks passed separately. Installed-wheel
checks cover the core workflow, 21 SDK/LangGraph cases, 44 runner contracts, and
the real PyFilesystem regression before/after source repair. Candidate-specific
build and check results are recorded in the release runbook and local reports.

### Scope

Experimental APIs and trace formats may change. Replay covers explicitly
wrapped calls; graph code can still execute unwrapped effects. Async, streaming,
arbitrary agent capture, and SDK response reconstruction are unsupported.
Selected task evidence does not establish corpus-wide reliability or an
efficiency advantage over a naive deletion baseline. Raw real-agent fixtures
are not included in the distribution.
