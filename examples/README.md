# Examples

- `scenario.json` + `buggy_agent.py`: dependency-preserving reduction without LangGraph.
- `langgraph_input.json` + `langgraph_issue_agent.py`: **real LangGraph** state graph example (needs the `langgraph` extra). Graph deliberately exhibits the stale-cache bug.
- `fake_graph.py`: **test-only protocol shim** to test adapter behavior when LangGraph is not installed. This is *not* an actual LangGraph integration.
- `test_generated_regression.py`: sample M0 regression test; intentionally fails until the bug is fixed.
- `openai_langgraph_input.json` + `openai_langgraph_regression.py`: installed OpenAI Responses SDK and real LangGraph with local HTTP/tool fixtures, exercising the complete capture/replay/minimize/generated-pytest flow.
- `anthropic_langgraph_regression.py`: equivalent installed Anthropic Messages SDK workflow, sharing the same provider-neutral input events in `openai_langgraph_input.json`.
- `openai_chat_langgraph_regression.py`: installed OpenAI Chat Completions SDK workflow with the same input and status-checking fix.

No example calls a hosted model provider or invokes an external mutating tool.

## SDK and graph workflow check

The SDK example records a synthetic ticket lookup. Its application bug selects a closed ticket; `build_fixed_graph` checks the ticket status before selecting it. The fixed graph consumes the same recorded requests/results, so the regression can be tested without making new model or tool calls. The SDK uses `httpx.MockTransport`, a reserved `.invalid` base URL, and a fixture-only key. Its function definition follows the [official OpenAI function-calling documentation](https://developers.openai.com/api/docs/guides/function-calling).

Install the `dev`, `langgraph`, `openai`, and `anthropic` extras for the full provider matrix, then run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_provider_graph_workflow.py
```

The shared checks use installed SDKs and actual compiled StateGraphs. They verify the CLI workflow, preservation of SDK tool-call data, strict rejection of mismatched/missing/unused interactions, and replay of recorded provider/tool error classes. They also reject recordings replayed through the other provider. The generated pytest test runs in a separate process: it fails with the original application bug and passes with the corrected graph. In-process network connections are blocked, replay HTTP/tool callbacks must remain zero, and recording uses only local fixtures. This validates synchronous buffered SDK/framework plumbing; it does not evaluate a hosted model, streaming, async clients, or arbitrary unwrapped side effects.

To produce your own reviewable example artifacts:

```powershell
.\.venv\Scripts\python.exe -m chrolo capture --graph examples.openai_langgraph_regression:build_graph --input examples/openai_langgraph_input.json --record-interactions --output results/provider_capture.json
.\.venv\Scripts\python.exe -m chrolo replay results/provider_capture.json --oracle examples.openai_langgraph_regression:original_failure --require-failure
.\.venv\Scripts\python.exe -m chrolo minimize results/provider_capture.json --oracle examples.openai_langgraph_regression:original_failure --output results/provider_minimal.json --test results/test_provider_regression.py
.\.venv\Scripts\python.exe -m pytest -q results/test_provider_regression.py
```

The last command is expected to fail with `Original failure recurred`. Changing the minimized fixture's `graph` to `examples.openai_langgraph_regression:build_fixed_graph` makes the same generated test pass. The fixed graph still consumes the recorded model/tool interactions.

The 2026-11-10 run retained artifacts in ignored `results/provider_workflow/`: capture, separate broken/fixed minimized fixtures and generated tests, and `report.json`. It reduced 3 events to 1 and pruned 3 interactions to 2, with one fixture SDK HTTP call and two fixture tool calls during recording and zero replay callbacks. Installed versions: OpenAI 2.54.0, LangGraph 1.2.19, HTTPX 0.28.1. These are local fixture results, separate from the real-agent Docker task evidence.

### Anthropic Messages coverage

The Anthropic example uses `input_schema` and `tool_use` blocks matching the [official tool-call documentation](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls). It exercises the actual SDK's message parsing and verifies that the recorded text, tool-use ID, and JSON tool input survive serialization. Local fixture provider errors replay as `anthropic.BadRequestError`; tool failures retain their own exception class. It shares the same status-checking application fix as the OpenAI example.

Use the same commands above with graph/oracle targets under `examples.anthropic_langgraph_regression`, and retain the shared `examples/openai_langgraph_input.json` input. Separate local capture, minimized fixture, and generated-test paths prevent overwriting the OpenAI artifacts.

The 2026-11-10 Anthropic run saved artifacts in ignored `results/anthropic_provider_workflow/`. Three events reduced to one and three interactions to two in seven checks. The generated test exited 1 before the application fix and 0 afterward. Recording used one local HTTP fixture call and two fixture tool calls; replay/minimization/fixed replay used zero callbacks. Installed versions: Anthropic 0.125.0, LangGraph 1.2.19, HTTPX 0.28.1. The shared OpenAI/Anthropic integration matrix passes all 14 tests, including bidirectional provider-substitution rejection. No hosted provider requests or API credentials were used.

### Chat Completions and installed-wheel checks

Chat Completions uses the nested function-tool schema and `message.tool_calls` envelope from the [official OpenAI documentation](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create). The actual installed SDK parses a local fixture response with null message content; the adapter retains the tool-call ID, function name, and JSON arguments without manufacturing output text. Use graph/oracle targets under `examples.openai_chat_langgraph_regression` with the shared input above.

The shared matrix now covers all three supported synchronous provider interfaces in 21 passing tests. Its three cross-interface tests each reject both other interfaces, covering all six directed substitution attempts, including Responses versus Chat Completions within the same SDK. Streaming, async, hosted-model quality, and unwrapped side effects remain outside these checks.

Local Chat Completions artifacts are retained in ignored `results/chat_provider_workflow/`. The 2026-11-10 run reduced three events to one and three calls to two in seven checks; generated pytest failed before the application fix and passed afterward. Recording made one local HTTP-fixture callback and two tool-fixture callbacks, with zero replay callbacks. Versions: OpenAI 2.54.0, LangGraph 1.2.19, HTTPX 0.28.1.

`benchmarks/package_workflow_smoke.py` separately checks a built wheel in a clean environment containing that wheel and pytest:

```powershell
# Use the Python interpreter of the clean wheel-installed environment:
.\results\package_workflow_release\venv\Scripts\python.exe benchmarks/package_workflow_smoke.py --output-dir results/package_workflow_release/check
```

The script rejects an editable/source import, runs the installed CLI from a temporary directory outside the checkout with only a copied standard-library fixture module on its import path, and checks capture, required-failure replay, minimization, and the generated test's expected failure then success. It requires no SDK extras or hosted providers. This is a packaged core-workflow check; the SDK/LangGraph matrix is tested separately. The report and copied artifacts are retained under the output directory. The existing package-smoke CI job now includes this step; the workflow file parses locally, but GitHub CI has not run in this local-only milestone.
