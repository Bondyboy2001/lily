"""Lily readers, advanced search and the phone/touch fallbacks: static checks plus a
render of each page through the Flask test client."""
import re
from pathlib import Path

import pytest

from .lily_env import lily_env, ADMIN_PASSWORD

REPO_ROOT = Path(__file__).resolve().parents[2]
CSS = REPO_ROOT / "cps/static/css"
JS = REPO_ROOT / "cps/static/js"
TEMPLATES = REPO_ROOT / "cps/templates"

READERS = ["read.html", "readdjvu.html", "listenmp3.html"]


def read(path):
    return path.read_text(encoding="utf-8")


def strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


# ---------------------------------------------------------------------------- static

def test_every_reader_loads_the_lily_reader_css():
    for name in READERS:
        html = read(TEMPLATES / name)
        assert "css/lily-reader.css" in html, name
        assert "lily-reader-page" in html and 'class="lily-reader' in html, name
        # Back goes to the book, not the library index.
        assert "url_for('web.show_book'" in html, name


def test_reader_chrome_follows_the_site_zoom():
    # The readers reset the page zoom, so the chrome takes the site's wide-screen zoom
    # itself; the epub page stays unzoomed and clears the chrome by hand.
    css = strip_comments(read(CSS / "lily-reader.css"))
    for width, zoom in (("1400px", "1.1"), ("1700px", "1.2")):
        assert re.search(r"@media \(min-width: %s\) \{ html\.lily-reader-page \{ --reader-zoom: %s; \} \}"
                         % (width, re.escape(zoom)), css), width
    assert re.search(r"#titlebar[^{]*\{\s*zoom: var\(--reader-zoom\)", css)
    viewer = re.search(r"\.lily-reader\.lily-epub #viewer \{(.*?)\}", css, flags=re.S).group(1)
    assert "zoom:" not in viewer
    assert "top: calc(64px * var(--reader-zoom))" in viewer


def test_pdf_reader_zooms_its_toolbars_not_its_pages():
    html = read(TEMPLATES / "readpdf.html")
    assert html.index("css/libs/viewer.css") < html.index("css/lily-pdf.css")
    css = strip_comments(read(CSS / "lily-pdf.css"))
    zoomed = re.search(r"([^{}]*)\{\s*zoom: var\(--pdf-chrome-zoom\);", css).group(1)
    assert ".toolbar" in zoomed and ".secondaryToolbar" in zoomed and ".findbar" in zoomed
    assert "#viewerContainer" not in zoomed
    assert re.search(r"#viewerContainer[^{]*\{\s*inset-block-start: calc\(32px \* var\(--pdf-chrome-zoom\)\)", css)


def test_pdf_reader_fetches_in_large_ranges():
    html = read(TEMPLATES / "readpdf.html")
    assert "PDFViewerApplicationOptions.set('disableRange', false);" in html
    assert "rangeChunkSize: 1048576" in html


def test_readers_toolbar_controls_are_labelled_buttons():
    html = read(TEMPLATES / "read.html")
    for control in ["slider", "bookmark", "setting", "fullscreen"]:
        match = re.search(r'<(\w+)[^>]*\bid="%s"[^>]*>' % control, html)
        assert match, control
        tag = match.group(0)
        assert match.group(1) == "button" and 'type="button"' in tag, tag
        assert "aria-label=" in tag, tag
    assert "css/reader.css" not in html


def test_epub_reader_fixes():
    html = read(TEMPLATES / "read.html")
    assert 'for="fontSizeFader"' in html and 'for="fader"' not in html
    assert "url_for('web.index') }}\" " not in html  # the old "Books" link
    # In-book search is live, not commented out.
    assert 'id="searchBox"' in html and "<!--input id=\"searchBox\"" not in html
    assert "js/reading/epub-search.js" in html
    assert html.index("js/reading/progress-sync.js") < html.index("js/reading/epub-progress.js")


def test_epub_reader_layout_and_loading():
    html = read(TEMPLATES / "read.html")
    epub = read(JS / "reading/epub.js")
    progress = read(JS / "reading/epub-progress.js")
    # One download: progress uses the reader's own book instead of a second ePub().
    assert "ePub(calibre" not in progress and "var epub=reader.book" in progress
    # The sidebar slides over the page and closes from a scrim, Escape or a picked entry.
    assert 'id="sidebar-scrim"' in html and "sidebarReflow" not in html
    assert "closeSidebar" in epub and '"Escape"' in epub
    # Fixed device-sized viewers (300x480 on phones, fixed iPad frames) stay gone.
    assert "max-device-width" not in read(CSS / "main.css")
    assert "#sidebar-scrim" in read(CSS / "lily-reader.css")
    # Only fonts that suit an English UI; the row labels survive the tick reset.
    for font in ("Yahei", "SimSun", "KaiTi"):
        assert font not in html
    assert "function pressOption" in html and html.count('aria-pressed="false"') >= 6


