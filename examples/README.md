# Examples

- `scenario.json` + `buggy_agent.py`: dependency-preserving reduction without LangGraph.
- `langgraph_input.json` + `langgraph_issue_agent.py`: **real LangGraph** state graph example (needs the `langgraph` extra). Graph deliberately exhibits the stale-cache bug.
- `fake_graph.py`: **test-only protocol shim** to test adapter behavior when LangGraph is not installed. This is *not* an actual LangGraph integration.
- `test_generated_regression.py`: sample M0 regression test; intentionally fails until the bug is fixed.

No example calls a hosted model provider or invokes an external mutating tool.
