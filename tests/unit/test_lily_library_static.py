"""Static checks for the library pages' restyle onto ~/projects/DESIGN.md (phase 2)."""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CSS = REPO_ROOT / "cps/static/css"
JS = REPO_ROOT / "cps/static/js"
TEMPLATES = REPO_ROOT / "cps/templates"

LIBRARY_TEMPLATES = [
    "index.html", "grid.html", "list.html", "detail.html", "author.html", "search.html",
    "search_form.html", "shelf.html", "shelf_edit.html", "shelf_order.html",
    "book_edit.html", "book_table.html", "image.html", "modal_dialogs.html",
]
SORT_TEMPLATES = ["index.html", "author.html", "search.html", "shelf.html", "grid.html", "list.html"]


def read(path):
    return path.read_text(encoding="utf-8")


def css_rules(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(sel.strip(), body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def test_layout_no_longer_loads_caliblur_js():
    layout = read(TEMPLATES / "layout.html")
    assert "caliBlur.js" not in layout
    assert "css/lily-library.css" in layout
    assert layout.index("css/lily-shell.css") < layout.index("css/lily-library.css")


def test_library_templates_have_no_inline_style_blocks():
    for name in LIBRARY_TEMPLATES:
        assert "<style" not in read(TEMPLATES / name), name


def test_detail_page_has_no_inline_styles_and_one_primary():
    html = read(TEMPLATES / "detail.html")
    assert 'style="' not in html
    # Actions are icon-only; Read is the one primary, marked by the accent colour.
    read_btn = html[html.index('id="readbtn"'):html.index('glyphicon-book')]
    assert "icon-btn" in read_btn and "is-primary" in read_btn
    assert html.count("is-primary") == 1
    # The trash is a plain icon button, not red at rest.
    delete_btn = html[html.index('id="delete"') - 200:html.index('id="delete"')]
    assert "icon-btn" in delete_btn and "btn-danger" not in delete_btn


def test_lily_library_css_uses_tokens_only():
    css = re.sub(r"/\*.*?\*/", "", read(CSS / "lily-library.css"), flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    for banned in ("rgba(", "hsla(", "gradient", "backdrop-filter", "blur("):
        assert banned not in css, banned


def test_lily_library_css_shadows_only_on_menus():
    for selector, body in css_rules(read(CSS / "lily-library.css")):
        for value in re.findall(r"box-shadow\s*:\s*([^;]+)", body):
            if value.strip() != "none":
                assert "tt-menu" in selector or "dropdown-menu" in selector, selector


def test_sort_bars_are_dropdowns_not_solid_buttons():
    for name in SORT_TEMPLATES:
        html = read(TEMPLATES / name)
        assert html.count("btn-primary") <= 1, name  # at most the view's one Primary (index: empty-state action)
        if name in ("grid.html", "list.html"):
            # these pages have no server-side sort: order and letter filter live in list_menu
            assert "image.list_menu(order, " in html and "image.chip(" not in html, name
        else:
            assert "image.sort_menu(order, " in html and "image.chip(" not in html, name
    macro = read(TEMPLATES / "image.html")
    assert 'class="btn lily-chip dropdown-toggle"' in macro and 'data-toggle="dropdown"' in macro
    rules = dict(css_rules(read(CSS / "lily-library.css")))
    chosen = rules[".btn.lily-chip.active,\n.btn.lily-chip[aria-current=\"true\"],\n.btn.lily-chip.active:focus"]
    assert "var(--accent-soft)" in chosen and "var(--accent)" in chosen


def test_advanced_search_pickers_are_fields():
    html = read(TEMPLATES / "search_form.html")
    assert 'data-style="btn-primary"' not in html and 'data-style="btn-danger"' not in html
    assert html.count("btn-primary") == 1  # the Search button
    css = read(CSS / "lily-library.css")
    assert ".bootstrap-select > .dropdown-toggle" in css


def test_cover_grid_is_a_css_grid_not_isotope():
    css = read(CSS / "lily-library.css")
    assert re.search(r"\.row\.lily-grid\s*\{[^}]*display:\s*grid", css)
    main_js = read(JS / "main.js")
    assert '.not(".lily-grid")' in main_js


def test_quick_actions_are_markup_not_injected():
    image = read(TEMPLATES / "image.html")
    assert "macro cover_actions" in image and "icon-btn" in image
    js = read(JS / "lily.js")
    for needle in ("lily-toggle-read", "lily-send-ereader", "lily-read-now", "/ajax/toggleread/"):
        assert needle in js, needle


def test_lily_js_keeps_the_caliblur_behaviours_that_are_still_needed():
    js = read(JS / "lily.js")
    for needle in ("shown.bs.dropdown", "dropdown-menu-right", "readmore", ".tooltip(", 'target: "_blank"'):
        assert needle in js, needle
    # The shell behaviour is still there.
    assert "drawer-open" in js


def test_style_css_has_no_legacy_colours():
    css = re.sub(r"/\*.*?\*/", "", read(CSS / "style.css"), flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert "rgba(" not in css and "gradient" not in css
    assert "body h2" not in css  # it outranked lily.css headings


def test_book_links_open_the_book_page_not_a_modal():
    for name in ["index.html", "author.html", "search.html", "shelf.html", "image.html", "book_exists_flash.html"]:
        assert "#bookDetailsModal" not in read(TEMPLATES / name), name
