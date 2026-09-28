"""Static checks that the admin, configuration and settings pages follow ~/projects/DESIGN.md."""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CSS = REPO_ROOT / "cps/static/css"
TEMPLATES = REPO_ROOT / "cps/templates"

ADMIN_TEMPLATES = [
    "admin.html", "config_db.html", "config_edit.html", "config_view_edit.html", "cwa_settings.html",
    "cwa_read_log.html", "logviewer.html", "email_edit.html", "schedule_edit.html", "user_edit.html",
    "user_table.html", "kosync_plugin.html", "hardcover_review_matches.html",
    "generate_kobo_auth_url.html", "tasks.html", "remote_login.html", "http_error.html", "shelfdown.html",
    "lily_form.html",
]
ADMIN_STYLESHEETS = ["lily-admin.css", "lily-settings.css"]
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def read(path):
    return path.read_text(encoding="utf-8")


def existing(names):
    return [name for name in names if (TEMPLATES / name).exists()]


@pytest.mark.parametrize("name", existing(ADMIN_TEMPLATES))
def test_admin_templates_have_no_inline_style_blocks(name):
    assert "<style" not in read(TEMPLATES / name), name


@pytest.mark.parametrize("name", existing(ADMIN_TEMPLATES))
def test_admin_templates_have_no_hard_coded_colours_in_style_attributes(name):
    for attr in re.findall(r'style="([^"]*)"', read(TEMPLATES / name)):
        assert not HEX.search(attr), (name, attr)
        assert "--color-secondary" not in attr, (name, attr)


@pytest.mark.parametrize("name", ADMIN_STYLESHEETS)
def test_admin_stylesheets_use_tokens_not_hex(name):
    css = re.sub(r"/\*.*?\*/", "", read(CSS / name), flags=re.S)
    assert not HEX.search(css), (name, HEX.findall(css)[:5])
    for banned in ("gradient", "backdrop-filter", "blur(", "--color-secondary"):
        assert banned not in css, (name, banned)


@pytest.mark.parametrize("name", ["http_error.html", "shelfdown.html"])
def test_standalone_pages_use_lily_css_not_caliblur(name):
    html = read(TEMPLATES / name)
    assert "caliBlur" not in html
    assert "css/lily.css" in html


def test_profile_hides_theme_picker_but_still_posts_theme():
    html = read(TEMPLATES / "user_edit.html")
    assert "caliBlur" not in html
    assert '<select name="theme"' not in html
    assert re.search(r'<input type="hidden" name="theme"[^>]*value="1"', html)


def test_admin_headings_carry_no_emoji():
    html = read(TEMPLATES / "admin.html")
    assert '<span aria-hidden="true">{{ emoji }}</span>' not in html
    # Emoji survive only inside the msgids passed to emoji_heading, which strips them.
    for line in html.splitlines():
        if re.search(r"[\U0001F300-\U0001FAFF⌛⚡⚙]", line):
            assert "emoji_heading(" in line, line


def test_settings_save_bar_has_one_primary_and_quiet_reset():
    html = read(TEMPLATES / "cwa_settings.html")
    bar = html[html.index('class="lily-savebar"'):html.index("</form>")]
    assert bar.count("btn-primary") == 1
    reset = re.search(r'<button[^>]*lily-btn-reset[^>]*>', bar).group(0)
    assert "btn-default" in reset and "btn-danger" not in reset


def test_folder_pickers_are_labelled_icon_buttons():
    for name in ("config_edit.html", "config_db.html", "lily_form.html"):
        html = read(TEMPLATES / name)
        for button in re.findall(r"<button[^>]*>\s*<span class=\"glyphicon glyphicon-folder-open", html):
            assert 'class="icon-btn"' in button, (name, button)
            assert "aria-label=" in button and "title=" in button, (name, button)


SETTINGS_FORMS = ["admin.html", "config_edit.html", "config_view_edit.html", "config_db.html",
                  "email_edit.html", "schedule_edit.html", "user_edit.html", "cwa_settings.html"]


@pytest.mark.parametrize("name", SETTINGS_FORMS)
def test_settings_pages_use_the_shared_row_macros(name):
    html = read(TEMPLATES / name)
    assert '{% import "lily_form.html" as f with context %}' in html
    assert 'class="lp' in html or "f.group(" in html
    # The old stacked cards are gone.
    assert "settings-container" not in html


@pytest.mark.parametrize("name", SETTINGS_FORMS + ["user_table.html"])
def test_settings_pages_share_the_settings_frame(name):
    html = read(TEMPLATES / name)
    assert html.startswith('{% extends "settings_layout.html" %}'), name
    assert "{% block settings %}" in html and "{% block body %}" not in html, name


def test_settings_frame_has_rail_search_and_lily_tabs():
    html = read(TEMPLATES / "settings_layout.html")
    assert 'id="lp-search"' in html and "lily-settings-shell.js" in html
    for tab in ("services", "ingest", "metadata", "hardcover", "duplicates", "maintenance", "interface"):
        assert f"'#{tab}'" in html, tab
    # The Lily settings page no longer draws its own chip row; the rail drives its tabs.
    assert "lily-settings-tabs" not in read(TEMPLATES / "cwa_settings.html")


@pytest.mark.parametrize("name", [n for n in SETTINGS_FORMS if n != "admin.html"])
def test_settings_forms_have_one_primary(name):
    html = read(TEMPLATES / name)
    body = html[html.index("{% block settings %}"):]
    body = re.sub(r'<div[^>]*class="modal.*', "", body, flags=re.S)
    assert body.count("btn-primary") == 1, name
