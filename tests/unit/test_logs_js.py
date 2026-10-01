"""Behavioral checks for cps/static/js/logs.js, run under Node with stubbed DOM/$."""

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

CASES = Path(__file__).with_name("logs_js_cases.mjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_logs_js_behaviour():
    result = subprocess.run(["node", str(CASES)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
