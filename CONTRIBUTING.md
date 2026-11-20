# Contributing to Chrolo

Chrolo aims to make reproducible agent failures easier to isolate and retain as regression tests. Contributions are welcome around replay correctness, developer workflows, integration coverage, and independently verifiable task oracles.

## Development setup

Use Python 3.10 or newer in a virtual environment. From the repository root:

```bash
python -m pip install -e '.[dev,langgraph,openai,anthropic]'
python -m pytest -q
```

Add the `langchain` extra when working on that adapter. Optional integrations can skip when dependencies are absent. The real-action Docker tests skip by default: they need retained local fixtures, pinned images, and explicit opt-in. See [benchmark setup](benchmarks/README.md).

## Report a bug

Open an issue with the Chrolo revision, Python and relevant dependency versions, command used, expected outcome, and a minimal synthetic reproduction. For replay failures, distinguish a request mismatch, an unrelated exception, and recurrence of the original failure. Include a precise failure oracle when possible.

Keep raw production traces, API keys, personal data, and confidential responses out of issues and commits. A clean heuristic scan is not proof that a trace is safe. Report security vulnerabilities through the process in [SECURITY.md](SECURITY.md).

## Submit a change

1. Keep the change focused and explain the concrete problem it fixes.
2. Preserve strict replay behavior and compatibility, or document any necessary format or behavior change explicitly.
3. Add meaningful regression coverage for changed capture, replay, matching, minimization, or privacy behavior. Documentation changes should have working commands and links.
4. Run the checks relevant to your change. For core behavior, run the full test suite and report optional skips separately.
5. Describe the resulting behavior, validation, and material limitations in the pull request.

Generated regression tests intentionally fail while their demonstration bug remains. The standard suite uses `tests/`; running pytest against an intentionally broken generated example is not a suite failure to hide.

## Correctness expectations

A reduction must preserve the supplied failure signature. Different errors, replay mismatches, missing results, skipped tests, and failed controls must not count as reproduction or a verified fix. Replayed boundaries must not fall back to live calls.

Benchmark contributions should record their selection rule, preserve negative and inconclusive cases, require passing empty-action controls for action-dependent claims, pin task images, and separate synthetic fixtures from real execution evidence. Report reduction and execution costs together. Raw corpora and recorded actions remain local unless separately reviewed and licensed for distribution.

## Useful areas to contribute

- Developer onboarding and capture-to-regression usability.
- Installed-package and supported-platform checks.
- Precise task oracles in additional independent repositories.
- Strict matching, error classification, and privacy regressions.
- Explicitly scoped framework and provider integrations.

Check the [roadmap](docs/roadmap.md) before starting a broad integration. Async, streaming, and arbitrary unwrapped side effects remain outside current support.

## License

Contributions are made under the project's [MIT License](LICENSE). Identify the source and applicable license of third-party code, datasets, and fixtures; do not assume the project license applies to upstream material.
