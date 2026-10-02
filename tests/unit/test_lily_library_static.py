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
    # Read is the one Primary and wears its word; the other actions are icon buttons.
    # The reader always opens in a new tab.
    m = re.search(r"<a id=\"readbtn\"[^>]*>.*?</a>", html, flags=re.S)
    assert m, "readbtn anchor missing"
    read_btn = m.group(0)
    assert 'target="_blank" rel="noopener"' in read_btn
    # So does the read button on a grid cover.
    assert 'window.open(url, "_blank", "noopener")' in read(JS / "lily.js")

    assert 'class="btn btn-primary"' in read_btn
    assert 'class="book-action-label">' in read_btn
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
    # Download is an icon too, its name hidden like the rest.
    for download in re.findall(r'<(?:a|button)[^>]*id="download(?:btn|Menu)"[^>]*>(.*?)</(?:a|button)>', html, flags=re.S):
        assert 'class="book-action-label sr-only"' in download
    assert "caret" not in html
    css = read(CSS / "lily-library.css")
    square = re.search(r"\.book-action-bar > \.btn\.is-icon,\s*\.book-action-bar > \.dropdown > \.btn\.is-icon \{([^}]*)\}", css)
    assert square and "width: 100%" in square.group(1)
    # The icons fill the column in equal shares, at every width.
    share = re.search(r"^\.book-action-bar > \.btn\.is-icon \{([^}]*)\}", css, flags=re.M)
    assert share and "flex: 1 1 0" in share.group(1)


def test_detail_rare_actions_are_icon_buttons_not_a_menu():
    html = read(TEMPLATES / "detail.html")
    assert "book-more" not in html and "More actions" not in html
    # Copying the UUID was dropped: an internal id isn't a book action.
    assert "uuid-copy" not in html and "Copy UUID" not in html
    for needle, label in (('id="delete"', "Delete book"),):
        btn = re.search(r'<button[^>]*' + re.escape(needle) + r'[^>]*>', html, flags=re.S)
        assert btn, needle
        assert "aria-label=\"{{ _('" + label + "') }}\"" in btn.group(0)
    # No archive, Keep offline or Shelves button: shelves are changed on the edit page
    for gone in ("toggle-archive-btn", "keep-offline-btn", "book-shelves-btn"):
        assert gone not in html


def test_detail_description_has_no_heading_and_shows_in_full():
    html = read(TEMPLATES / "detail.html")
    section = re.search(r'<section class="book-detail-description"(.*?)</section>', html, flags=re.S).group(0)
    assert "<h3" not in section and "aria-label=\"{{ _('Description') }}\"" in section
    assert 'class="comments"' in section
    assert "is-clamped" not in html and "book-description-toggle" not in html
    for selector, body in css_rules(read(CSS / "lily-library.css")):
        if ".book-detail-description" in selector:
            assert "line-clamp" not in body, selector


def test_detail_page_does_not_show_tags_or_shelves():
    html = read(TEMPLATES / "detail.html")
    assert 'class="tags"' not in html and "entry.tags" not in html and "data='category'" not in html
    assert 'class="shelves"' not in html and "book-shelves-row" not in html and "books_shelfs" not in html
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
    for decl in ("display: inline-flex", "height: 44px"):
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


def test_page_numbers_sit_in_the_toolbar_after_sort():
    assert 'class="pagination"' not in read(TEMPLATES / "layout.html")
    for name in ("index.html", "author.html", "search.html", "shelf.html"):
        html = read(TEMPLATES / name)
        toolbar = html[html.index('<div class="lily-list-toolbar">'):]
        toolbar = toolbar[:toolbar.index("image.list_head()")]
        assert "image.pager(pagination)" in toolbar, name
        if "image.sort_menu(" in toolbar:
            assert toolbar.index("image.sort_menu(") < toolbar.index("image.pager("), name
    rules = dict(css_rules(read(CSS / "lily-library.css")))
    assert "flex-direction: row" in rules[".lily-list-toolbar:has(> .pagination)"]
    chips = [body for sel, body in css_rules(read(CSS / "lily-library.css"))
             if sel == ".lily-list-toolbar .pagination > li > a"]
    assert any("height: 38px" in body for body in chips)
    assert any("min-width: 38px" in body for body in chips)
    # page numbers only: no Previous or Next
    macro = read(TEMPLATES / "image.html")
    macro = macro[macro.index("{% macro pager("):]
    macro = macro[:macro.index("{%- endmacro %}")]
    for gone in ("page-previous", "page-next", 'rel="prev"', 'rel="next"', "page-step-word"):
        assert gone not in macro, gone
    assert "page-step-word" not in read(CSS / "lily-library.css")


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
    assert "grid-template-columns: clamp(240px, 19vw, 340px) minmax(min(100%, 420px), max-content) minmax(260px, 1fr)" in main_rules[0]
    assert "grid-template-rows: min-content min-content 1fr auto" in main_rules[0]
    # Wide: the panel sits beside the cover only; the description runs the full width under both.
    extra = next(body for selector, body in css_rules(css) if selector == ".book-detail-extra")
    assert "grid-row: 4" in extra and "grid-column: 1 / -1" in extra
    description = next(body for selector, body in css_rules(css) if selector == ".book-detail-description .comments")
    assert "max-width" not in description
    # Phone: row sizing is reset.
    assert "grid-template-rows: auto" in main_rules[-1]
    metadata = next(body for selector, body in css_rules(css) if selector == "dl.book-metadata")
    for declaration in ("grid-row: 1 / 4", "grid-column: 3", "padding: 20px 22px", "border: 0", "border-radius: 10px",
                        "background: var(--surface)", "margin: 0"):
        assert declaration in metadata
    # One column of facts at every width
    assert "grid-template-columns: minmax(0, 1fr)" in metadata
    assert not any("book-metadata" in selector and "repeat(" in body for selector, body in css_rules(css))
    html = read(TEMPLATES / "detail.html")
    # The panel is its own grid item, not tucked under the description.
    assert html.index('<dl class="book-metadata">') < html.index('<div class="book-detail-extra">')


