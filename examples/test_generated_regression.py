"""Generated regression: fails until the underlying agent bug is fixed."""
import json
from pathlib import Path
from chrolo.core import parse_events
from chrolo.cli import resolve

def test_regression():
    fixture = (Path(__file__).parent / 'minimal.json').resolve()
    events = parse_events(json.loads(fixture.read_text())["events"])
    actual = resolve('examples.buggy_agent:run')(events)
    assert not resolve('examples.buggy_agent:is_failure')(actual), "Original failure recurred"