def test_progress_sync_contract():
    js = read(JS / "reading/progress-sync.js")
    assert "X-CSRFToken" in js and "keepalive" in js
    assert "visibilitychange" in js and "pagehide" in js
    epub = read(JS / "reading/epub-progress.js")
    assert "LilyProgress.create" in epub
    # The legacy unscoped per-book key must never be read: it shared positions
    # between user accounts and libraries on the same browser.
    assert 'localStorage.getItem("calibre.reader.progress' not in epub
    assert "getItem('calibre.reader.progress" not in epub
    # The last page before notes/index counts as the end, so back matter can't block "Finished".
    assert "function atStoryEnd" in epub and "atStoryEnd(fraction) ? 1 : fraction" in epub
    assert "BACK_MATTER" in epub and "STORY_END_MIN_FRACTION = 0.9" in epub
    # Vendor restore:false stays — progressSync alone picks the position.
    assert "restore: false" in read(JS / "reading/epub.js")
    assert '"page:"' in read(TEMPLATES / "readpdf.html")
    assert '"time:"' in read(JS / "reading/audio-player.js")
    for name in ("read.html", "readpdf.html", "listenmp3.html"):
        assert "{{ progress_url }}" in read(TEMPLATES / name), name


def test_audio_player_has_speed_control_and_no_soundmanager():
    html = read(TEMPLATES / "listenmp3.html")
    assert 'id="audio-speed"' in html and "<audio" in html
    assert "soundmanager2" not in html and "bootstrap.min.css" not in html


def test_advanced_search_link_stays_on_phones():
    css = strip_comments(read(CSS / "lily-shell.css"))
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if ".lily-search + .icon-btn" in selector:
            assert "display: none" not in body and "display:none" not in body, selector


def test_cover_actions_are_reachable_on_touch():
    css = strip_comments(read(CSS / "lily-library.css"))
    block = css[css.index("@media (hover: none)"):]
    block = block[:block.index("\n}\n")]
    assert ".lily-cover-actions" in block and "display: none" not in block


def test_advanced_search_uses_lily_form_rows():
    html = read(TEMPLATES / "search_form.html")
    assert 'import "lily_form.html" as f' in html
    assert "col-sm-" not in html and not re.search(r'\sstyle="', html)
    for field in ("title", "authors", "publisher", "comments", "read_status"):
        assert 'name="%s"' % field in html, field
    # The rating bounds are star radio groups posting the same field names (search.py).
    for field in ("ratinghigh", "ratinglow"):
        assert "image.rating_input('%s'" % field in html, field
    assert "date_field('publishstart'" in html and "date_field('publishend'" in html


# ---------------------------------------------------------------------------- render

def _register_remaining_blueprints(app):
    """layout.html links to every blueprint; add the ones the shared test app lacks."""
    from cps.cwa_functions import (library_refresh, cwa_settings,
                                   cwa_internal)
    from cps.editbooks import editbook
    from cps.search_metadata import meta
    from cps.duplicates import duplicates
    from cps.logs import logs
    for bp in (library_refresh, cwa_settings, cwa_internal,
               editbook, meta, duplicates, logs):
        if bp.name not in app.blueprints:
            app.register_blueprint(bp)


@pytest.fixture
def client(tmp_path):
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        _register_remaining_blueprints(env.app)
        book_id = env.add_book("Reader Book", tags=("Fiction",), lang="eng")
        c = env.app.test_client()
        resp = c.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
        assert resp.status_code in (200, 302)
        yield env, c, book_id


@pytest.mark.unit
@pytest.mark.parametrize("fmt, marker", [
    ("epub", 'id="searchView"'),
    ("djvu", 'class="lily-reader lily-djvu"'),
    ("mp3", 'id="audio-speed"'),
    ("pdf", "js/reading/progress-sync.js"),
])
def test_reader_pages_render(client, fmt, marker):
    env, c, book_id = client
    resp = c.get(f"/read/{book_id}/{fmt}")
    assert resp.status_code == 200, resp.data[:300]
    html = resp.get_data(as_text=True)
    assert marker in html
    if fmt != "pdf":
        assert f'href="/book/{book_id}"' in html
    if fmt in ("epub", "mp3", "pdf", "djvu"):
        assert f"/ajax/progress/{book_id}?format={fmt}" in html


