"""Static checks for the library pages' restyle onto docs/design.md (phase 2)."""
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
    # Actions are labelled buttons; Read ("Read" or "Continue · 42%") is the one Primary and
    # opens the reader in this tab.
    m = re.search(r"<a id=\"readbtn\"[^>]*>.*?</a>", html, flags=re.S)
    assert m, "readbtn anchor missing"
    read_btn = m.group(0)
    assert 'class="btn btn-primary"' in read_btn and 'target="_blank"' not in read_btn
    assert "url_for('web.read_book'" in read_btn
    assert "{{ read_label }}" in read_btn and "_('Continue')" in html
    assert html.count("btn-primary") == 1
    assert "btn-danger" not in html


def test_detail_edit_and_read_state_are_named_icon_buttons():
    html = read(TEMPLATES / "detail.html")
    edit = re.search(r'<a href="[^"]*show_edit_book[^"]*" id="edit_book" class="btn is-icon"[^>]*>', html, flags=re.S)
    assert edit, "Edit Metadata icon button missing"
    assert "aria-label=\"{{ _('Edit Metadata') }}\"" in edit.group(0)
    toggle = re.search(r'<button[^>]*id="toggle-read-btn"[^>]*>(.*?)</button>', html, flags=re.S)
    assert toggle and 'class="btn is-icon"' in toggle.group(0)
    assert 'class="book-action-label sr-only"' in toggle.group(1)
    css = read(CSS / "lily-library.css")
    assert "width: 36px" in _rules_by_selector(css, ".book-action-bar > .btn.is-icon")[".book-action-bar > .btn.is-icon"]


def test_detail_rare_actions_are_icon_buttons_not_a_menu():
    html = read(TEMPLATES / "detail.html")
    assert "book-more" not in html and "More actions" not in html
    for needle, label in (('class="btn is-icon uuid-copy"', "Copy UUID"), ('id="delete"', "Delete Book")):
        btn = re.search(r'<button[^>]*' + re.escape(needle) + r'[^>]*>', html, flags=re.S)
        assert btn, needle
        assert "aria-label=\"{{ _('" + label + "') }}\"" in btn.group(0)
    archive = re.search(r'<button[^>]*id="toggle-archive-btn"[^>]*>(.*?)</button>', html, flags=re.S)
    assert archive and 'class="btn is-icon"' in archive.group(0)
    assert 'class="book-action-label sr-only"' in archive.group(1)


def test_detail_description_has_no_heading_and_shows_in_full():
    html = read(TEMPLATES / "detail.html")
    section = re.search(r'<section class="book-detail-description"(.*?)</section>', html, flags=re.S).group(0)
    assert "<h3" not in section and "aria-label=\"{{ _('Description') }}\"" in section
    assert 'class="comments"' in section
    assert "is-clamped" not in html and "book-description-toggle" not in html
    for selector, body in css_rules(read(CSS / "lily-library.css")):
        if ".book-detail-description" in selector:
            assert "line-clamp" not in body, selector


def test_detail_tags_are_plain_links_in_the_facts_panel():
    html = read(TEMPLATES / "detail.html")
    panel = re.search(r'<dl class="book-metadata">(.*?)</dl>', html, flags=re.S).group(0)
    tags = re.search(r'<div class="tags">(.*?)</div>', panel, flags=re.S).group(0)
    assert "data='category'" in tags and "glyphicon" not in tags and "lily-chip" not in tags
    assert "is-tag" not in html and "is-tag" not in read(CSS / "lily-library.css")


def test_continue_reading_progress_sits_on_the_cover():
    html = read(TEMPLATES / "index.html")
    cover = re.search(r'<div class="cover">(.*?)\n      </div>', html, flags=re.S).group(1)
    meta = re.search(r'<div class="meta">(.*?)\n      </div>', html, flags=re.S).group(1)
    assert "continue-reading-progress" in cover and "progress" not in meta


def test_continue_reading_opens_the_reader_in_this_tab():
    html = read(TEMPLATES / "index.html")
    cover = re.search(r'<div class="cover">(.*?)\n      </div>', html, flags=re.S).group(1)
    assert 'href="{{ resume_url }}"' in cover and 'target="_blank"' not in cover


