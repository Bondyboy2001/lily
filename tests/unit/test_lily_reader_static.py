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
    assert 'for="fader"' not in html
    assert "url_for('web.index') }}\" " not in html  # the old "Books" link
    # In-book search is live, not commented out.
    assert 'id="searchBox"' in html and "<!--input id=\"searchBox\"" not in html
    assert "js/reading/epub-search.js" in html
    assert html.index("js/reading/progress-sync.js") < html.index("js/reading/epub.js")
    assert html.index("js/reading/epub.js") < html.index("js/reading/epub-progress.js")


def test_epub_reader_phone_controls():
    html = read(TEMPLATES / "read.html")
    # A-/A+ with the value shown, not a bare slider.
    assert 'id="fontSmaller"' in html and 'id="fontLarger"' in html and 'id="fontSizeValue"' in html
    assert 'type="range"' not in html
    # Book default, Serif and Sans only.
    font = html[html.index('id="font"'):html.index('id="lineHeightSelect"')]
    assert re.findall(r'<button type="button" id="(\w+)"', font) == ["default", "Serif", "SansSerif"]
    for gone in ("Yahei", "SimSun", "KaiTi", 'id="Arial"'):
        assert gone not in html, gone
    # Spread is a wide-screen option, hidden on phones; there is no reflow option (the
    # contents panel always slides over the page).
    assert 'class="reader-setting reader-wide-only" id="layout"' in html
    assert "sidebarReflow" not in html
    css = strip_comments(read(CSS / "lily-reader.css"))
    assert re.search(r"@media \(max-width: 767px\) \{\s*\.lily-reader \.md-content > \.reader-wide-only "
                     r"\{ display: none; \}", css)
    settings = read(JS / "reading/epub-settings.js")
    assert 'matchMedia("(max-width: 767px)")' in settings
    assert "sidebarReflow" not in settings and "reflow" not in settings.lower()
    # 150% until the reader picks a size, and the sheet says so before the script runs.
    assert "DEFAULT_FONT_SIZE = 150" in settings
    assert 'id="fontSizeValue" class="reader-size-value" aria-live="polite">150%<' in html
    # CSP: no inline handlers; the settings script binds the buttons.
    assert not re.search(r"\son[a-z]+=", html)
    assert "js/reading/epub-settings.js" in html
    # Fills a phone screen, notch included.
    assert "viewport-fit=cover" in html


def test_epub_reader_follows_app_theme():
    for name in READERS:
        assert "{% include 'lily_theme_head.html' %}" in read(TEMPLATES / name), name
    settings = read(JS / "reading/epub-settings.js")
    assert 'getAttribute("data-theme") === "dark" ? "darkTheme" : "lightTheme"' in settings
    assert 'localStorage.getItem("calibre.reader.theme") ?? "lightTheme"' not in read(JS / "reading/epub.js")
    # Dark is the app's dark palette (paper / ink), in the page, its frame and the theme button.
    html = read(TEMPLATES / "read.html")
    dark = html[html.index('"darkTheme": {'):]
    dark = dark[:dark.index('"dark": true')]
    assert '"#1A1517"' in dark and '"#F0E8EC"' in dark
    assert "#202124" not in html + read(CSS / "epub_themes.css") + read(CSS / "main.css")
    themes_css = read(CSS / "epub_themes.css")
    assert re.search(r"\.darkTheme \{\s*background: #1A1517;\s*color: #F0E8EC;", themes_css)
    # Status bar colours: dark matches the dark --paper everywhere.
    assert 'dark: "#1A1517"' in read(JS / "lily.js") and "#1B1719" not in read(JS / "lily.js")
    manifest = read(REPO_ROOT / "cps/static/manifest.json")
    assert "#1f1f1f" not in manifest.lower()


def test_epub_viewer_fills_phone_screen():
    css = strip_comments(read(CSS / "main.css"))
    # No per-device fixed sizes (300x480 on every modern iPhone).
    assert "device-width" not in css
    assert not re.search(r"#viewer(\s+iframe)?\s*\{[^}]*width:\s*\d+px", css)
    phone = css[css.index("@media only screen and (max-width: 550px)"):]
    assert "safe-area-inset" in phone
    # The page is pinned to the visible screen (fixed, inset 0), so no 100vh under phone toolbars.
    reader_css = strip_comments(read(CSS / "lily-reader.css"))
    main_rule = re.search(r"\.lily-reader\.lily-epub #main,[^{]*\{([^}]*)\}", reader_css).group(1)
    assert "position: fixed" in main_rule and "inset: 0" in main_rule
    # The page slides exactly as far as the sidebar is wide.
    assert "translate(var(--reader-sidebar), 0)" in css
    assert re.search(r"#sidebar \{[^}]*width: var\(--reader-sidebar\)", css)
    assert "260px, 0" not in css and "min-width: 300px" not in css
    # Tap zones stay, wider, over the page edges.
    assert re.search(r"\.lily-reader\.lily-epub #next \{[^}]*width: 22%", reader_css)
    # Progress label at full contrast.
    progress = re.search(r"\.lily-reader #progress:not\(\[role\]\) \{([^}]*)\}", reader_css).group(1)
    assert "opacity: 1" in progress