@pytest.mark.unit
@pytest.mark.parametrize("fmt", ["cbz", "txt"])
def test_unsupported_formats_have_no_reader(client, fmt):
    env, c, book_id = client
    resp = c.get(f"/read/{book_id}/{fmt}")
    assert resp.status_code == 302


def test_djvu_viewer_creates_its_worker_from_the_top_window():
    # Chrome blocks a worker created inside GWT's helper iframe, so the viewer never got its
    # document; the vendored build creates it through $wnd instead (see the lib's README).
    scripts = sorted((JS / "libs/djvu_html5/djvu_html5").glob("*.cache.js"))
    assert scripts
    for script in scripts:
        code = read(script)
        assert "djvuWorker=new $wnd.Worker(" in code and "djvuWorker=new Worker(" not in code, script.name


def test_djvu_reader_uses_the_lily_chrome():
    # The epub title bar with the pdf reader's page box and zoom; the viewer's own toolbar
    # and status sprite are hidden and driven from djvu_reader.js.
    html = read(TEMPLATES / "readdjvu.html")
    assert "{% include 'lily_theme_head.html' %}" in html
    for control in ('id="titlebar"', 'id="book-title"', 'id="djvu-page"', 'id="djvu-zoom"',
                    'id="prev" class="arrow"', 'id="next" class="arrow"', 'id="progress-sync-status"',
                    "js/reading/progress-sync.js", "css/lily-icons.css"):
        assert control in html, control
    css = strip_comments(read(CSS / "lily-reader.css"))
    assert re.search(r"#djvuContainer \.toolbar,\s*\.lily-reader\.lily-djvu #djvuContainer \.statusImage \{ display: none; \}", css)
    js = strip_comments(read(JS / "reading/djvu_reader.js"))
    # The canvas backdrop follows the theme, and positions save like the pdf reader's.
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", js)
    assert 'getPropertyValue("--sunk")' in js and '"page:" + page' in js


def test_rating_inputs_are_keyboard_radio_groups():
    # docs/design.md §5.14: a rating input is a radio group (none + 1-5 stars) drawn as stars,
    # so the arrow keys pick a rating; the mouse-only bootstrap-rating-input plugin is gone.
    image = read(TEMPLATES / "image.html")
    macro = re.search(r"{% macro rating_input.*?{%- endmacro %}", image, flags=re.S).group(0)
    assert 'role="radiogroup"' in macro and 'type="radio"' in macro
    assert "aria-labelledby=" in macro and "aria-label=" in macro
    assert "ngettext('%(num)s star', '%(num)s stars', n)" in macro
    # Clearing a rating is a delete, so the "none" option is the trash glyph.
    clear = re.search(r'<label[^>]*class="lily-stars-clear"[^>]*>.*?</label>', macro).group(0)
    assert "glyphicon-trash" in clear and 'title="{{ none_text }}"' in clear
    for name in ("book_edit.html", "search_form.html"):
        html = read(TEMPLATES / name)
        assert "bootstrap-rating-input" not in html and "data-clearable" not in html, name
        assert "image.rating_input(" in html, name
    css = strip_comments(read(CSS / "lily-library.css"))
    assert "rating-input" not in css
    assert re.search(r"\.lily-stars \.lily-star \{[^}]*font-size: 27px", css)
    assert re.search(r"\.lily-stars-input:focus-visible \+ label \{[^}]*outline: 2px solid", css)
    # get_meta.js fills the rating by checking a radio, not through the plugin.
    meta = read(JS / "get_meta.js")
    assert '$("#rating").data("rating")' not in meta and "input[name='rating']" in meta


@pytest.mark.unit
def test_authors_page_has_no_letter_filter(client):
    env, c, _ = client
    # The letter menu used to appear once a list had more than nine initials.
    for letter in "ABCDEFGHIJK":
        env.add_book(f"{letter} Book", author=f"{letter} Author")
    resp = c.get("/author")
    assert resp.status_code == 200, resp.data[:300]
    html = resp.get_data(as_text=True)
    assert "lily-field-toggle" in html and "lily-order-toggle" in html
    assert "lily-filter-toggle" not in html and "lily-letter-menu" not in html


