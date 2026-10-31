# Contributing to Chrolo

Thank you for helping make failed agent runs reproducible.

1. Use a small, synthetic or sanitized scenario; do not commit raw production agent traces, API keys, or personal data.
2. Open an issue describing a failure that Chrolo cannot minimize or preserve reliably, and include the failure oracle and expected outcome.
3. Add regression coverage for changes to minimization, capture, and replay behavior.
4. Run `python -m pytest -q` and, if available, the optional LangGraph integration test.
5. Prefer precise, executable failure signatures rather than model-judged "looks wrong" assertions.

Initial PR priorities: model/tool mock boundary capture; reference graph adapters; outcome-preservation tests; redaction and safe trace imports; benchmarks versus delta-debugging baselines.
