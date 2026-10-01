import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

CASES = Path(__file__).with_name("progress_sync_cases.mjs")
REFRESH_CASES = Path(__file__).with_name("lily_refresh_cases.mjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_progress_sync_behaviour():
    result = subprocess.run(["node", str(CASES)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_refresh_toast_behaviour():
    result = subprocess.run(["node", str(REFRESH_CASES)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