@pytest.mark.unit
def test_advanced_search_renders(client):
    env, c, _ = client
    resp = c.get("/advsearch")
    assert resp.status_code == 200, resp.data[:300]
    html = resp.get_data(as_text=True)
    assert 'class="lp-row' in html
    assert 'name="include_tag"' in html and 'name="exclude_tag"' in html
    assert '<option class="tags_click" value="1">Fiction</option>' in html


@pytest.mark.unit
def test_reading_and_finished_lists_show_in_the_sidebar_and_filter_by_status(client):
    env, c, _ = client
    ub = env.ub
    admin = env.admin()
    admin.sidebar_view = 0  # every optional section off: these two still show
    reading = env.add_book("Halfway Book")
    finished = env.add_book("Done Book")
    env.add_book("Untouched Book")
    ub.session.add(ub.ReadBook(user_id=admin.id, book_id=reading, read_status=ub.ReadBook.STATUS_IN_PROGRESS))
    ub.session.add(ub.ReadBook(user_id=admin.id, book_id=finished, read_status=ub.ReadBook.STATUS_FINISHED))
    ub.session.commit()

    html = c.get("/inprogress/stored/").get_data(as_text=True)
    assert 'id="nav_inprogress"' in html and 'id="nav_read"' in html
    assert "Halfway Book" in html and "Done Book" not in html and "Untouched Book" not in html

    html = c.get("/read/stored/").get_data(as_text=True)
    assert "Done Book" in html and "Halfway Book" not in html and "Untouched Book" not in html


def test_pdf_reader_has_a_back_to_book_link_first_in_its_toolbar():
    html = read(TEMPLATES / "readpdf.html")
    link = re.search(r'<a id="backToBook"[^>]*>', html).group(0)
    assert 'class="toolbarButton"' in link
    assert "url_for('web.show_book', book_id=pdffile)" in link
    assert "aria-label=\"{{_('Back to book')}}\"" in link and "title=\"{{_('Back to book')}}\"" in link
    left = html.index('id="toolbarViewerLeft"')
    assert left < html.index('id="backToBook"') < html.index('id="sidebarToggle"')
    css = strip_comments(read(CSS / "lily-pdf.css"))
    assert re.search(r"#backToBook::before \{[^}]*mask-image: url\(libs/images/toolbarButton-pageUp\.svg\)", css)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)


def test_pdf_reader_opens_at_page_width_on_phones_only():
    html = read(TEMPLATES / "readpdf.html")
    assert "window.matchMedia('(max-width: 600px)')" in html
    assert "PDFViewerApplicationOptions.set('defaultZoomValue', phone ? 'page-width' : '150')" in html
    # The saved position is a page number only, so the default zoom never fights it.
    assert '"page:"' in html and "app.page = Math.floor(page)" in html


def test_epub_page_theme_follows_the_app_theme_until_one_is_picked():
    html = read(TEMPLATES / "read.html")
    assert "function savedReaderTheme ()" in html
    assert 'localStorage.getItem("lily-theme")' in html and '"darkTheme" : "lightTheme"' in html
    # Applying the starting theme must not save it, or the first open would pin Light for good.
    assert "if (remember !== false)" in html
    assert 'localStorage.setItem("calibre.reader.theme"' not in html
    js = read(JS / "reading" / "epub.js")
    assert "selectTheme(savedReaderTheme(), false)" in js


@pytest.mark.unit
def test_book_editor_shows_only_the_optional_fields_with_values(client):
    env, c, book_id = client
    resp = c.get(f"/admin/book/{book_id}")
    assert resp.status_code == 200, resp.data[:300]
    html = resp.get_data(as_text=True)
    # The fixture book has a language, a publish date and a tag, but no series, publisher,
    # rating or description: those stay hidden, and nothing offers to add them by hand
    for key in ("languages", "pubdate", "tags"):
        assert f'data-optional="{key}">' in html, key
    for key in ("series", "publisher", "rating", "comments"):
        assert f'data-optional="{key}" hidden>' in html, key
    assert "data-optional-add" not in html and 'id="tag-add"' not in html
    assert 'id="author-add"' in html and 'id="title"' in html
    # The rating is a star radio group named by its visible label; "none" is chosen.
    group = re.search(r'<div class="lily-stars" role="radiogroup"[^>]*>(.*?)</div>', html, flags=re.S)
    assert group and 'aria-labelledby="rating-label"' in group.group(0)
    assert '<label id="rating-label">' in html
    radios = re.findall(r'<input type="radio"[^>]*name="rating"[^>]*>', group.group(1))
    assert [re.search(r'value="([^"]*)"', r).group(1) for r in radios] == ["1", "2", "3", "4", "5", ""]
    assert all("data-optional-value" in r for r in radios)
    assert "checked" in radios[-1] and not any("checked" in r for r in radios[:-1])
    assert "<span class=\"sr-only\">3 stars</span>" in group.group(1)
    assert "<span class=\"sr-only\">1 star</span>" in group.group(1)


