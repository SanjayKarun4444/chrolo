# Examples

- `scenario.json` + `buggy_agent.py`: dependency-preserving reduction without LangGraph.
- `langgraph_input.json` + `langgraph_issue_agent.py`: **real LangGraph** state graph example (needs the `langgraph` extra). Graph deliberately exhibits the stale-cache bug.
- `fake_graph.py`: **test-only protocol shim** to test adapter behavior when LangGraph is not installed. This is *not* an actual LangGraph integration.
- `test_generated_regression.py`: sample M0 regression test; intentionally fails until the bug is fixed.
- `openai_langgraph_input.json` + `openai_langgraph_regression.py`: installed OpenAI Responses SDK and real LangGraph with local HTTP/tool fixtures, exercising the complete capture/replay/minimize/generated-pytest flow.

No example calls a hosted model provider or invokes an external mutating tool.

## SDK and graph workflow check

The SDK example records a synthetic ticket lookup. Its application bug selects a closed ticket; `build_fixed_graph` checks the ticket status before selecting it. The fixed graph consumes the same recorded requests/results, so the regression can be tested without making new model or tool calls. The SDK uses `httpx.MockTransport`, a reserved `.invalid` base URL, and a fixture-only key. Its function definition follows the [official OpenAI function-calling documentation](https://developers.openai.com/api/docs/guides/function-calling).

Install the `dev`, `langgraph`, and `openai` extras, then run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_provider_graph_workflow.py
```

The six checks use the installed SDK and actual compiled StateGraph. They verify the CLI workflow, preservation of SDK function-call data, strict rejection of mismatched/missing/unused interactions, and replay of recorded provider/tool error classes. The generated pytest test runs in a separate process: it fails with the original application bug and passes with the corrected graph. In-process network connections are blocked, replay HTTP/tool callbacks must remain zero, and recording uses only local fixtures. This validates synchronous buffered SDK/framework plumbing; it does not evaluate a hosted model, streaming, async clients, or arbitrary unwrapped side effects.

To produce your own reviewable example artifacts:

```powershell
.\.venv\Scripts\python.exe -m chrolo capture --graph examples.openai_langgraph_regression:build_graph --input examples/openai_langgraph_input.json --record-interactions --output results/provider_capture.json
.\.venv\Scripts\python.exe -m chrolo replay results/provider_capture.json --oracle examples.openai_langgraph_regression:original_failure --require-failure
.\.venv\Scripts\python.exe -m chrolo minimize results/provider_capture.json --oracle examples.openai_langgraph_regression:original_failure --output results/provider_minimal.json --test results/test_provider_regression.py
.\.venv\Scripts\python.exe -m pytest -q results/test_provider_regression.py
```

The last command is expected to fail with `Original failure recurred`. Changing the minimized fixture's `graph` to `examples.openai_langgraph_regression:build_fixed_graph` makes the same generated test pass. The fixed graph still consumes the recorded model/tool interactions.

The 2026-11-10 run retained artifacts in ignored `results/provider_workflow/`: capture, separate broken/fixed minimized fixtures and generated tests, and `report.json`. It reduced 3 events to 1 and pruned 3 interactions to 2, with one fixture SDK HTTP call and two fixture tool calls during recording and zero replay callbacks. Installed versions: OpenAI 2.54.0, LangGraph 1.2.19, HTTPX 0.28.1. These are local fixture results, separate from the real-agent Docker task evidence.