def test_detail_toolbar_buttons_are_labelled():
    css = read(CSS / "lily-library.css")
    parts = _rules_by_selector(css, ".book-action-bar")
    body = parts[".book-action-bar > .btn"]
    for decl in ("display: inline-flex", "height: 36px"):
        assert decl in body, decl
    lily = read(CSS / "lily.css")
    assert re.search(r"\.icon-btn:hover,\s*\.icon-btn:focus\s*{[^}]*color:\s*var\(--accent\)", lily)
    assert re.search(r":focus-visible[^{]*{[^}]*outline:\s*2px solid", lily)
    assert re.search(r"\.icon-btn::after\s*{[^}]*content:\s*[\"']", lily)


def _rules_by_selector(css_text, prefix):
    parts = {}
    for selector, body in css_rules(css_text):
        for part in selector.split(","):
            part = part.strip()
            if part.startswith(prefix):
                parts[part] = body
    return parts


def test_detail_read_toggle_shows_state_without_a_disc():
    sel = '.book-action-bar #toggle-read-btn[aria-pressed="true"] .glyphicon'
    parts = _rules_by_selector(read(CSS / "lily-library.css"), ".book-action-bar #toggle-read-btn")
    assert "color: var(--success)" in parts[sel]
    for body in parts.values():
        assert "border-radius: 50%" not in body and "background: var(--success)" not in body
    html = read(TEMPLATES / "detail.html")
    assert "entry.read_status and 'glyphicon-ok' or 'glyphicon-eye-open'" in html


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
    for needle in ("lily-toggle-read", "/ajax/toggleread/", "lily-shelf-item", "/shelf/book/"):
        assert needle in js, needle


def test_read_quick_action_is_a_same_tab_link_to_a_readable_format():
    image = read(TEMPLATES / "image.html")
    actions = image[image.index("macro cover_actions"):image.index("macro book_card")]
    # The same list as helper.check_read_formats, so MOBI/AZW3/FB2/HTML never get a Read button.
    assert "book|reader_formats" in actions and "data-book-formats" not in actions
    read_now = actions[actions.index("lily-read-now") - 40:actions.index("lily-read-now") + 200]
    assert "<a " in read_now and "web.read_book" in read_now and "_blank" not in read_now
    js = read(JS / "lily.js")
    assert "window.open" not in js and "pickFormat" not in js
    # Only external links (arXiv, identifiers) in the facts panel open a new tab
    assert 'target="_blank"' not in read(TEMPLATES / "detail.html").split('<dl class="book-metadata">')[0]


def test_continue_reading_opens_the_reader():
    html = read(TEMPLATES / "index.html")
    row = html[html.index('class="continue-reading"'):html.index('class="continue-reading up-next"')]
    assert "web.read_book" in row and "book|reader_formats" in row
    assert "data-book-formats" not in html


def test_empty_shelf_text_names_a_control_that_exists():
    html = read(TEMPLATES / "shelf.html")
    assert "Add to shelf" not in html and "Shelves button" in html
    assert 'title="{{ _(\'Shelves\') }}"' in read(TEMPLATES / "image.html")


def test_lily_js_keeps_the_caliblur_behaviours_that_are_still_needed():
    js = read(JS / "lily.js")
    # "readmore" was dropped on purpose when lily.js replaced caliblur.js, so it is
    # deliberately not in this list.
    for needle in ("shown.bs.dropdown", "dropdown-menu-right", 'target: "_blank"'):
        assert needle in js, needle
    # The app builds no JS tooltips: the design has none, and the one Bootstrap tooltip
    # that did exist flickered because it was appended to <body> over its own trigger.
    assert ".tooltip(" not in js
    # The shell behaviour is still there.
    assert "drawer-open" in js