@pytest.mark.unit
def test_advanced_search_rating_bounds_are_star_radio_groups(client):
    env, c, _ = client
    html = c.get("/advsearch").get_data(as_text=True)
    for name, label in (("ratinghigh", "Rating above"), ("ratinglow", "Rating below")):
        group = re.search(r'<div class="lily-stars" role="radiogroup" aria-label="%s">(.*?)</div>' % label,
                          html, flags=re.S)
        assert group, name
        values = re.findall(r'<input type="radio"[^>]*name="%s"[^>]*value="([^"]*)"' % name, group.group(1))
        # "Any rating" posts an empty value, which search.py treats as no bound.
        assert values == ["1", "2", "3", "4", "5", ""], name
        assert '<span class="sr-only">Any rating</span>' in group.group(1)


@pytest.mark.unit
def test_book_page_title_is_the_only_h1(client, temp_cwa_db):
    env, c, book_id = client
    html = c.get(f"/book/{book_id}").get_data(as_text=True)
    assert html.count("<h1") == 1
    assert re.search(r'<h1 id="title">\s*Reader Book\s*</h1>', html)
    assert "lily-page-title" not in html
    # Other pages keep their title in the top bar.
    html = c.get("/advsearch").get_data(as_text=True)
    assert html.count("<h1") == 1 and '<h1 class="lily-page-title">' in html


@pytest.mark.unit
def test_grid_quick_actions_name_their_book(client):
    env, c, _ = client
    html = c.get("/").get_data(as_text=True)
    actions = re.search(r'<div class="lily-cover-actions"[^>]*>(.*?)</div>', html, flags=re.S).group(1)
    labels = re.findall(r'aria-label="([^"]*)"', actions)
    assert "Read Reader Book" in labels or "Download Reader Book" in labels
    assert "Mark Reader Book as read" in labels and "Edit Reader Book" in labels
    toggle = re.search(r'<button[^>]*class="icon-btn lily-toggle-read[^"]*"[^>]*>', actions).group(0)
    assert "aria-pressed" not in toggle and "title=" not in toggle
    assert 'data-label-read="Mark Reader Book as unread"' in toggle


# ---------------------------------------------------------------------------- bookmarks

def test_epub_reader_keeps_several_bookmarks():
    epub = read(JS / "reading/epub.js")
    marks = read(JS / "reading/epub-bookmarks.js")
    shared = read(JS / "reading/bookmarks.js")
    html = read(TEMPLATES / "read.html")
    # The one-bookmark code and its raw alert() are gone; the vendor list starts empty.
    assert "there can only be one" not in epub and "updateBookmark" not in epub
    assert "bookmarks: []" in epub and "calibre.bookmark " not in epub
    assert "web.set_bookmark" not in html and "web.reader_bookmarks" in html
    order = [html.index("js/reading/" + name) for name in ("epub.js", "bookmarks.js", "epub-bookmarks.js")]
    assert order == sorted(order)
    # The title bar button toggles the page's bookmark and shows it pressed.
    assert 'data-add-label="' in html and 'data-remove-label="' in html
    assert '"aria-pressed"' in marks and "}, true);" in marks and "event.stopPropagation()" in marks
    # Bookmarks list in the sidebar tab, labelled with the chapter and the page's first words.
    assert 'id="show-Bookmarks"' in html and 'aria-pressed="false"' in html
    assert "reader-bookmark-chapter" in marks and "reader-bookmark-excerpt" in marks
    assert "lilyChapterFor" in epub and "lilyChapterFor" in marks
    # Rows remove through a labelled trash icon button (§5.2).
    assert "icon-btn is-danger reader-bookmark-remove" in shared and "glyphicon-trash" in shared
    assert 'setAttribute("aria-label", options.removeLabel)' in shared
    assert '"X-CSRFToken"' in shared and '"DELETE"' in shared


