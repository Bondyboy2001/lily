"""The release's quick checks: one script runs them for CI and the pre-push hook."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def test_ci_and_the_pre_push_hook_run_the_same_check_script():
    release = (ROOT / ".github/workflows/release.yml").read_text()
    assert "run: scripts/check.sh ruff mypy" in release and "run: scripts/check.sh vulture" in release
    assert "uvx " not in release  # tool versions live in scripts/check.sh only
    hook = ROOT / ".githooks/pre-push"
    assert hook.stat().st_mode & 0o111 and (ROOT / "scripts/check.sh").stat().st_mode & 0o111
    assert 'scripts/check.sh" --commit "$local_sha"' in hook.read_text()


def test_dead_code_fails_the_run_without_holding_back_the_image():
    release = (ROOT / ".github/workflows/release.yml").read_text()
    publish = release.split("\n  publish:\n", 1)[1].split("\n\n", 1)[0]
    assert "needs: [checks, lint, build]" in publish
