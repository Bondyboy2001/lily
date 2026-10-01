"""Static checks that deleted admin-dashboard code stays deleted."""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CPS = REPO_ROOT / "cps"


def read(path):
    return path.read_text(encoding="utf-8")


@pytest.mark.unit
def test_admin_template_is_gone():
    assert not (CPS / "templates" / "admin.html").exists()


@pytest.mark.unit
def test_nothing_renders_admin_template():
    for path in CPS.rglob("*.py"):
        assert "admin.html" not in read(path), path


@pytest.mark.unit
def test_main_js_has_no_dead_admin_handlers():
    js = read(CPS / "static" / "js" / "main.js")
    for gone in ("restartTimer", "pollTaskCompletion", '#restart"',
                 '#shutdown"', '#admin_refresh_cover_cache"',
                 '#restart_database"', '#metadata_backup"',
                 '#hardcover_auto_fetch"'):
        assert gone not in js, gone


@pytest.mark.unit
def test_admin_py_has_no_dead_imports_or_functions():
    src = read(CPS / "admin.py")
    assert "cwa_get_package_versions" not in src
    assert "admin_refresh_cover_cache" not in src
    assert "datetime_time" not in src
    assert "format_timedelta" not in src
    assert "format_time" not in src


@pytest.mark.unit
def test_layout_has_no_dynamic_duplicates_branch():
    html = read(CPS / "templates" / "layout.html")
    assert "element['id'] == 'duplicates'" not in html
    settings = read(CPS / "templates" / "settings_layout.html")
    assert 'id="duplicate-count-badge"' in settings


@pytest.mark.unit
def test_dead_admin_css_selectors_removed():
    css = read(CPS / "static" / "css" / "lily-admin.css")
    for cls in ("lily-admin-dashboard", "lily-facts", "lily-fact",
                "lily-fact-note", "lily-bool", "lily-admin-table"):
        assert not re.search(r"\." + cls + r"\b", css), cls


@pytest.mark.unit
def test_reverseproxy_has_no_dead_state():
    src = read(CPS / "reverseproxy.py")
    assert "is_proxied" not in src and "self.proxied" not in src


@pytest.mark.unit
def test_log_archive_constant_removed():
    pkg = read(CPS / "cwa_functions" / "__init__.py")
    common = read(CPS / "cwa_functions" / "common.py")
    assert "LOG_ARCHIVE" not in pkg and "LOG_ARCHIVE" not in common