def test_reader_scripts_never_alert():
    for script in sorted((JS / "reading").glob("*.js")):
        assert not re.search(r"\balert\(", read(script)), script.name


def test_bookmark_failures_are_quiet_notices():
    include = read(TEMPLATES / "reader_bookmark_status.html")
    assert 'id="bookmark-status"' in include and 'role="status"' in include
    for kind in ("load", "save", "remove"):
        assert 'data-%s-failed="' % kind in include, kind
    for name in ("read.html", "readdjvu.html", "readpdf.html"):
        assert "{% include 'reader_bookmark_status.html' %}" in read(TEMPLATES / name), name
    css = strip_comments(read(CSS / "lily-reader.css"))
    assert ".lily-reader .lily-bookmark-status:empty { display: none; }" in css
    assert "#bookmark-status:empty { display: none; }" in strip_comments(read(CSS / "lily-pdf.css"))


def test_paged_readers_bookmark_pages():
    djvu = read(TEMPLATES / "readdjvu.html")
    js = read(JS / "reading/djvu_reader.js")
    assert djvu.index("js/reading/bookmarks.js") < djvu.index("js/reading/djvu_reader.js")
    for control in ('id="bookmark"', 'id="bookmarks-button"', 'aria-expanded="false"', 'id="bookmarks-panel"',
                    'id="bookmarks-empty"', "data-bookmarks-url="):
        assert control in djvu, control
    assert "LilyBookmarks.paged(" in js and "bookmarks.setPage(page)" in js
    pdf = read(TEMPLATES / "readpdf.html")
    assert pdf.index("js/reading/bookmarks.js") < pdf.index("LilyBookmarks.paged(")
    for control in ('id="lilyBookmark"', 'id="lilyBookmarks"', 'id="lilyBookmarksPanel"', "bookmarks.setPage(evt.pageNumber)"):
        assert control in pdf, control
    shared = read(JS / "reading/bookmarks.js")
    assert '"page:" + page' in shared and "aria-expanded" in shared and '"Escape"' in shared
    css = strip_comments(read(CSS / "lily-pdf.css"))
    assert "#lilyBookmark::before" in css and "url(../icons/phosphor/bookmark-simple.svg)" in css


@pytest.mark.unit
@pytest.mark.parametrize("fmt", ["epub", "pdf", "djvu"])
def test_readers_render_their_bookmarks_url(client, fmt):
    env, c, book_id = client
    html = c.get(f"/read/{book_id}/{fmt}").get_data(as_text=True)
    assert f"/ajax/bookmarks/{book_id}/{fmt.upper()}" in html
    assert 'id="bookmark-status"' in html


@pytest.mark.unit
def test_delete_dialog_renders_its_placeholders_as_markup(client, temp_cwa_db):
    # The translated sentence wraps empty spans that main.js fills with the title and format;
    # escaped, they would show up as literal "<span …>" text in the dialog.
    env, c, book_id = client
    html = c.get(f"/book/{book_id}").get_data(as_text=True)
    assert '<span class="delete-title"></span> will be deleted from the library' in html
    assert 'The <span class="delete-format"></span> file of <span class="delete-title"></span>' in html
    assert "&lt;span" not in html


@pytest.mark.unit
def test_a_book_with_no_author_shows_none(client, temp_cwa_db):
    # Calibre files a book with no author under "Unknown"; Lily leaves the author blank
    env, c, _ = client
    book_id = env.add_book("Abstract Algebra", author="Unknown")
    detail = c.get(f"/book/{book_id}").get_data(as_text=True)
    assert "Abstract Algebra" in detail and 'class="author"' not in detail
    edit = c.get(f"/admin/book/{book_id}").get_data(as_text=True)
    assert 'name="authors" id="authors" value=""' in edit
    authors = c.get("/author").get_data(as_text=True)
    assert "Test Author" in authors and "Unknown" not in authors


