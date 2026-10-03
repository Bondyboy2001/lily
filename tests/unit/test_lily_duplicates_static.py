"""Static checks that the duplicates page follows docs/design.md.

Colours live in lily.css only; the page, its stylesheet (lily-duplicates.css) and its scripts use tokens.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CSS = REPO_ROOT / "cps/static/css"
JS = REPO_ROOT / "cps/static/js"
TEMPLATES = REPO_ROOT / "cps/templates"

DUPLICATES_TEMPLATES = ["duplicates.html"]
DUPLICATES_STYLESHEETS = ["lily-duplicates.css"]
DUPLICATES_SCRIPTS = ["duplicates.js", "duplicate-notifier.js"]

HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
# HTML character references (&#039;) look like hex colours to the regex above.
CHAR_REF = re.compile(r"&#x?[0-9a-fA-F]+;")
COLOUR_FUNCTION = re.compile(r"\b(?:rgba?|hsla?)\(")


def read(path):
    return path.read_text(encoding="utf-8")


def strip_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


@pytest.mark.parametrize("name", DUPLICATES_TEMPLATES)
def test_duplicates_templates_have_no_inline_style_blocks(name):
    assert "<style" not in read(TEMPLATES / name), f"{name}: move inline <style> into lily-duplicates.css"


@pytest.mark.parametrize("name", DUPLICATES_TEMPLATES)
def test_duplicates_templates_have_no_hard_coded_colours(name):
    text = CHAR_REF.sub("", read(TEMPLATES / name))
    assert not HEX.findall(text), f"{name}: hex colour outside lily.css"
    assert not COLOUR_FUNCTION.findall(text), f"{name}: rgb()/hsl() colour outside lily.css"
    assert "gradient" not in text.lower(), f"{name}: gradients are not part of the design language"


@pytest.mark.parametrize("name", DUPLICATES_STYLESHEETS)
def test_duplicates_stylesheets_only_shadow_floating_surfaces(name):
    # The one shadow in the language belongs to menus and popovers (§5); it comes from --menu-shadow.
    css = strip_comments(read(CSS / name))
    for value in re.findall(r"box-shadow\s*:\s*([^;]+);", css):
        assert value.strip() in ("none", "var(--menu-shadow)"), f"{name}: decorative box-shadow {value!r}"


@pytest.mark.parametrize("name", DUPLICATES_SCRIPTS)
def test_duplicates_scripts_inject_no_colours(name):
    text = CHAR_REF.sub("", read(JS / name))
    assert not HEX.findall(text), f"{name}: hex colour in script"
    assert "gradient" not in text.lower(), f"{name}: gradient in script"


def test_duplicates_views_have_at_most_one_primary_button():
    for name in DUPLICATES_TEMPLATES:
        html = read(TEMPLATES / name)
        # Modals are separate views; count only the page body outside them.
        body = re.split(r'class="modal[ "]', html, maxsplit=1)[0]
        assert body.count("btn-primary") <= 1, f"{name}: more than one Primary button in the view"


def test_duplicates_empty_state_follows_the_guide():
    # §5.13: the shared empty_state macro (.library-empty-state), not a page-local copy.
    html = read(TEMPLATES / "duplicates.html")
    assert "image.empty_state('glyphicon-ok-circle', _('No Duplicate Books')" in html
    assert "stats-empty" not in html
    assert ".stats-empty" not in read(CSS / "lily-duplicates.css")


def test_duplicates_empty_state_offers_a_scan():
    # Scanning runs itself after an import; the empty state's one action rescans on demand,
    # with no scan card or settings link beside it.
    html = read(TEMPLATES / "duplicates.html")
    assert re.search(r'<button type="button" class="btn btn-primary" id="scan_duplicates">'
                     r"{{_\('Scan for duplicates'\)}}</button>", html)
    assert "Run Full Duplicate Scan" not in html
    assert "Duplicate settings" not in html
    assert "next_scan_run" not in html
    script = read(JS / "duplicates.js")
    handler = script[script.index("$('#scan_duplicates').on('click'"):]
    assert "duplicateScanEndpoint('/duplicates/trigger-scan')" in handler
    assert "'X-CSRFToken': csrfToken" in handler
    assert "setInterval(pollDuplicateScanTask" in handler
