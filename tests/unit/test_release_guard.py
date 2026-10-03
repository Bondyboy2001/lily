"""The release's quick checks run before every push, at the same pins as CI."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def _pins(text):
    return set(re.findall(r"uvx ((?:ruff|vulture|mypy)==[\d.]+)", text))


def test_check_script_runs_the_releases_quick_checks_at_its_pins():
    release = _pins((ROOT / ".github/workflows/release.yml").read_text())
    assert release == _pins((ROOT / "scripts/check.sh").read_text())
    assert {p.split("==")[0] for p in release} == {"ruff", "vulture", "mypy"}


def test_pre_push_hook_checks_the_pushed_commit():
    hook = ROOT / ".githooks/pre-push"
    assert hook.stat().st_mode & 0o111 and (ROOT / "scripts/check.sh").stat().st_mode & 0o111
    assert 'scripts/check.sh" "$local_sha"' in hook.read_text()


def _jobs(text):
    """Each job's YAML block in release.yml, by job id."""
    body = text.split("\njobs:\n", 1)[1]
    return dict(re.findall(r"^  ([\w-]+):\n((?:(?:    .*)?\n)+)", body, re.M))


def test_dead_code_fails_the_run_without_holding_back_the_image():
    jobs = _jobs((ROOT / ".github/workflows/release.yml").read_text())
    assert "uvx vulture" in jobs["dead-code"] and "vulture" not in jobs["checks"]
    assert "needs: [checks, build]\n" in jobs["publish"]
    assert "needs: [checks, dead-code, build, publish]\n" in jobs["notify"]
    assert "if: always() && github.ref == 'refs/heads/main'" in jobs["notify"]
