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
    # Actions are labelled buttons; Read is the one Primary.
    # The reader always opens in a new tab.
    m = re.search(r"<a id=\"readbtn\"[^>]*>.*?</a>", html, flags=re.S)
    assert m, "readbtn anchor missing"
    read_btn = m.group(0)
    assert 'target="_blank" rel="noopener"' in read_btn
    # So does the read button on a grid cover.
    assert 'window.open(url, "_blank", "noopener")' in read(JS / "lily.js")
    assert 'class="btn btn-primary"' in read_btn
    assert "url_for('web.read_book'" in read_btn
    assert "{{ _('Read') }}" in read_btn
    # A book in progress offers to continue, with how far in it is.
    assert "{{ _('Continue') }}" in read_btn and "resume.percent" in read_btn
    assert html.count("btn-primary") == 1
    assert "btn-danger" not in html


def test_detail_edit_and_read_state_are_named_icon_buttons():
    html = read(TEMPLATES / "detail.html")
    edit = re.search(r'<a href="[^"]*show_edit_book[^"]*" id="edit_book" class="btn is-icon"[^>]*>', html, flags=re.S)
    assert edit, "Edit Metadata icon button missing"
    assert "aria-label=\"{{ _('Edit metadata') }}\"" in edit.group(0)
    toggle = re.search(r'<button[^>]*id="toggle-read-btn"[^>]*>(.*?)</button>', html, flags=re.S)
    assert toggle and 'class="btn is-icon' in toggle.group(0)
    assert 'class="book-action-label sr-only"' in toggle.group(1)
    css = read(CSS / "lily-library.css")
    assert "width: 36px" in _rules_by_selector(css, ".book-action-bar > .btn.is-icon")[".book-action-bar > .btn.is-icon"]


def test_detail_rare_actions_are_icon_buttons_not_a_menu():
    html = read(TEMPLATES / "detail.html")
    assert "book-more" not in html and "More actions" not in html
    # Copying the UUID was dropped: an internal id isn't a book action.
    assert "uuid-copy" not in html and "Copy UUID" not in html
    for needle, label in (('id="delete"', "Delete book"),):
        btn = re.search(r'<button[^>]*' + re.escape(needle) + r'[^>]*>', html, flags=re.S)
        assert btn, needle
        assert "aria-label=\"{{ _('" + label + "') }}\"" in btn.group(0)
    archive = re.search(r'<button[^>]*id="toggle-archive-btn"[^>]*>(.*?)</button>', html, flags=re.S)
    assert archive and 'class="btn is-icon' in archive.group(0)
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


def test_continue_reading_progress_sits_on_the_cover_and_its_share_under_the_author():
    html = read(TEMPLATES / "index.html")
    cover = re.search(r'<div class="cover">(.*?)\n      </div>', html, flags=re.S).group(1)
    meta = re.search(r'<div class="meta">(.*?)\n      </div>', html, flags=re.S).group(1)
    assert "continue-reading-progress" in cover and "continue-reading-progress" not in meta
    assert 'class="continue-reading-percent"' in meta and "% read" in meta


def test_continue_reading_opens_the_reader_in_a_new_tab():
    html = read(TEMPLATES / "index.html")
    cover = re.search(r'<div class="cover">(.*?)\n      </div>', html, flags=re.S).group(1)
    # Only the reader link opens a new tab; a book with no readable format opens its page in place.
    assert '{% if resume_format %}target="_blank" rel="noopener"' in cover
    assert "Continue reading %(title)s (opens in a new tab)" in cover


def test_continue_reading_is_one_scrolling_row():
    body = re.search(r"^\.continue-reading-row \{([^}]*)\}", read(CSS / "lily-library.css"), flags=re.M).group(1)
    assert "display: flex" in body and "overflow-x: auto" in body and "grid-template-columns" not in body


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
    sel = '.book-action-bar #toggle-read-btn.is-on .glyphicon'
    parts = _rules_by_selector(read(CSS / "lily-library.css"), ".book-action-bar #toggle-read-btn")
    assert "color: var(--success)" in parts[sel]
    for body in parts.values():
        assert "border-radius: 50%" not in body and "background: var(--success)" not in body
    html = read(TEMPLATES / "detail.html")
    # One glyph for both states (a tick in a circle); the colour shows which.
    assert 'id="read-icon" class="glyphicon glyphicon-ok-circle"' in html
    assert "eye-open" not in html


