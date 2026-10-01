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

def test_reader_css_uses_tokens_only():
    css = strip_comments(read(CSS / "lily-reader.css"))
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    for banned in ("rgba(", "hsla(", "gradient", "backdrop-filter"):
        assert banned not in css, banned


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
    for field in ("title", "authors", "publisher", "comments", "read_status", "ratinghigh", "ratinglow"):
        assert 'name="%s"' % field in html, field
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


def test_rating_clear_buttons_are_trash_icons():
    # bootstrap-rating-input draws an X unless told otherwise; clearing a rating is a delete.
    for name in ("book_edit.html", "search_form.html"):
        html = read(TEMPLATES / name)
        for tag in re.findall(r"<input[^>]*data-clearable[^>]*>", html):
            assert 'data-clearable-icon="glyphicon-trash"' in tag, (name, tag)


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
    # The fixture book has a language and a publish date, but no series, publisher or rating
    for key in ("languages", "pubdate"):
        assert f'data-optional="{key}">' in html, key
        assert f'data-optional-add="{key}" hidden>' in html, key
    for key in ("series", "publisher", "rating"):
        assert f'data-optional="{key}" hidden>' in html, key
        assert f'data-optional-add="{key}">' in html, key