def test_epub_resume_without_waiting_for_locations():
    epub = read(JS / "reading/epub.js")
    assert "restore: false" in epub and "restore: true" not in epub
    assert "previousLocationCfi: startCfi" in epub and "LilyProgress.create" in epub
    # epub.js can land a page early on a mid-paragraph CFI; both restores step on.
    assert "alignTo(startCfi)" in epub and "reader.lilyShow = function" in epub
    progress = read(JS / "reading/epub-progress.js")
    # One book instance: no second ePub() download just to count locations.
    assert "ePub(" not in progress
    assert "locations.save()" in progress and "locations.load(" in progress
    assert "calibre.bookStamp" in progress
    # The saved position is not gated on locations.generate().
    assert "Promise.all([epub.locations.generate()" not in progress
    assert "bookStamp:" in read(TEMPLATES / "read.html")


def test_epub_reader_layout_and_loading():
    html = read(TEMPLATES / "read.html")
    epub = read(JS / "reading/epub.js")
    progress = read(JS / "reading/epub-progress.js")
    # One download: progress uses the reader's own book instead of a second ePub().
    assert "ePub(calibre" not in progress and "reader.book" in progress
    # The sidebar slides over the page and closes from a scrim, Escape or a picked entry.
    assert 'id="sidebar-scrim"' in html and "sidebarReflow" not in html
    assert "closeSidebar" in epub and '"Escape"' in epub
    # Fixed device-sized viewers (300x480 on phones, fixed iPad frames) stay gone.
    assert "max-device-width" not in read(CSS / "main.css")
    assert "#sidebar-scrim" in read(CSS / "lily-reader.css")
    # Only fonts that suit an English UI; the row labels survive the tick reset.
    for font in ("Yahei", "SimSun", "KaiTi"):
        assert font not in html
    settings = read(JS / "reading/epub-settings.js")
    assert 'group.querySelectorAll("button")' in settings and 'button.querySelector("span")' in settings


def test_progress_sync_contract():
    js = read(JS / "reading/progress-sync.js")
    assert "X-CSRFToken" in js and "keepalive" in js
    assert "visibilitychange" in js and "pagehide" in js
    epub = read(JS / "reading/epub.js")
    assert "LilyProgress.create" in epub
    # The legacy unscoped per-book key must never be read: it shared positions
    # between user accounts and libraries on the same browser.
    progress = read(JS / "reading/epub-progress.js")
    assert "calibre.reader.progress" not in progress
    # Vendor restore:false stays — progressSync alone picks the position.
    assert "restore: false" in epub
    # A failed POST stays pending and is retried with backoff and when the network comes back.
    assert "RETRY_MAX" in js and '"online"' in js
    assert '"page:"' in read(TEMPLATES / "readpdf.html")
    assert '"time:"' in read(JS / "reading/audio-player.js")
    for name in ("read.html", "readpdf.html", "listenmp3.html"):
        assert "{{ progress_url }}" in read(TEMPLATES / name), name


def test_epub_reader_touch_and_fullscreen():
    epub = read(JS / "reading/epub.js")
    # iOS has no page Fullscreen API.
    assert "screenfull.isEnabled" in epub and '$("#fullscreen").remove()' in epub
    # Picking a contents entry, bookmark or search hit closes the panel.
    assert "#tocView, #bookmarksView, #searchResults" in epub and "setTimeout(closeSidebar, 0)" in epub


def test_end_of_book_card():
    html = read(TEMPLATES / "read.html")
    assert 'id="finish-card"' in html and "hidden>" in html
    assert "Read next in series:" in html and "Back to library" in html
    assert "js/reading/epub-finish.js" in html
    js = read(JS / "reading/epub-finish.js")
    assert "lily:reader-progress" in js and "0.99" in js
    assert "lily:reader-progress" in read(JS / "reading/epub-progress.js")