def test_site_has_no_horizontal_separator_borders():
    names = ["style.css", "lily.css", "lily-shell.css", "lily-library.css",
             "lily-admin.css", "lily-stats.css", "lily-reader.css",
             "login.css"]
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


def test_editor_adds_only_title_and_authors_by_hand_and_shows_other_fields_once_fetched():
    template = read(TEMPLATES / "book_edit.html")
    for key in ("series", "publisher", "pubdate", "languages", "rating", "tags", "comments"):
        assert f'data-optional="{key}"{{% if not shown.{key} %}} hidden{{% endif %}}' in template
    # No "Add …" for any of them: only authors and shelves have an Add button
    assert "data-optional-add" not in template and "editbook-add-fields" not in template
    assert 'id="tag-add"' not in template
    assert re.findall(r'<button type="button" class="btn btn-default btn-sm" id="([^"]+)"', template) == [
        "author-add", "shelf-add"]
    # Title, authors and shelves always show; Details hides with its heading when it is empty
    for always in ('id="title"', 'id="author-rows"', 'id="shelf-rows"'):
        assert always in template
    assert '<section class="editbook-section"{% if not details_shown %} hidden{% endif %}>' in template
    edit_js = read(JS / "edit_books.js")
    assert '$form.on("lily:reveal-filled", function () {' in edit_js
    assert '.closest("section[hidden]").prop("hidden", false)' in edit_js
    assert 'add: $(), fixed: true' in edit_js and "data-optional-add" not in edit_js
    css = read(CSS / "lily-library.css")
    assert ".editbook-section[hidden]" in css and "editbook-add-fields" not in css
    assert '$("#book_edit_frm").trigger("lily:reveal-filled");' in read(JS / "get_meta.js")


def test_fetch_metadata_search_is_a_field_and_a_separate_button():
    template = read(TEMPLATES / "book_edit.html")
    form = template.split('<form class="padded-bottom" id="meta-search">', 1)[1].split("</form>", 1)[0]
    assert "input-group" not in form
    css = read(CSS / "lily-library.css")
    assert "#metaModal #meta-search { order: 3; flex: 1 0 100%; display: flex; gap: 8px;" in css


def test_fetch_metadata_opens_beside_the_cover():
    js = read(JS / "get_meta.js")
    assert "cover.getBoundingClientRect().right / zoom + 24" in js
    assert 'dialog.classList.toggle("meta-beside-cover", fits);' in js
    css = read(CSS / "lily-library.css")
    assert "#metaModal .modal-dialog.meta-beside-cover {" in css
    assert "margin-left: var(--meta-left);" in css


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


def test_read_toggles_flip_their_label_without_aria_pressed():
    # docs/design.md §5.1: a label that names the next action and aria-pressed contradict each
    # other ("Mark as unread, pressed"), so these toggles show their state with .is-on.
    html = read(TEMPLATES / "detail.html")
    for btn_id, state in (("toggle-read-btn", "entry.read_status"),):
        tag = re.search(r'<button[^>]*id="%s"[^>]*>' % btn_id, html, flags=re.S).group(0)
        assert "aria-pressed" not in tag, btn_id
        assert "class=\"btn is-icon{{ ' is-on' if %s }}\"" % state in tag, btn_id
    assert '"aria-pressed"' not in html
    assert '$btn.toggleClass("is-on", isRead)' in html
    image = read(TEMPLATES / "image.html")
    toggle = re.search(r'<button[^>]*lily-toggle-read[^>]*>', image, flags=re.S).group(0)
    assert "aria-pressed" not in toggle and "is-on" in toggle
    js = read(JS / "lily.js")
    handler = js[js.index('".lily-cover-actions .lily-toggle-read"'):]
    handler = handler[:handler.index("}).fail(")]
    assert "aria-pressed" not in handler and '$btn.toggleClass("is-on", nowRead)' in handler
    # The read look (success disc on the cover, success tint on the book page) hangs off .is-on.
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