def test_pdf_reader_remembers_the_zoom_per_book():
    html = read(TEMPLATES / "readpdf.html")
    assert '"lily-pdf-zoom:" + (phone ? "phone:" : "") + {{ progress_key|tojson }}' in html
    # Put back after pdf.js' start-up view (which would undo it); the phone/desktop default
    # stays pdf.js' start-up zoom.
    init = html[html.index('app.eventBus.on("documentinit"'):]
    assert init.index("app.pdfViewer.currentScaleValue = savedZoom") < init.index('app.eventBus.on("pagesinit"')
    assert 'app.eventBus.on("scalechanging"' in html and "evt.presetValue ||" in html
    # Saving waits for the saved page to be restored, so the opening zoom isn't recorded.
    handler = html[html.index('app.eventBus.on("scalechanging"'):]
    assert handler.index("if (!restored)") < handler.index("localStorage.setItem")


@pytest.mark.unit
def test_book_page_offers_the_rest_of_the_series_and_the_author(client, temp_cwa_db):
    import sqlite3
    env, c, _ = client
    ids = {n: env.add_book(f"Saga {n}", author="Ann Writer") for n in (1, 2, 3)}
    env.add_book("Standalone", author="Ann Writer")
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.create_function("title_sort", 1, lambda t: t)  # Calibre's triggers call it
    con.execute("INSERT INTO series (name, sort) VALUES ('Saga', 'Saga')")
    sid = con.execute("SELECT id FROM series WHERE name='Saga'").fetchone()[0]
    for n, book_id in ids.items():
        con.execute("INSERT INTO books_series_link (book, series) VALUES (?, ?)", (book_id, sid))
        con.execute("UPDATE books SET series_index=? WHERE id=?", (float(n), book_id))
    con.commit()
    con.close()

    def row(html, heading_id):
        section = html[html.index(f'aria-labelledby="{heading_id}"'):]
        section = section[:section.index("</section>")]
        return re.findall(r'<p title="([^"]+)" class="title">', section), section

    html = c.get(f"/book/{ids[1]}").get_data(as_text=True)
    titles, section = row(html, "related-series-heading")
    assert "Next in Saga" in section and titles == ["Saga 2", "Saga 3"] and "Book 2" in section
    titles, section = row(html, "related-author-heading")
    assert "More by Ann Writer" in section and titles == ["Standalone"]

    # The last book looks back instead; a book with no series or siblings shows neither row.
    html = c.get(f"/book/{ids[3]}").get_data(as_text=True)
    titles, section = row(html, "related-series-heading")
    assert "Earlier in Saga" in section and titles == ["Saga 1", "Saga 2"]
    lone = env.add_book("Only Child", author="Solo Author")
    html = c.get(f"/book/{lone}").get_data(as_text=True)
    assert "related-series-heading" not in html and "related-author-heading" not in html


def test_phone_tap_strips_sit_above_the_book():
    # The iframe in #viewer took every tap but the outer 14px, so tapping an edge did nothing
    css = read(CSS / "lily-reader.css")
    phone = css.split("@media (max-width: 600px) {", 1)[1].split("\n}\n", 1)[0]
    arrow = phone.split(".lily-reader.lily-epub .arrow {", 1)[1].split("}", 1)[0]
    assert "z-index: 3;" in arrow and "width: 18%;" in arrow
    # ...which is above #viewer's own
    viewer = read(CSS / "main.css").split("#viewer {", 1)[1].split("}", 1)[0]
    assert "z-index: 2;" in viewer


def test_phone_pdf_toolbar_keeps_to_one_row_with_larger_buttons():
    # At 390px pdf.js' bar plus Lily's Back and bookmark buttons wrapped: zoom "+" sat on the page
    css = read(CSS / "lily-pdf.css")
    phone = css.split("@media (max-width: 600px) {", 1)[1].split("\n}\n", 1)[0]
    assert ":root { --pdf-chrome-zoom: 1.25; }" in phone
    hidden = phone.split("{ display: none; }", 1)[0]
    for selector in ("#toolbarViewerMiddle", "#editorModeButtons", "#editorModeSeparator"):
        assert selector in hidden