def test_reader_service_worker_is_scoped_to_reader_pages():
    sw = read(JS / "reading/reader-sw.js")
    assert re.search(r'var VERSION = "lily-reader-v\d+";', sw)
    # Never caches POSTs, ranges, signed-out redirects or other HTML.
    assert 'request.method !== "GET"' in sw and '"Range"' in sw
    assert "opaqueredirect" in sw and "response.redirected" in sw
    assert "ajax/progress" not in sw
    html = read(TEMPLATES / "read.html")
    assert "js/reading/reader-offline.js" in html
    assert "url_for('web.reader_service_worker')" in html
    assert "data-scope=\"{{ url_for('web.index') }}read/\"" in html
    assert "serviceWorker" not in read(JS / "lily.js")


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
    from cps.cwa_functions import (library_refresh, cwa_check_status, cwa_settings,
                                   cwa_internal)
    from cps.editbooks import editbook
    from cps.search_metadata import meta
    from cps.duplicates import duplicates
    from cps.logs import logs
    from cps.gdrive import gdrive
    for bp in (library_refresh, cwa_check_status, cwa_settings, cwa_internal,
               editbook, meta, duplicates, logs, gdrive):
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
    if fmt in ("epub", "mp3", "pdf"):
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


def test_rating_clear_buttons_are_trash_icons():
    # bootstrap-rating-input draws an X unless told otherwise; clearing a rating is a delete.
    for name in ("book_edit.html", "search_form.html"):
        html = read(TEMPLATES / name)
        for tag in re.findall(r"<input[^>]*data-clearable[^>]*>", html):
            assert 'data-clearable-icon="glyphicon-trash"' in tag, (name, tag)


@pytest.mark.unit
def test_authors_page_pages_by_letter(client):
    env, c, _ = client
    # The author list is cut by first letter on the server (a big library has thousands of names).
    for letter in "ABCDEFGHIJK":
        env.add_book(f"{letter} Book", author=f"{letter} Author")
    resp = c.get("/author")
    assert resp.status_code == 200, resp.data[:300]
    html = resp.get_data(as_text=True)
    assert "lily-field-toggle" in html and "lily-order-toggle" in html
    assert "lily-filter-toggle" in html and "lily-letter-menu" in html
    assert 'data-server-list="1"' in html


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


def _put_in_series(env, series_name, book_index_pairs):
    """Link books into one series (metadata.db, as Calibre stores it)."""
    import sqlite3
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.create_function("title_sort", 1, lambda t: t)
    try:
        cur = con.cursor()
        cur.execute("INSERT OR IGNORE INTO series (name, sort) VALUES (?, ?)", (series_name, series_name))
        series_id = cur.execute("SELECT id FROM series WHERE name=?", (series_name,)).fetchone()[0]
        for book_id, index in book_index_pairs:
            cur.execute("UPDATE books SET series_index=? WHERE id=?", (index, book_id))
            cur.execute("INSERT INTO books_series_link (book, series) VALUES (?, ?)", (book_id, series_id))
        con.commit()
    finally:
        con.close()


@pytest.mark.unit
def test_epub_reader_offers_next_in_series(client):
    env, c, book_id = client
    second = env.add_book("Reader Book Two", fmt="EPUB")
    third = env.add_book("Reader Book Three", fmt="PDF")
    _put_in_series(env, "Reader Saga", [(book_id, 1.0), (third, 3.0), (second, 2.0)])

    html = c.get(f"/read/{book_id}/epub").get_data(as_text=True)
    assert 'id="finish-card"' in html
    assert f'href="/read/{second}/epub"' in html and "Reader Book Two" in html
    # The next book has no EPUB: the card links to its book page instead.
    html = c.get(f"/read/{second}/epub").get_data(as_text=True)
    assert f'href="/book/{third}"' in html and "Reader Book Three" in html
    # The last book has no "next".
    html = c.get(f"/read/{third}/epub").get_data(as_text=True)
    assert 'id="finish-next"' not in html


@pytest.mark.unit
def test_epub_reader_without_series_has_no_next(client):
    env, c, book_id = client
    html = c.get(f"/read/{book_id}/epub").get_data(as_text=True)
    assert 'id="finish-card"' in html and 'id="finish-next"' not in html
    assert 'bookStamp: "' in html


@pytest.mark.unit
def test_reader_service_worker_route(client):
    env, c, _ = client
    anonymous = env.app.test_client()
    for who in (c, anonymous):
        resp = who.get("/reader-sw.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["Content-Type"]
        assert resp.headers["Cache-Control"] == "no-cache"
        assert b"lily-reader-v" in resp.data