def test_style_css_has_no_legacy_colours():
    css = re.sub(r"/\*.*?\*/", "", read(CSS / "style.css"), flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert "rgba(" not in css and "gradient" not in css
    assert "body h2" not in css  # it outranked lily.css headings


def test_book_links_open_the_book_page_not_a_modal():
    for name in ["index.html", "author.html", "search.html", "shelf.html", "image.html"]:
        assert "#bookDetailsModal" not in read(TEMPLATES / name), name


def test_detail_rows_keep_metadata_in_a_side_panel():
    css = read(CSS / "lily-library.css")
    main_rules = [body for selector, body in css_rules(css) if selector == ".book-detail-main"]
    # Wide: cover | book | facts panel, with content-sized heading and action rows.
    # The middle column fits its content, so the panel fills the space a short title leaves.
    assert "grid-template-columns: clamp(220px, 17vw, 300px) minmax(min(100%, 420px), max-content) minmax(260px, 1fr)" in main_rules[0]
    assert "grid-template-rows: min-content min-content 1fr" in main_rules[0]
    # Phone: row sizing is reset.
    assert "grid-template-rows: auto" in main_rules[-1]
    metadata = next(body for selector, body in css_rules(css) if selector == "dl.book-metadata")
    for declaration in ("grid-column: 3", "padding: 20px 22px", "border: 0", "border-radius: 10px",
                        "background: var(--surface)", "margin: 0"):
        assert declaration in metadata
    html = read(TEMPLATES / "detail.html")
    # The panel is its own grid item, not tucked under the description.
    assert html.index('<dl class="book-metadata">') < html.index('<div class="book-detail-extra">')


def test_site_has_no_horizontal_separator_borders():
    names = ["style.css", "lily.css", "lily-shell.css", "lily-library.css",
             "lily-admin.css", "lily-stats.css", "lily-reader.css",
             "duplicates-notifications.css", "login.css"]
    rule = r"border-(?:top|bottom):\s*1px solid var\(--line(?:-soft)?\)"
    for name in names:
        css = re.sub(r"/\*.*?\*/", "", read(CSS / name), flags=re.S)
        if name == "lily-shell.css":
            # The one exception: the sticky top bar's bottom rule.
            topbar = re.search(r"\.navbar\.lily-topbar \{[^}]*\}", css).group(0)
            assert "border-bottom: 1px solid var(--line-soft)" in topbar
            css = css.replace(topbar, "")
        assert not re.search(rule, css), name
    rules = dict(css_rules(read(CSS / "lily.css")))
    assert "display: none" in rules["hr"]
    assert "display: none" in rules[".dropdown-menu .divider"]


def test_edit_shelf_is_beside_page_heading_not_in_actions_menu():
    layout = read(TEMPLATES / "layout.html")
    assert re.search(r'</h1>\s*{% block page_title_actions %}', layout)
    shelf = read(TEMPLATES / "shelf.html")
    actions = re.search(r'{% block page_title_actions %}(.*?){% endblock %}', shelf, flags=re.S)
    assert actions and 'id="edit_shelf"' in actions.group(1)
    assert "glyphicon-pencil" in actions.group(1)
    assert 'aria-label="{{ _(\'Edit Shelf\') }}"' in actions.group(1)
    assert shelf.count('id="edit_shelf"') == 1
    assert 'id="shelf-menu-toggle"' not in shelf
    assert 'id="delete_shelf"' not in shelf
    edit = read(TEMPLATES / "shelf_edit.html")
    assert re.search(r'<button[^>]*type="button"[^>]*id="delete_shelf"', edit)
    assert "shelf.delete_shelf" in edit
    assert "delete_confirm_modal()" in edit


def test_shelf_heading_edit_action_preserves_permissions():
    from types import SimpleNamespace

    from jinja2 import DictLoader, Environment

    env = Environment(loader=DictLoader({
        "shelf.html": read(TEMPLATES / "shelf.html"),
        "layout.html": '<h1>{{ title }}</h1>{% block page_title_actions %}{% endblock %}',
        "image.html": "",
    }))
    for authenticated, public, editor, visible in (
        (True, False, False, True), (True, True, True, True),
        (True, True, False, False), (False, False, True, False),
        (False, True, True, False),
    ):
        html = env.get_template("shelf.html").render(
            title="Shelf: Papers",
            current_user=SimpleNamespace(is_authenticated=authenticated, role_edit_shelfs=lambda: editor),
            shelf=SimpleNamespace(id=7, is_public=public),
            _=lambda text: text,
            url_for=lambda endpoint, **kwargs: "/shelf/edit/7",
        )
        assert ('id="edit_shelf"' in html) is visible
        if visible:
            assert html.index("Shelf: Papers") < html.index('id="edit_shelf"')
            assert 'href="/shelf/edit/7"' in html
def test_search_suggestions_open_the_picked_book_or_author():
    js = read(JS / "lily.js")
    block = js[js.index("Top bar search"):js.index("Colour theme button")]
    assert 'name: "authors"' in block and "window.location.href = item.url" in block
    assert "minLength: MIN_LENGTH" in block and "MIN_LENGTH = 2" in block
    assert "data-label-authors" in read(TEMPLATES / "layout.html")


def test_series_grid_is_a_css_grid_without_isotope():
    assert "isotope" not in read(JS / "filter_grid.js")
    css = read(CSS / "lily-library.css")
    assert re.search(r"\n\.lily-series-grid\s*\{[^}]*display:\s*grid", css)
    assert "float: left" not in css[css.index("Series grid (grid.html)"):css.index("Continue reading row")]