def test_lily_library_css_uses_tokens_only():
    css = re.sub(r"/\*.*?\*/", "", read(CSS / "lily-library.css"), flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    for banned in ("rgba(", "hsla(", "gradient", "backdrop-filter", "blur("):
        assert banned not in css, banned


def test_lily_library_css_shadows_only_on_menus():
    for selector, body in css_rules(read(CSS / "lily-library.css")):
        for value in re.findall(r"box-shadow\s*:\s*([^;]+)", body):
            if value.strip() != "none":
                # Floating layers only (design §4.4): menus, and the cover's round quick actions.
                assert "tt-menu" in selector or "dropdown-menu" in selector or selector == ".lily-cover-actions .icon-btn", selector


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
    for needle in ("lily-toggle-read", "lily-read-now", "/ajax/toggleread/"):
        assert needle in js, needle


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
    assert "grid-template-rows: min-content min-content 1fr auto" in main_rules[0]
    # Wide: the description runs under the cover, up to the panel.
    extra = next(body for selector, body in css_rules(css) if selector == ".book-detail-extra")
    assert "grid-row: 4" in extra and "grid-column: 1 / 3" in extra
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
    assert re.search(r'</h1>(?:{% endif %})?\s*{% block page_title_actions %}', layout)
    shelf = read(TEMPLATES / "shelf.html")
    actions = re.search(r'{% block page_title_actions %}(.*?){% endblock %}', shelf, flags=re.S)
    assert actions and 'id="edit_shelf"' in actions.group(1)
    assert "glyphicon-pencil" in actions.group(1)
    assert 'aria-label="{{ _(\'Edit shelf\') }}"' in actions.group(1)
    assert shelf.count('id="edit_shelf"') == 1
    assert 'id="shelf-menu-toggle"' not in shelf
    assert 'id="delete_shelf"' not in shelf
    edit = read(TEMPLATES / "shelf_edit.html")
    assert re.search(r'<button[^>]*type="button"[^>]*id="delete_shelf"', edit)
    assert "shelf.delete_shelf" in edit
    assert "delete_confirm_modal()" in edit
    # One card and one bar: delete is a quiet button at the left of the bar, not its own card.
    actions = re.search(r'<div class="lp-actions">(.*?)</div>', edit, re.S).group(1)
    assert actions.index('id="delete_shelf"') < actions.index("lp-spacer") < actions.index('id="submit"')
    assert "section-delete" not in edit
    assert "shelf.order_shelf" in edit
    admin_css = read(TEMPLATES.parent / "static" / "css" / "lily-admin.css")
    assert re.search(r"\.lp-shelf-edit\s*\{[^}]*max-width:\s*640px", admin_css)


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


def test_grid_covers_have_no_popups():
    # docs/design.md §5.6: nothing pops up over a grid cover or its quick-action buttons;
    # the buttons keep an aria-label for screen readers.
    image = read(TEMPLATES / "image.html")
    actions = re.search(r"{% macro cover_actions.*?{%- endmacro %}", image, flags=re.S).group(0)
    assert "title=" not in actions
    assert actions.count("aria-label=") >= 4
    for name in ("image.html", "index.html", "grid.html"):
        html = read(TEMPLATES / name)
        assert not re.search(r'<span class="img"[^>]*title=', html), name
        assert not re.search(r'<span class="badge[^"]*"[^>]*title=', html), name
    js = read(JS / "lily.js")
    assert '$btn.attr({ title:' not in js
    assert 'attr("title", $btn.data("label-read"))' not in js


def test_cover_quick_actions_are_floating_round_buttons():
    # docs/design.md §6.3: round frosted discs at the cover's bottom right, not a bar across its foot.
    css = read(CSS / "lily-library.css")
    bar = next(body for sel, body in css_rules(css) if sel == ".lily-cover-actions")
    assert "--btn-radius: 50%" in bar and "right: 8px" in bar and "bottom: 8px" in bar
    assert "left: 0" not in bar and "--cover-tint" not in css
    assert "--cover-tint" not in read(JS / "lily.js")


def test_editor_hides_empty_optional_fields_until_added_or_fetched():
    template = read(TEMPLATES / "book_edit.html")
    for key in ("series", "publisher", "pubdate", "languages", "rating"):
        assert f'data-optional="{key}"{{% if not shown.{key} %}} hidden{{% endif %}}' in template
        assert f"('{key}', _('Add " in template
    # Title, authors, tags, shelves and description always show
    for always in ('id="title"', 'id="author-rows"', 'id="tag-rows"', 'id="shelf-rows"', 'id="comments"'):
        assert always in template
    edit_js = read(JS / "edit_books.js")
    assert '$form.on("lily:reveal-filled", function () {' in edit_js
    assert '$("#book_edit_frm").trigger("lily:reveal-filled");' in read(JS / "get_meta.js")


def test_editor_save_panel_starts_with_read():
    template = read(TEMPLATES / "book_edit.html")
    panel = template.split('<aside class="editbook-actions"', 1)[1].split("</aside>", 1)[0]
    assert panel.index('class="btn btn-default editbook-read"') < panel.index('id="submit"')
    assert "url_for('web.read_book', book_id=book.id, book_format=reader_list[0])" in panel
    assert "reader_list=helper.check_read_formats(book)" in read(REPO_ROOT / "cps/editbooks.py")


def test_delete_dialog_names_the_book_and_promises_no_restore():
    dialogs = read(TEMPLATES / "modal_dialogs.html")
    assert "administrator to restore" not in dialogs and "Are You Sure?" not in dialogs
    assert 'class="delete-title"' in dialogs and "can’t be undone in Lily" in dialogs
    for name in ("detail.html", "book_edit.html"):
        html = read(TEMPLATES / name)
        for opener in re.findall(r'<button[^>]*data-target="#deleteModal"[^>]*>', html):
            assert "data-delete-title=" in opener, (name, opener)
    js = read(REPO_ROOT / "cps/static/js/main.js")
    assert 'data("delete-title")' in js


def test_book_title_is_the_page_h1_and_the_top_bar_has_none():
    # docs/design.md §6.1: the book title is the book page's h1; the top bar renders no
    # (empty) h1 there. The title keeps its 40px/700 content look (§3.1).
    html = read(TEMPLATES / "detail.html")
    assert re.search(r'<h1 id="title">{{ entry.title }}</h1>', html)
    assert '<h2 id="title">' not in html
    layout = read(TEMPLATES / "layout.html")
    assert ("{% if page != 'book' %}<h1 class=\"lily-page-title\">{% block page_title %}{{ title }}"
            "{% endblock %}</h1>{% endif %}") in layout
    css = read(CSS / "lily-library.css")
    assert "h2#title" not in css
    title = [b for s, b in css_rules(css) if s == ".book-detail-meta h1#title"]
    assert title and "font-size: 40px" in title[0] and "font-weight: 700" in title[0]
    assert "font-size: 23px" in "".join(title[1:])  # phone size


def test_grid_quick_actions_name_their_book():
    # docs/design.md §9: "Read Quiet Machines", not five identical "Read in browser" buttons.
    image = read(TEMPLATES / "image.html")
    actions = re.search(r"{% macro cover_actions.*?{%- endmacro %}", image, flags=re.S).group(0)
    for msgid in ("Read %(name)s", "Download %(name)s", "Mark %(name)s as unread",
                  "Mark %(name)s as read", "Edit %(name)s"):
        assert "_('%s', name=book.title)" % msgid in actions, msgid
    assert "Read in browser" not in actions and "_('Edit metadata')" not in actions


def test_read_and_archive_toggles_flip_their_label_without_aria_pressed():
    # docs/design.md §5.1: a label that names the next action and aria-pressed contradict each
    # other ("Mark as unread, pressed"), so these toggles show their state with .is-on.
    html = read(TEMPLATES / "detail.html")
    for btn_id, state in (("toggle-read-btn", "entry.read_status"), ("toggle-archive-btn", "entry.is_archived")):
        tag = re.search(r'<button[^>]*id="%s"[^>]*>' % btn_id, html, flags=re.S).group(0)
        assert "aria-pressed" not in tag, btn_id
        assert "class=\"btn is-icon{{ ' is-on' if %s }}\"" % state in tag, btn_id
    assert '"aria-pressed"' not in html
    assert '$btn.toggleClass("is-on", isRead)' in html and '$btn.toggleClass("is-on", isArchived)' in html
    image = read(TEMPLATES / "image.html")
    toggle = re.search(r'<button[^>]*lily-toggle-read[^>]*>', image, flags=re.S).group(0)
    assert "aria-pressed" not in toggle and "is-on" in toggle
    js = read(JS / "lily.js")
    handler = js[js.index('".lily-cover-actions .lily-toggle-read"'):]
    handler = handler[:handler.index("}).fail(")]
    assert "aria-pressed" not in handler and '$btn.toggleClass("is-on", nowRead)' in handler
    # The read look (success disc on the cover, success tint on the book page) and the archive
    # look (chosen chip) hang off .is-on now.
    library = read(CSS / "lily-library.css")
    assert "toggle-read-btn[aria-pressed" not in library and "lily-toggle-read[aria-pressed" not in library
    assert ".lily-cover-actions .icon-btn[aria-pressed" not in library
    disc = [b for s, b in css_rules(library) if s.startswith(".lily-cover-actions .icon-btn.is-on")]
    assert disc and "background: var(--success)" in disc[0] and "color: var(--surface)" in disc[0]
    lily = css_rules(read(CSS / "lily.css"))
    assert any(".btn.is-on" in s and "var(--accent-soft)" in b for s, b in lily)
    assert any(".icon-btn.is-on" in s and "var(--control-tint-strong)" in b for s, b in lily)


def test_book_views_hide_the_unknown_author_placeholder():
    for name in ["detail.html", "image.html", "index.html", "listenmp3.html", "shelf_order.html"]:
        assert "|named_authors" in read(TEMPLATES / name), name
