"""Explicit opt-in checks using retained local traces and isolated Docker tasks."""
import os

import pytest

from benchmarks.real_regressions import DEFAULT_MANIFEST, load_case, read_json, run_case

pytestmark = pytest.mark.skipif(
    os.environ.get("CHROLO_RUN_REAL_REGRESSIONS") != "1",
    reason="Set CHROLO_RUN_REAL_REGRESSIONS=1 to run retained local fixtures in Docker",
)


@pytest.mark.parametrize("entry", read_json(DEFAULT_MANIFEST)["cases"], ids=lambda entry: entry["name"])
def test_real_agent_replay_regression(entry):
    try:
        case = load_case(entry)
    except (OSError, ValueError, KeyError, TypeError):
        pytest.fail("Local regression fixtures are missing or invalid; see benchmarks/README.md", pytrace=False)
    result = run_case(case, repeat=1)
    assert result["passed"], result["checks"]