def _reader_chrome_contrast(opacity, ink, ground):
    named = {"white": "#ffffff", "black": "#000000"}

    def rgb(value):
        value = named.get(value, value).lstrip("#")
        if len(value) == 3:
            value = "".join(c * 2 for c in value)
        return [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]

    def luminance(c):
        r, g, b = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    fg, bg = rgb(ink), rgb(ground)
    mixed = [opacity * f + (1 - opacity) * b for f, b in zip(fg, bg)]
    hi, lo = sorted((luminance(mixed), luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_reader_chrome_is_legible_in_every_page_theme():
    # The chapter title (.7), page share (.6) and arrows (.3) take the theme's ink at an
    # opacity; in Sepia that was 3.3:1, 2.7:1 and 1.6:1
    template = read(TEMPLATES / "read.html")
    themes = re.findall(r'"bgColor": "([^"]+)",\s*"css_path": "[^"]*",\s*"title-color": "([^"]+)"', template)
    assert len(themes) == 4
    css = read(CSS / "lily-reader.css")

    def opacity(selector):
        rule = css.split(selector + " {", 1)[1].split("}", 1)[0]
        return float(re.search(r"opacity: ([.\d]+);", rule).group(1))

    for ground, ink in themes:
        assert _reader_chrome_contrast(opacity(".lily-reader #chapter-title"), ink, ground) >= 4.5
        assert _reader_chrome_contrast(opacity(".lily-reader #progress:not([role])"), ink, ground) >= 4.5
        assert _reader_chrome_contrast(opacity(".lily-reader .arrow"), ink, ground) >= 3


@pytest.mark.unit
def test_book_table_opens_on_the_columns_a_cleanup_reads(client, temp_cwa_db):
    env, c, book_id = client
    html = c.get("/table").get_data(as_text=True)

    def visible(field):
        return re.search(r'<th[^>]*data-field="%s"[^>]*data-visible\s*=\s*"(\w+)"' % field, html).group(1)

    for field in ("title", "authors", "formats", "isbn", "added"):
        assert visible(field) == "true", field
    for field in ("sort", "author_sort", "tags", "series", "languages", "publishers"):
        assert visible(field) == "false", field
    # Admins can look the ticked books up; it starts disabled like the other selection actions
    assert re.search(r'id="lookup_selected_books"[^>]*aria-disabled="true"', html)
    rows = c.get("/ajax/listbooks?offset=0&limit=10").get_json()["rows"]
    row = next(r for r in rows if r["id"] == book_id)
    assert row["formats"] == "EPUB" and row["added"] == "2026-01-01" and row["isbn"] == ""


@pytest.mark.unit
def test_a_columns_the_user_toggled_keeps_its_saved_state(client, temp_cwa_db):
    env, c, _ = client
    saved = {"title": "true", "authors": "true", "series": "true"}
    assert c.post("/ajax/table_settings", json=saved).status_code == 200
    html = c.get("/table").get_data(as_text=True)
    assert re.search(r'data-field="series"[^>]*data-visible\s*=\s*"true"', html)
    assert re.search(r'data-field="formats"[^>]*data-visible\s*=\s*"true"', html)


@pytest.mark.unit
def test_book_page_offers_a_lookup_when_the_description_is_missing(client, temp_cwa_db):
    env, c, book_id = client
    html = c.get(f"/book/{book_id}").get_data(as_text=True)
    assert 'id="book-fetch-prompt"' in html
    assert f"/admin/book/{book_id}?fetch=1" in html
    # The edit page takes the flag and the lookup button it opens is there
    edit = c.get(f"/admin/book/{book_id}?fetch=1").get_data(as_text=True)
    assert 'id="get_meta"' in edit
    assert '.has("fetch")' in read(JS / "get_meta.js")


@pytest.mark.unit
def test_long_browse_lists_get_a_filter_short_ones_do_not(client, temp_cwa_db):
    env, c, _ = client
    assert 'id="lily-list-filter"' not in c.get("/author").get_data(as_text=True)
    for n in range(30):
        env.add_book(f"Book {n}", author=f"Author {n:02d}")
    html = c.get("/author").get_data(as_text=True)
    assert 'id="lily-list-filter"' in html and 'id="lily-list-nomatch"' in html
    assert "applyListFilter" in read(JS / "filter_list.js")


@pytest.mark.unit
def test_duplicates_are_a_notice_never_a_dialog(client, temp_cwa_db):
    env, c, _ = client
    html = c.get("/").get_data(as_text=True)
    assert "duplicate-notification-modal" not in html and "Remind me later" not in html
    assert 'id="duplicate-notice-config"' in html and "js/duplicate-notifier.js" in html
    js = read(JS / "duplicate-notifier.js")
    assert ".modal(" not in js and "showNotice" in js


@pytest.mark.unit
def test_slash_focuses_the_search_box(client, temp_cwa_db):
    env, c, _ = client
    assert 'aria-keyshortcuts="/"' in c.get("/").get_data(as_text=True)
    js = read(JS / "lily.js")
    assert 'e.key === "/"' in js and 'getElementById("query")' in js
