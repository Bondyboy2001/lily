"""Static checks that the admin, configuration and settings pages follow docs/design.md."""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CSS = REPO_ROOT / "cps/static/css"
TEMPLATES = REPO_ROOT / "cps/templates"

ADMIN_TEMPLATES = [
    "cwa_settings.html", "user_edit.html", "user_table.html", "http_error.html", "lily_form.html",
]
ADMIN_STYLESHEETS = ["lily-admin.css"]
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


@pytest.mark.parametrize("name", ["http_error.html"])
def test_standalone_pages_use_lily_css_not_caliblur(name):
    html = read(TEMPLATES / name)
    assert "caliBlur" not in html
    assert "css/lily.css" in html


def test_profile_hides_theme_picker_but_still_posts_theme():
    html = read(TEMPLATES / "user_edit.html")
    assert "caliBlur" not in html
    assert '<select name="theme"' not in html
    assert re.search(r'<input type="hidden" name="theme"[^>]*value="1"', html)


@pytest.mark.parametrize("name", existing(ADMIN_TEMPLATES))
def test_admin_headings_carry_no_emoji(name):
    html = read(TEMPLATES / name)
    assert '<span aria-hidden="true">{{ emoji }}</span>' not in html, name
    # Emoji survive only inside the msgids passed to emoji_heading, which strips them.
    for line in html.splitlines():
        if re.search(r"[\U0001F300-\U0001FAFF⌛⚡⚙]", line):
            assert "emoji_heading(" in line, (name, line)


SETTINGS_FORMS = ["user_edit.html", "cwa_settings.html"]


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


def test_settings_frame_is_a_short_rail_without_search_or_tabs():
    html = read(TEMPLATES / "settings_layout.html")
    assert "lily-settings-shell.js" in html
    for gone in ('id="lp-search"', "lp-tabs", "'pane'"):
        assert gone not in html, gone


def test_settings_rail_signout_is_a_quiet_button():
    html = read(TEMPLATES / "settings_layout.html")
    assert "lp-rail-sep" not in html
    assert "_('Logout')" not in html
    block = re.search(r"{% if signed_in %}(.*?){% endif %}", html, flags=re.S)
    assert block, "signed_in block missing"
    frag = block.group(1)
    assert 'class="lp-rail-signout-row"' in frag
    assert re.search(
        r'<a href="{{ url_for\(\'web\.logout\'\) }}" class="btn btn-default lp-rail-signout" id=\'logout\'>{{ _\(\'Sign out\'\) }}</a>',
        frag)


def test_signout_row_styles_desktop_and_mobile():
    css = read(CSS / "lily-admin.css")
    assert re.search(
        r"\.lp-rail-list > \.lp-rail-signout-row\s*{[^}]*margin-top:\s*12px[^}]*padding-left:\s*10px", css)
    assert re.search(r"\.lp-rail-signout\s*{[^}]*white-space:\s*nowrap", css)
    assert re.search(r"\.lp-rail-signout\.btn,[^{]*{[^}]*background:\s*var\(--danger\)", css)
    container = re.search(r"@container lp-settings[^\n]*{(.*)", css, flags=re.S)
    assert container
    assert re.search(
        r"\.lp-rail-list > \.lp-rail-signout-row\s*{[^}]*margin-top:\s*0[^}]*padding-left:\s*0", container.group(1))


def test_settings_rail_lists_only_the_essential_pages():
    html = read(TEMPLATES / "settings_layout.html")
    ids = re.findall(r"\{'id': '(\w+)', 'group'", html)
    assert ids == ["profile", "import", "users", "duplicates", "logs"]
    assert "maintenance" not in html and "admin.admin" not in html


@pytest.mark.parametrize("name", ["duplicates.html", "logs.html"])
def test_utility_pages_share_the_settings_frame(name):
    html = read(TEMPLATES / name)
    assert html.startswith('{% extends "settings_layout.html" %}'), name
    assert "{% block settings %}" in html and "{% block body %}" not in html, name
    assert "{% block pane_class %} is-wide{% endblock %}" in html, name


def test_sidebar_has_no_utility_links():
    layout = read(TEMPLATES / "layout.html")
    assert 'id="nav_duplicates"' not in layout and 'id="nav_logs"' not in layout
    sidebar = layout[layout.index('<aside class="lily-sidebar"'):layout.index("</aside>")]
    assert "logs.show_logs" not in sidebar
    settings = read(TEMPLATES / "settings_layout.html")
    assert "'id': 'duplicates'" in settings and "duplicates.show_duplicates" in settings
    assert "signed_in and (is_admin or current_user.role_edit())" in settings
    assert "'id': 'logs'" in settings and "logs.show_logs" in settings
    assert 'id="duplicate-count-badge"' in settings
    sidebar_source = read(REPO_ROOT / "cps/render_template.py")
    assert '"id": "duplicates"' not in sidebar_source


def test_removed_settings_pages_are_gone():
    for name in ("config_edit.html", "config_view_edit.html", "schedule_edit.html", "config_db.html",
                 "db_backups.html", "book_recovery.html", "ingest_failures.html", "metadata_suggestions.html",
                 "tasks.html", "hardcover_review_matches.html", "account_security.html", "reading_stats.html",
                 "cwa_stats_tabs.html"):
        assert not (TEMPLATES / name).exists(), name


@pytest.mark.parametrize("name", SETTINGS_FORMS)
def test_settings_forms_have_one_primary(name):
    html = read(TEMPLATES / name)
    body = html[html.index("{% block settings %}"):]
    body = re.sub(r'<div[^>]*class="modal.*', "", body, flags=re.S)
    assert body.count("btn-primary") == 1, name
