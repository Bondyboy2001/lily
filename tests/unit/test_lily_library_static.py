"""Static checks that the library pages follow docs/design.md."""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CSS = REPO_ROOT / "cps/static/css"
JS = REPO_ROOT / "cps/static/js"
TEMPLATES = REPO_ROOT / "cps/templates"

LIBRARY_TEMPLATES = [
    "index.html", "list.html", "detail.html", "author.html", "search.html",
    "search_form.html", "shelf.html", "shelf_edit.html", "shelf_order.html",
    "book_edit.html", "image.html", "modal_dialogs.html",
]
SORT_TEMPLATES = ["index.html", "author.html", "search.html", "shelf.html", "list.html"]


def read(path):
    return path.read_text(encoding="utf-8")


def css_rules(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(sel.strip(), body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def test_layout_loads_lily_library_css_after_the_shell():
    layout = read(TEMPLATES / "layout.html")
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
    assert 'class="book-action-label">{{ read_name }}</span>' in read_btn
    assert "_('Read')" in html
    # Always "Read": no Continue and no percent, even for a book in progress
    assert "Continue" not in html and "resume.percent" not in html
    assert html.count("btn-primary") == 1
    assert "btn-danger" not in html


def test_detail_edit_and_read_state_are_named_icon_buttons():
    html = read(TEMPLATES / "detail.html")
    edit = re.search(r'<a href="[^"]*show_edit_book[^"]*" id="edit_book" class="btn is-icon"[^>]*>', html, flags=re.S)
    assert edit, "Edit Metadata icon button missing"
    assert "aria-label=\"{{ _('Edit metadata') }}\"" in edit.group(0)
    # Fetch metadata sits just before Edit and opens the lookup on this page.
    fetch = re.search(r'<button type="button" id="fetch_book_meta" class="btn is-icon" data-toggle="modal" data-target="#metaModal"[^>]*>', html, flags=re.S)
    assert fetch and "aria-label=\"{{ _('Fetch metadata') }}\"" in fetch.group(0)
    assert html.index('id="fetch_book_meta"') < html.index('id="edit_book"')
    toggle = re.search(r'<button[^>]*id="toggle-read-btn"[^>]*>(.*?)</button>', html, flags=re.S)
    assert toggle and 'class="btn is-icon' in toggle.group(0)
    assert 'class="book-action-label sr-only"' in toggle.group(1)
    # Download is an icon too, its name hidden like the rest.
    for download in re.findall(r'<(?:a|button)[^>]*id="download(?:btn|Menu)"[^>]*>(.*?)</(?:a|button)>', html, flags=re.S):
        assert 'class="book-action-label sr-only"' in download
    assert "caret" not in html
    css = read(CSS / "lily-library.css")
    square = re.search(r"\.book-action-bar > \.btn\.is-icon,\s*\.book-action-bar > \.dropdown > \.btn\.is-icon \{([^}]*)\}", css)
    assert square and "width: 44px" in square.group(1)
    # 44px squares beside the labelled Read; on phones they share the line under Read.
    share = re.search(r"^\.book-action-bar > \.btn\.is-icon \{([^}]*)\}", css, flags=re.M)
    assert share and "flex: none" in share.group(1)
    assert re.search(r"\.book-action-bar > \.btn\.btn-primary \{ flex: 1 1 100%; \}", css)
    # Delete sits evenly with the other buttons, no extra gap before it.
    assert not re.search(r"\.book-action-bar > \.btn\.is-danger \{ margin-left", css)


def test_detail_rare_actions_are_icon_buttons_not_a_menu():
    html = read(TEMPLATES / "detail.html")
    assert "book-more" not in html and "More actions" not in html
    # Copying the UUID was dropped: an internal id isn't a book action.
    assert "uuid-copy" not in html and "Copy UUID" not in html
    for needle, label in (('id="delete"', "Delete book"),):
        btn = re.search(r'<button[^>]*' + re.escape(needle) + r'[^>]*>', html, flags=re.S)
        assert btn, needle
        assert "aria-label=\"{{ _('" + label + "') }}\"" in btn.group(0)
    # No archive button, and no offline reading
    for gone in ("toggle-archive-btn", "keep-offline-btn", "offline-btn"):
        assert gone not in html
    shelves = re.search(r'<button[^>]*id="book-shelves-btn"[^>]*>', html, flags=re.S)
    assert shelves and 'class="btn is-icon dropdown-toggle"' in shelves.group(0)
    assert "aria-label=\"{{ _('Shelves') }}\"" in shelves.group(0)


def test_detail_page_has_no_description():
    # Lily keeps no descriptions: no pull-quote on the page, none in the hidden save form
    html = read(TEMPLATES / "detail.html")
    assert "book-detail-description" not in html and "book-detail-extra" not in html
    assert "comments" not in html and "og:description" not in html
    assert "book-detail-description" not in read(CSS / "lily-library.css")


def test_detail_page_names_the_shelves_the_book_is_on():
    # §6.4: a line under the action bar, the Shelves icon then a link to each shelf the book
    # is on (no "On" label); the Shelves menu shows or hides a shelf's link in place.
    html = read(TEMPLATES / "detail.html")
    line = re.search(r'<p class="book-on-shelves" id="book-on-shelves".*?</p>', html, flags=re.S)
    assert line and "shelf.show_shelf" in line.group(0) and "{% if not on %} hidden" in line.group(0)
    assert "_('On')" not in line.group(0) and "_('Shelves')" in line.group(0)
    assert html.index('id="book-on-shelves"') > html.index('class="book-action-bar"')
    js = read(JS / "shelves.js")
    assert "updateOnShelves($item.data(\"shelf-url\"), data.on)" in js
    css = read(CSS / "lily-library.css")
    assert re.search(r"\.book-on-shelves a:not\(\[hidden\]\) ~ a:not\(\[hidden\]\)::before", css)
    # A hidden shelves line (book on no shelf) must not pull the fetched line up to 6px
    assert ".book-on-shelves:not([hidden]) + .book-fetched-from" in css


def test_detail_page_does_not_show_tags():
    html = read(TEMPLATES / "detail.html")
    # The hidden form Fetch Metadata saves through carries the tags, unseen
    html = re.sub(r'<form [^>]*id="book_edit_frm" hidden>.*?</form>', "", html, flags=re.S)
    assert 'class="tags"' not in html and "entry.tags" not in html and "data='category'" not in html
    assert "is-tag" not in html and "is-tag" not in read(CSS / "lily-library.css")


def test_library_has_no_continue_reading_row():
    html = read(TEMPLATES / "index.html")
    assert "continue_reading" not in html and "Continue Reading" not in html


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
    # One glyph for both states (an eye); the colour shows which.
    assert 'id="read-icon" class="glyphicon glyphicon-eye-open"' in html
    assert "ok-circle" not in html


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
        if name == "list.html":
            # this page has no server-side sort: order and letter filter live in list_menu
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
        toolbar = toolbar[:toolbar.index('<div class="row display-flex lily-grid')]
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
    # No Isotope anywhere: main.js lays out nothing, and the plugin bundle is gone.
    assert "isotope" not in read(JS / "main.js")


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


def test_detail_page_is_a_frontispiece_stage():
    # docs/design.md §6.4: a --sunk stage (cover plate | heading, actions), then the details dialog.
    css = read(CSS / "lily-library.css")
    rules = css_rules(css)
    main_rules = [body for selector, body in rules if selector == ".book-detail-main"]
    assert "grid-template-columns: clamp(240px, 20vw, 312px) minmax(0, 1fr)" in main_rules[0]
    assert "background: var(--sunk)" in main_rules[0] and "border-radius: 10px" in main_rules[0]
    assert "box-shadow" not in main_rules[0]
    # Phone: one centred column, row sizing reset.
    assert "grid-template-rows: auto" in main_rules[-1] and "minmax(0, 1fr)" in main_rules[-1]
    plate = next(body for selector, body in rules if selector == ".book-detail-cover")
    # The bare cover: no mount, so no whitespace round it.
    assert "background" not in plate and "padding" not in plate and "box-shadow" not in plate
    # The plate takes the cover's shape (no letterbox); a deformed cover is cropped at 1:1.6.
    art = next(body for selector, body in rules
               if selector.split(",")[-1].strip() == ".book-detail-cover-art")
    assert "aspect-ratio" not in art and "container-type: inline-size" in art
    art_img = next(body for selector, body in rules
                   if selector.split(",")[-1].strip() == ".book-detail-cover-art img")
    assert "height: auto" in art_img and "max-height: 160cqw" in art_img
    assert "object-fit: cover" in art_img and "contain" not in art_img
    html = read(TEMPLATES / "detail.html")
    stage = html[html.index('<div class="book-detail-main">'):html.index('<div class="meta-actions" hidden>')]
    assert 'id="readbtn"' in stage
    extra = html[html.index('<div class="meta-actions" hidden>'):html.index('id="bookInfoModal"')]
    # No related rows (they were the rest of a series); the lookup lives in the details dialog.
    assert "related-series-heading" not in html and "book_row" not in html
    assert "book-metadata-lookup" not in stage and "book-metadata-lookup" not in extra
    assert "book-record" not in html
    # Date added and Last edited live in the details dialog, opened from the action bar.
    assert "book-dates" not in html and "entry.timestamp" not in stage
    assert 'id="book-info-btn"' in stage and 'data-target="#bookInfoModal"' in stage
    dialog = html[html.index('id="bookInfoModal"'):]
    assert '<dl class="book-info">' in dialog
    assert dialog.index("entry.timestamp|formatdate") < dialog.index("entry.last_modified|formatdate")
    assert dialog.index('<div class="book-metadata-lookup">') < dialog.index("_('Book ID')")
    # The plate wears the grid card's marks (ribbons, Finished, Fetched) and its green read edge.
    plate = stage[stage.index('{# The plate wears'):stage.index('<div class="book-detail-head">')]
    assert "metadata_lookup.status == 'matched'" in plate
    assert "image.cover_marks(entry.data|map(attribute='format')|map('lower')|list, entry.read_status, fetched, fetched_id='book-fetched-dot')" in plate
    assert "{{ ' is-read' if entry.read_status }}" in plate
    edge = next(body for selector, body in rules if selector == ".book-detail-cover.is-read .book-detail-cover-art::after")
    assert "outline: 3px solid var(--success)" in edge
    dot = next(body for selector, body in rules if selector == ".lily-fetched")
    assert "position: absolute" in dot and "background: var(--success)" in dot
    # A flush corner square, not a ringed disc.
    assert "border-radius: 0 8px 0 6px" in dot and "border:" not in dot
    assert "top: 0" in dot and "right: 0" in dot
    assert "position: relative" in next(body for selector, body in rules if selector == ".book-detail-cover")
    info = next(body for selector, body in rules if selector == "dl.book-info")
    assert "display: grid" in info and "border" not in info


def test_detail_page_has_no_fact_tags():
    # The file, date and arXiv link live in the details dialog, not under the byline.
    assert "book-fact" not in read(TEMPLATES / "detail.html")
    assert "book-fact" not in read(CSS / "lily-library.css")


def test_site_has_no_horizontal_separator_borders():
    names = ["style.css", "lily.css", "lily-shell.css", "lily-library.css",
             "lily-admin.css", "lily-duplicates.css", "lily-reader.css",
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
    for name in ("image.html", "index.html"):
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


def test_editor_shows_every_field_even_when_blank():
    # Design §6.4: nothing hides while empty but Edition and Volume, behind their Add buttons
    template = read(TEMPLATES / "book_edit.html")
    assert "data-optional" not in template and "shown." not in template and "details_shown" not in template
    assert "editbook-section\"{%" not in template
    assert template.count(" hidden{% endif %}") == 5  # the two fields, the two buttons, their row
    for field in ('id="title"', 'id="edition"', 'id="volume"', 'id="author-rows"',
                  'id="pubdate"', 'id="tag-rows"'):
        assert field in template, field
    # No publisher, language, rating or description: Lily keeps none of them
    for gone in ('id="publisher"', 'id="languages"', "rating_input", 'id="comments"'):
        assert gone not in template, gone
    # Edition and Volume pair under the title, added by hand like authors and tags
    numbers = template.split('<div class="editbook-fields editbook-numbers">', 1)[1].split("{# One row per author", 1)[0]
    assert numbers.index('id="edition"') < numbers.index('id="volume"')
    assert re.findall(r'<button type="button" class="btn btn-default btn-sm" id="([^"]+)"', template) == [
        "edition-add", "volume-add", "author-add", "tag-add"]
    assert 'name="series_index"' not in template
    edit_js = read(JS / "edit_books.js")
    # Tags use the authors' row editor: a field per value, × beside it, Add tag below
    assert 'add: $("#tag-add"),' in edit_js and "chips" not in edit_js
    for gone in ("reveal-filled", "opts.fixed"):
        assert gone not in edit_js, gone
    assert '$("#edition-add, #volume-add").on("click"' in edit_js
    # Fetch Metadata shows the Edition field when it takes an edition off the title
    assert 'trigger("lily:show-number", ["edition"])' in read(JS / "get_meta.js")
    css = read(CSS / "lily-library.css")
    assert "data-optional" not in css and "editbook-publisher" not in css


def test_fetch_metadata_search_is_a_field_and_a_separate_button():
    template = read(TEMPLATES / "meta_fetch.html")
    form = template.split('<form class="padded-bottom" id="meta-search">', 1)[1].split("</form>", 1)[0]
    assert "input-group" not in form
    css = read(CSS / "lily-library.css")
    assert "#metaModal #meta-search { order: 3; flex: 1 0 100%; display: flex; gap: 8px;" in css


def test_fetch_metadata_opens_beside_the_cover():
    js = read(JS / "get_meta.js")
    assert "cover.getBoundingClientRect().right / zoom + 24" in js
    assert 'dialog.classList.toggle("meta-beside-cover", fits);' in js
    # Beside the book page's whole plate, on any window but a phone's
    assert '$(".editbook-cover-section .cover, .book-detail-cover")[0]' in js
    assert "var fits = left > 0 && room >= 440;" in js
    css = read(CSS / "lily-library.css")
    assert "#metaModal .modal-dialog.meta-beside-cover {" in css
    assert "margin-left: var(--meta-left);" in css


def test_editor_layout():
    # Design §6.4: Read under the cover, Delete last in the cover column, Fetch Metadata at
    # the Book heading, and a Save panel of Save and Cancel only
    template = read(TEMPLATES / "book_edit.html")
    side = template.split('<div class="editbook-cover-tools">', 1)[1].split('{# Only the title', 1)[0]
    assert side.index('class="btn btn-default btn-block editbook-read"') < side.index('id="btn-upload-cover"')
    assert side.index('id="btn-upload-cover"') < side.index('<div class="editbook-danger">') < side.index('id="delete"')
    assert "url_for('web.read_book', book_id=book.id, book_format=reader_list[0])" in side
    assert "reader_list=helper.check_read_formats(book)" in read(REPO_ROOT / "cps/editbooks.py")
    head = template.split('<div class="editbook-head">', 1)[1].split("</div>", 1)[0]
    assert "<h3>{{_('Book')}}</h3>" in head and 'id="get_meta"' in head
    panel = template.split('<aside class="editbook-actions"', 1)[1].split("</aside>", 1)[0]
    assert re.findall(r'class="btn [^"]*"', panel) == ['class="btn btn-primary"', 'class="btn btn-default"']
    # The × clears a value; the trash is only for deleting the book or a format
    assert template.count("glyphicon-trash") == 1 and 'glyphicon-trash" aria-hidden="true"></span> {{_("Delete book")}}' in template
    assert 'glyphicon glyphicon-remove", "aria-hidden": "true"' in read(JS / "edit_books.js")
    css = read(CSS / "lily-library.css")
    assert "grid-template-columns: 220px minmax(0, 640px) 220px;" in css
    assert "#tag-rows" not in css


def test_editor_has_no_shelves_section():
    # Shelves change from the book page's Shelves menu. The editor keeps only disabled
    # fields, so a save leaves the shelves alone unless Fetch Metadata files an arXiv paper
    template = read(TEMPLATES / "book_edit.html")
    assert "shelf-rows" not in template and "shelf-add" not in template and "shelves-label" not in template
    assert '<input type="hidden" name="shelves_present" value="1" disabled>' in template
    assert re.search(r'<input type="hidden" name="shelves" id="shelves" value=\'[^\']*\' disabled>', template)
    assert "shelf-rows" not in read(JS / "edit_books.js")
    assert 'set("shelves_present", "1");' in read(JS / "get_meta.js")


def test_native_file_inputs_hide_at_the_element():
    # §9: hidden things leave the tab order. The Replace Cover and top-bar spare file inputs
    # must hide at the element, so whatever stylesheets load they can never render as a
    # native "Choose file" input; the label (or the .btn-file overlay) opens the picker.
    # The inline style is load-bearing: Bootstrap's `input[type=file] { display: block }`
    # would otherwise beat the hidden attribute.
    cover = re.search(r'<input [^>]*id="btn-upload-cover"[^>]*>', read(TEMPLATES / "book_edit.html")).group(0)
    assert " hidden" in cover and 'style="display: none;"' in cover
    assert "#btn-upload-cover" not in re.sub(r"/\*.*?\*/", "", read(CSS / "style.css"), flags=re.S)
    spare = re.search(r'<input [^>]*id="btn-upload2"[^>]*>', read(TEMPLATES / "layout.html")).group(0)
    assert " hidden" in spare and 'style="display: none;"' in spare and 'class="hide"' not in spare


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


def test_deleting_a_book_navigates_only_after_the_post():
    # Passing `location=loc` assigned window.location, which left the page before the delete
    # POST went out. The delete is a posted form; the server redirects once it is done.
    js = read(JS / "main.js")
    assert 'postButton(event, getPath() + "/delete/" + deleteId);' in js
    assert "location=" not in js.replace(" ", "")
    assert "function postButton(event, action){" in js and "newForm.submit();" in js


def test_book_title_is_the_page_h1_and_the_top_bar_has_none():
    # docs/design.md §6.1: the book title is the book page's h1; the top bar renders no
    # (empty) h1 there. The title keeps its 52px/700 display look (§3.1).
    html = read(TEMPLATES / "detail.html")
    assert re.search(r'<h1 id="title">{{ entry.title }}</h1>', html)
    assert '<h2 id="title">' not in html
    layout = read(TEMPLATES / "layout.html")
    assert ("{% if page != 'book' %}<h1 class=\"lily-page-title\">{% block page_title %}{{ title }}"
            "{% endblock %}</h1>{% endif %}") in layout
    css = read(CSS / "lily-library.css")
    assert "h2#title" not in css
    title = [b for s, b in css_rules(css) if s == ".book-detail-meta h1#title"]
    assert title and "font-size: 52px" in title[0] and "font-weight: 700" in title[0]
    assert "font-size: 30px" in "".join(title[1:])  # phone size


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
    for name in ["detail.html", "listenmp3.html", "shelf_order.html"]:
        assert "|named_authors" in read(TEMPLATES / name), name


def test_installed_app_opens_the_reader_in_place():
    # §6.7: a new tab would leave an installed (standalone) app for the browser.
    js = read(JS / "lily.js")
    assert 'matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true' in js
    assert '$("a[data-reader-link]")' in js and 'this.removeAttribute("target")' in js
    handler = js[js.index('".lily-cover-actions .lily-read-now"'):]
    assert handler.index("window.location.href = url") < handler.index('window.open(url, "_blank", "noopener")')
    for name, needle in (("detail.html", 'id="readbtn"'), ("book_edit.html", 'id="readbtn"')):
        html = read(TEMPLATES / name)
        start = html.rindex("<a", 0, html.index(needle))
        tag = html[start:html.index(">", html.index(needle))]
        assert "data-reader-link=" in tag, name


def test_covers_take_their_own_shape_on_a_shelf():
    # §5.15: a fixed tile cropped Letter pages (half of arXiv's stamp) and 3:4 fronts (words at
    # the edge). The card keeps an A4 slot; the cover box takes the image's shape on its foot.
    css = read(CSS / "lily-library.css")
    rules = css_rules(css)
    card = [b for s, b in rules if s == ".lily-grid > .lily-book"]
    assert card and "display: grid" in card[0] and "container-type: inline-size" in card[0]
    slot = [b for s, b in rules if s == ".lily-grid > .lily-book::before"]
    assert slot and "aspect-ratio: 1 / 1.414" in slot[0]
    cover = [b for s, b in rules if ".lily-book .cover" in s and "grid-area: 1 / 1" in b]
    assert cover and "aspect-ratio: var(--r)" in cover[0] and "align-self: end" in cover[0]
    assert "calc(141.4cqw * var(--r))" in cover[0]
    order = [b for s, b in rules if s == ".lily-order-cover"]
    assert order and "aspect-ratio: var(--r" in order[0]
    assert "object-position: left center" not in css and "cover-wide" not in css
    js = read(JS / "lily.js")
    assert 'style.setProperty("--r"' in js and "cover-wide" not in js


def test_series_appear_nowhere():
    # Series were removed (2026-10-03): no page shows, edits, sorts or searches by them. The
    # calibre library still stores them; nothing reads them for display.
    assert not (TEMPLATES / "grid.html").exists() and not (JS / "filter_grid.js").exists()
    for name in ("book_edit.html", "detail.html", "image.html", "index.html", "list.html",
                 "listenmp3.html", "search_form.html", "shelf_order.html", "duplicates.html", "meta_fetch.html"):
        html = read(TEMPLATES / name)
        # (a calibre custom column of the "series" type is the user's own and stays)
        assert not re.search(r"\.series\b|series_index|data='series'|'series':", html), name
    for name in ("edit_books.js", "get_meta.js", "main.js"):
        assert "series" not in read(JS / name), name
    for name in ("web.py", "web_lists.py", "web_typeahead.py", "search.py", "opds.py"):
        source = read(REPO_ROOT / "cps" / name)
        for gone in ("/series", "get_series_json", "seriesasc", "render_series_books", "adv_search_serie",
                     "_related_books", "feed_series"):
            assert gone not in source, (name, gone)
    css = read(CSS / "lily-library.css")
    for gone in (".meta .series", "lily-series-grid", "related-number", "#book_of", "book-related"):
        assert gone not in css, gone


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


def test_duplicate_scan_notice_does_not_show_the_tasks_progress_line():
    # "Duplicate scan: Building duplicate index: 17836/17836 books" repeated the bar in words
    js = read(JS / "duplicates.js")
    notice = js[js.index("function setDuplicateScanNotice"):js.index("function showDuplicateScanFinishedNotice")]
    assert "taskMessage" not in notice


def test_book_byline_names_are_accent_and_the_ampersand_is_ink():
    css = read(CSS / "lily-library.css")
    byline = css.split(".book-detail-meta .author {", 1)[1].split("}", 1)[0]
    assert "color: var(--ink);" in byline
    assert ".book-detail-meta .author a { color: var(--accent); }" in css


def test_grid_covers_hang_a_ribbon_per_file_type():
    image = read(TEMPLATES / "image.html")
    card = re.search(r"{% macro book_card.*?{%- endmacro %}", image, flags=re.S).group(0)
    assert "{{ cover_marks(formats, is_read, _('Metadata fetched') if book.id|metadata_fetched) }}" in \
        card[card.index('<span class="img">'):card.index("</a>")]
    cover = re.search(r"{% macro cover_marks.*?{%- endmacro %}", image, flags=re.S).group(0)
    # Icon only, one ribbon per format, named for screen readers and with no popup over the cover (§5.6).
    ribbons = cover[cover.index('<span class="lily-cover-ribbons"'):cover.index('<span class="lily-cover-marks">')]
    assert 'role="img" aria-label=' in ribbons and "title=" not in ribbons
    assert "lily-ribbon lily-ribbon-{{ f if f in ['epub', 'pdf', 'djvu'] else 'other' }}" in ribbons
    # Finished and Fetched share the bottom-left marks, and lily.js puts the eye back there first.
    marks = cover[cover.index('<span class="lily-cover-marks">'):]
    assert marks.index("badge read") < marks.index("fetched_mark(")
    js = read(JS / "lily.js")
    assert '.find(".cover .lily-cover-marks")' in js and ".prependTo($marks)" in js
    rules = css_rules(read(CSS / "lily-library.css"))
    rule = lambda sel: next(body for selector, body in rules if selector == sel)
    hang = rule(".cover .lily-cover-ribbons")
    assert "top: 0" in hang and "left: 12px" in hang and "pointer-events: none" in hang
    ribbon = rule(".lily-ribbon")
    assert "clip-path: polygon(" in ribbon and "background: var(--ribbon)" in ribbon
    assert "mask: var(--glyph)" in rule(".lily-ribbon::before")
    for kind in ("pdf", "epub", "djvu", "other"):
        assert "--glyph: url(" in rule(f".lily-ribbon-{kind}"), kind
    assert (REPO_ROOT / "cps/static/icons/formats/djvu.svg").is_file()
    corner = rule(".cover .lily-cover-marks")
    assert "left: 8px" in corner and "bottom: 8px" in corner
    # The fetched disc is filled green so it reads on light and dark covers alike.
    disc = rule(".cover .lily-cover-marks > .lily-fetched")
    assert "background: var(--success)" in disc and "color: var(--surface)" in disc


def test_there_is_no_offline_reading_and_old_workers_are_let_go():
    # No service worker, page script or Save offline button any more
    for gone in ("cps/offline.py", "cps/templates/sw.js", "cps/static/js/offline.js"):
        assert not (REPO_ROOT / gone).exists(), gone
    layout = read(TEMPLATES / "layout.html")
    assert "lily-sw" not in layout and "js/offline.js" not in layout
    assert "offline-btn" not in read(TEMPLATES / "detail.html")
    assert "lily-offline-auto" not in read(TEMPLATES / "index.html")
    # A browser that installed the worker before drops it and its caches on the next visit
    js = read(REPO_ROOT / "cps/static/js/lily.js")
    assert "navigator.serviceWorker.getRegistrations()" in js and "reg.unregister()" in js
    assert 'n.indexOf("lily-") === 0' in js and "window.caches.delete(n)" in js


def test_grid_cards_show_no_authors():
    image = read(TEMPLATES / "image.html")
    card = re.search(r"{% macro book_card.*?{%- endmacro %}", image, flags=re.S).group(0)
    assert "author" not in card.split("-%}", 1)[1]
    assert ".lily-book .meta .author" not in read(CSS / "lily-library.css")
