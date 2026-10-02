"""Static checks that the admin and settings pages follow docs/design.md."""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CSS = REPO_ROOT / "cps/static/css"
TEMPLATES = REPO_ROOT / "cps/templates"

ADMIN_TEMPLATES = [
    "cwa_settings.html", "user_edit.html", "user_table.html", "http_error.html", "lily_form.html",
]
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
EMOJI = re.compile("[\U0001F300-\U0001FAFF⌛☀-➿]")


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


@pytest.mark.parametrize("name", ["http_error.html"])
def test_standalone_pages_use_lily_css(name):
    html = read(TEMPLATES / name)
    assert "css/lily.css" in html


def test_profile_hides_theme_picker_but_still_posts_theme():
    html = read(TEMPLATES / "user_edit.html")
    assert '<select name="theme"' not in html
    assert re.search(r'<input type="hidden" name="theme"[^>]*value="1"', html)


@pytest.mark.parametrize("name", existing(ADMIN_TEMPLATES) + ["duplicates.html"])
def test_admin_labels_carry_no_emoji(name):
    assert not EMOJI.findall(read(TEMPLATES / name)), name


SETTINGS_FORMS = ["user_edit.html", "cwa_settings.html"]


@pytest.mark.parametrize("name", SETTINGS_FORMS)
def test_settings_pages_use_the_shared_row_macros(name):
    html = read(TEMPLATES / name)
    assert '{% import "lily_form.html" as f with context %}' in html
    assert 'class="lp' in html or "f.group(" in html


@pytest.mark.parametrize("name", SETTINGS_FORMS + ["user_table.html"])
def test_settings_pages_share_the_settings_frame(name):
    html = read(TEMPLATES / name)
    assert html.startswith('{% extends "settings_layout.html" %}'), name
    assert "{% block settings %}" in html and "{% block body %}" not in html, name


def test_settings_frame_loads_its_shell_script():
    assert "lily-settings-shell.js" in read(TEMPLATES / "settings_layout.html")


def test_users_page_is_people_cards_with_an_add_tile():
    html = read(TEMPLATES / "user_table.html")
    assert 'class="lp-people"' in html and 'class="lp-person"' in html
    assert 'class="lp-monogram"' in html and "_('You')" in html
    # Add user is the next slot in the grid, not a Primary button floating under it.
    assert 'class="lp-person-add" id="add_user"' in html
    assert "btn-primary" not in html and "lp-actions" not in html
    css = read(CSS / "lily-admin.css")
    assert re.search(r"\.lp-people \{[^}]*minmax\(260px, 1fr\)[^}]*gap: 22px", css)
    assert re.search(r"@container lp-settings \(max-width: 760px\) \{.*\.lp-people \{ grid-template-columns: minmax\(0, 1fr\)",
                     css, flags=re.S)


def test_settings_rail_signout_is_a_quiet_button():
    html = read(TEMPLATES / "settings_layout.html")
    assert "lp-rail-sep" not in html
    assert "_('Logout')" not in html
    block = re.search(r"{% if signed_in %}(.*?){% endif %}", html, flags=re.S)
    assert block, "signed_in block missing"
    frag = block.group(1)
    assert 'class="lp-rail-signout-row"' in frag
    assert re.search(
        r'<a href="{{ url_for\(\'web\.logout\'\) }}" class="btn btn-default is-danger lp-rail-signout" id=\'logout\'>{{ _\(\'Sign out\'\) }}</a>',
        frag)


def test_signout_row_styles_desktop_and_mobile():
    css = read(CSS / "lily-admin.css")
    assert re.search(
        r"\.lp-rail-list > \.lp-rail-signout-row\s*{[^}]*margin-top:\s*12px[^}]*padding-left:\s*10px", css)
    assert re.search(r"\.lp-rail-signout\s*{[^}]*white-space:\s*nowrap", css)
    assert not re.search(r"\.lp-rail-signout[^{]*{[^}]*background:\s*var\(--danger\)", css)
    container = re.search(r"@container lp-settings[^\n]*{(.*)", css, flags=re.S)
    assert container
    assert re.search(
        r"\.lp-rail-list > \.lp-rail-signout-row\s*{[^}]*margin-top:\s*0[^}]*padding-left:\s*0", container.group(1))


@pytest.mark.parametrize("name", ["duplicates.html", "logs.html"])
def test_utility_pages_share_the_settings_frame(name):
    html = read(TEMPLATES / name)
    assert html.startswith('{% extends "settings_layout.html" %}'), name
    assert "{% block settings %}" in html and "{% block body %}" not in html, name
    assert "{% block pane_class %} is-wide{% endblock %}" in html, name


def test_logs_page_is_a_live_tail():
    html = read(TEMPLATES / "logs.html")
    assert 'id="log_output"' in html and 'role="status"' in html
    js = read(REPO_ROOT / "cps/static/js/logs.js")
    assert "visibilitychange" in js and "since=" in js


def test_logs_page_copies_what_it_shows():
    html = read(TEMPLATES / "logs.html")
    button = html[html.index('id="log_copy"'):html.index("</button>")]
    # An icon button in the log's top-right corner, named by its tooltip and a hidden label.
    assert 'class="icon-btn logs-copy"' in button and "disabled" in button
    assert 'title="{{ _(\'Copy logs\') }}"' in button and 'class="sr-only logs-copy-label"' in button
    assert "glyphicon-copy" in button and 'aria-hidden="true"' in button and 'aria-live="polite"' in button
    frame = html[html.index('class="logs-frame"'):]
    assert frame.index('id="log_copy"') < frame.index('id="log_output"') < frame.index("</div>")
    js = read(REPO_ROOT / "cps/static/js/logs.js")
    # Plain-HTTP installs have no Clipboard API, so there must be a fallback.
    assert "navigator.clipboard" in js and "isSecureContext" in js and 'execCommand("copy")' in js
    assert '$copy.prop("disabled", !text)' in js
    css = read(REPO_ROOT / "cps/static/css/lily-admin.css")
    assert ".logs-copy-buffer" in css and ".lily-logs .logs-frame { position: relative; }" in css
    assert ".lily-logs .logs-copy { position: absolute; top: 8px; right: 8px; }" in css


@pytest.mark.parametrize("name", SETTINGS_FORMS)
def test_settings_forms_have_one_primary(name):
    html = read(TEMPLATES / name)
    body = html[html.index("{% block settings %}"):]
    body = re.sub(r'<div[^>]*class="modal.*', "", body, flags=re.S)
    assert body.count("btn-primary") == 1, name