def test_installed_app_opens_the_reader_in_place():
    # §6.7: a new tab would leave an installed (standalone) app for the browser.
    js = read(JS / "lily.js")
    assert 'matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true' in js
    assert '$("a[data-reader-link]")' in js and 'this.removeAttribute("target")' in js
    handler = js[js.index('".lily-cover-actions .lily-read-now"'):]
    assert handler.index("window.location.href = url") < handler.index('window.open(url, "_blank", "noopener")')
    for name, needle in (("detail.html", 'id="readbtn"'), ("book_edit.html", 'id="readbtn"'),
                         ("index.html", 'class="book-cover-link"')):
        html = read(TEMPLATES / name)
        start = html.rindex("<a", 0, html.index(needle))
        tag = html[start:html.index(">", html.index(needle))]
        assert "data-reader-link=" in tag, name


def test_wide_page_covers_keep_their_left_margin():
    # §5.15: Letter pages are wider than the A4 cover box; a centred crop cut arXiv's
    # left-margin stamp in half, so the trim comes off the right margin instead.
    css = read(CSS / "lily-library.css")
    grid = [b for s, b in css_rules(css) if ".continue-reading-item .cover img" in s]
    assert grid and "object-fit: cover" in grid[0] and "object-position: left center" in grid[0]
    order = [b for s, b in css_rules(css) if s == ".lily-order-cover"]
    assert order and "object-position: left center" in order[0]


def test_covers_much_wider_than_a4_are_shown_whole():
    # §5.15: a 3:4 publisher cover prints words near its edge; a fill crop cut them off.
    css = read(CSS / "lily-library.css")
    wide = [b for s, b in css_rules(css) if ".lily-book .cover img.cover-wide" in s]
    assert wide and "object-fit: contain" in wide[0]
    assert '"cover-wide"' in read(JS / "lily.js")


def test_book_row_series_number_is_not_a_hidden_author_line():
    # Grid cards hide .meta .author; the book page's "Book N" must not use that class.
    image = read(TEMPLATES / "image.html")
    row = image[image.index("{% macro book_row"):]
    row = row[:row.index("{%- endmacro %}")]
    assert 'class="related-number"' in row and 'class="author"' not in row
    css = read(CSS / "lily-library.css")
    assert ".continue-reading-item .meta .continue-reading-percent,\n.continue-reading-item .meta .related-number {" in css


def test_editor_keeps_the_authors_in_the_books_own_order():
    # The first author names the book and its folder; sorting the rows A-Z on any edit made
    # "Bond, Gauthier, Strokorb" save as "Gauthier, Bond, Strokorb" and moved the folder
    edit_js = read(JS / "edit_books.js")
    assert "localeCompare" not in edit_js and "opts.sort" not in edit_js
    authors = edit_js.split('field: $("#authors")', 1)[1].split("});", 1)[0]
    assert "sort" not in authors


def test_empty_shelf_says_how_books_get_there_now():
    # The book page's Shelves menu is gone (shelves change on the edit page); the empty
    # state sent people to it
    template = read(TEMPLATES / "shelf.html")
    assert "Shelves menu" not in template
    assert "choose Edit metadata and add this shelf under Shelves" in template


def test_fetch_result_cover_has_no_size_badge():
    # The size pill over each result's cover was removed: no text over the art
    assert "image-dimensions" not in read(TEMPLATES / "book_edit.html")
    assert "image-dimensions" not in read(CSS / "lily-library.css")


def test_small_copy_fixes_stay_fixed():
    assert "<title>{{ instance }} | {{ error_name }}</title>" in read(TEMPLATES / "http_error.html")
    assert "ngettext('%(num)s Result for “%(term)s”'" in read(TEMPLATES / "search.html")
    readpdf = read(TEMPLATES / "readpdf.html")
    assert "app.setTitle = function () { document.title = {{ title|tojson }}; };" in readpdf
    assert 'data.url && data.count > 0' in read(TEMPLATES / "detail.html")


def test_the_book_table_is_reachable_from_the_view_switch():
    # /table (bulk shelve, mark read, delete) had no link anywhere
    switch = read(TEMPLATES / "image.html").split("{% macro view_switch", 1)[1].split("{%- endmacro %}", 1)[0]
    assert "url_for('web.books_table')" in switch and "Edit many books at once" in switch
