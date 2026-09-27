"""Static checks that the stats and duplicates pages follow ~/projects/DESIGN.md.

Colours live in lily.css only; these pages and their stylesheet use tokens, and charts read the tokens at
runtime through static/js/lily-charts.js.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CSS = REPO_ROOT / "cps/static/css"
JS = REPO_ROOT / "cps/static/js"
TEMPLATES = REPO_ROOT / "cps/templates"

STATS_TEMPLATES = [
    "stats.html", "cwa_stats_full.html", "cwa_stats_system.html", "cwa_stats_tabs.html",
    "cwa_user_activity.html", "cwa_library_stats.html", "cwa_api_stats.html", "duplicates.html",
]
STATS_STYLESHEETS = ["lily-stats.css", "duplicates-notifications.css"]
STATS_SCRIPTS = ["lily-charts.js", "duplicates.js", "duplicate-notifier.js"]

HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
# HTML character references (&#039;) look like hex colours to the regex above.
CHAR_REF = re.compile(r"&#x?[0-9a-fA-F]+;")
COLOUR_FUNCTION = re.compile(r"\b(?:rgba?|hsla?)\(")
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def read(path):
    return path.read_text(encoding="utf-8")


def strip_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


@pytest.mark.parametrize("name", STATS_TEMPLATES)
def test_stats_templates_have_no_inline_style_blocks(name):
    assert "<style" not in read(TEMPLATES / name), f"{name}: move inline <style> into lily-stats.css"


@pytest.mark.parametrize("name", STATS_TEMPLATES)
def test_stats_templates_have_no_hard_coded_colours(name):
    text = CHAR_REF.sub("", read(TEMPLATES / name))
    assert not HEX.findall(text), f"{name}: hex colour outside lily.css"
    assert not COLOUR_FUNCTION.findall(text), f"{name}: rgb()/hsl() colour outside lily.css"
    assert "gradient" not in text.lower(), f"{name}: gradients are not part of the design language"


@pytest.mark.parametrize("name", STATS_STYLESHEETS)
def test_stats_stylesheets_use_tokens_only(name):
    css = strip_comments(read(CSS / name))
    assert not HEX.findall(css), f"{name}: hex colour outside lily.css"
    assert not COLOUR_FUNCTION.findall(css), f"{name}: rgb()/hsl() colour outside lily.css"
    for banned in ("gradient", "backdrop-filter", "blur("):
        assert banned not in css, f"{name}: {banned} is not part of the design language"


@pytest.mark.parametrize("name", STATS_STYLESHEETS)
def test_stats_stylesheets_only_shadow_floating_surfaces(name):
    # The one shadow in the language belongs to menus and popovers (§5); it comes from --menu-shadow.
    css = strip_comments(read(CSS / name))
    for value in re.findall(r"box-shadow\s*:\s*([^;]+);", css):
        assert value.strip() in ("none", "var(--menu-shadow)"), f"{name}: decorative box-shadow {value!r}"


@pytest.mark.parametrize("name", STATS_SCRIPTS)
def test_stats_scripts_inject_no_colours(name):
    text = CHAR_REF.sub("", read(JS / name))
    assert not HEX.findall(text), f"{name}: hex colour in script"
    assert "gradient" not in text.lower(), f"{name}: gradient in script"


@pytest.mark.parametrize("name", ["cwa_stats_tabs.html", "cwa_user_activity.html", "cwa_library_stats.html",
                                  "cwa_api_stats.html", "cwa_stats_system.html", "duplicates.html"])
def test_stats_labels_have_no_emoji(name):
    assert not EMOJI.findall(read(TEMPLATES / name)), f"{name}: emoji in labels"


def test_stats_tabs_are_chips():
    html = read(TEMPLATES / "cwa_stats_tabs.html")
    assert "nav nav-pills stats-tabs" in html
    assert 'role="tablist"' in html


def test_stats_charts_use_the_lily_theme():
    for name in ("cwa_user_activity.html", "cwa_library_stats.html", "cwa_api_stats.html"):
        html = read(TEMPLATES / name)
        assert "echarts.init(" not in html, f"{name}: create charts with LilyCharts.init so tokens apply"
        assert "LilyCharts.init(" in html
    assert "js/lily-charts.js" in read(TEMPLATES / "cwa_stats_tabs.html")


def test_stats_views_have_at_most_one_primary_button():
    for name in STATS_TEMPLATES:
        html = read(TEMPLATES / name)
        # Modals are separate views; count only the page body outside them.
        body = re.split(r'class="modal[ "]', html, maxsplit=1)[0]
        assert body.count("btn-primary") <= 1, f"{name}: more than one Primary button in the view"


def test_duplicates_empty_state_follows_the_guide():
    html = read(TEMPLATES / "duplicates.html")
    assert "stats-empty" in html
    assert "stats-empty-glyph" in html and "stats-empty-title" in html
