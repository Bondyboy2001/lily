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

READERS = ["read.html", "readcbr.html", "readtxt.html", "readdjvu.html", "listenmp3.html"]


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


def test_readers_toolbar_controls_are_labelled_buttons():
    for name, ids in (("read.html", ["slider", "bookmark", "setting", "fullscreen"]),
                      ("readcbr.html", ["slider", "setting", "fullscreen"])):
        html = read(TEMPLATES / name)
        for control in ids:
            match = re.search(r'<(\w+)[^>]*\bid="%s"[^>]*>' % control, html)
            assert match, (name, control)
            tag = match.group(0)
            assert match.group(1) == "button" and 'type="button"' in tag, (name, tag)
            assert "aria-label=" in tag, (name, tag)
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
    font = html[html.index('id="font"'):html.index('id="readerLayout"')]
    assert re.findall(r'<button type="button" id="(\w+)"', font) == ["default", "Serif", "SansSerif"]
    for gone in ("Yahei", "SimSun", "KaiTi", 'id="Arial"'):
        assert gone not in html, gone
    # Spread and reflow are wide-screen options, hidden on phones.
    assert 'class="reader-setting reader-wide-only" id="layout"' in html
    reflow = html[:html.index('id="sidebarReflow"')]
    assert reflow.rindex("reader-wide-only") > reflow.rindex('id="layout"')
    css = strip_comments(read(CSS / "lily-reader.css"))
    assert re.search(r"@media \(max-width: 799px\) \{\s*\.lily-reader \.md-content > \.reader-wide-only "
                     r"\{ display: none; \}", css)
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
    # Dark is Abyss (paper / ink), in the page, its frame and the theme button.
    html = read(TEMPLATES / "read.html")
    dark = html[html.index('"darkTheme": {'):]
    dark = dark[:dark.index('"dark": true')]
    assert '"#1A1B26"' in dark and '"#C8D1F5"' in dark
    assert "#202124" not in html + read(CSS / "epub_themes.css") + read(CSS / "main.css")
    themes_css = read(CSS / "epub_themes.css")
    assert re.search(r"\.darkTheme \{\s*background: #1A1B26;\s*color: #C8D1F5;", themes_css)
    # Status bar colours: dark matches Abyss --paper everywhere.
    assert 'dark: "#1A1B26"' in read(JS / "lily.js") and "#1B1719" not in read(JS / "lily.js")
    manifest = read(REPO_ROOT / "cps/static/manifest.json")
    assert "#1f1f1f" not in manifest.lower()


def test_epub_viewer_fills_phone_screen():
    css = strip_comments(read(CSS / "main.css"))
    # No per-device fixed sizes (300x480 on every modern iPhone).
    assert "device-width" not in css
    assert not re.search(r"#viewer(\s+iframe)?\s*\{[^}]*width:\s*\d+px", css)
    phone = css[css.index("@media only screen and (max-width: 550px)"):]
    assert "100dvh" in phone and "safe-area-inset" in phone
    # The page slides exactly as far as the sidebar is wide.
    assert "translate(var(--reader-sidebar), 0)" in css
    assert re.search(r"#sidebar \{[^}]*width: var\(--reader-sidebar\)", css)
    assert "260px, 0" not in css and "min-width: 300px" not in css
    # Tap zones stay, wider, over the page edges.
    reader_css = strip_comments(read(CSS / "lily-reader.css"))
    assert re.search(r"\.lily-reader\.lily-epub #next \{[^}]*width: 22%", reader_css)
    # Progress label at full contrast.
    progress = re.search(r"\.lily-reader #progress:not\(\[role\]\) \{([^}]*)\}", reader_css).group(1)
    assert "opacity: 1" in progress


def test_epub_resume_without_waiting_for_locations():
    epub = read(JS / "reading/epub.js")
    assert "restore: false" in epub and "restore: true" not in epub
    assert "previousLocationCfi: startCfi" in epub and "LilyProgress.create" in epub
    progress = read(JS / "reading/epub-progress.js")
    # One book instance: no second ePub() download just to count locations.
    assert "ePub(" not in progress
    assert "locations.save()" in progress and "locations.load(" in progress
    assert "calibre.bookStamp" in progress
    # The saved position is not gated on locations.generate().
    assert "Promise.all([epub.locations.generate()" not in progress
    assert "bookStamp:" in read(TEMPLATES / "read.html")


def test_progress_sync_contract():
    js = read(JS / "reading/progress-sync.js")
    assert "X-CSRFToken" in js and "keepalive" in js
    assert "visibilitychange" in js and "pagehide" in js
    # A failed POST keeps its position and is retried when the network comes back.
    assert "pending = sending" in js and '"online"' in js
    for name, tag in (("readcbr.html", '"page:"'), ("readpdf.html", '"page:"')):
        assert tag in read(TEMPLATES / name), name
    assert '"time:"' in read(JS / "reading/audio-player.js")
    for name in ("read.html", "readcbr.html", "readpdf.html", "listenmp3.html"):
        assert "ajax/progress/" in read(TEMPLATES / name), name


def test_epub_reader_touch_and_fullscreen():
    epub = read(JS / "reading/epub.js")
    # iOS has no page Fullscreen API.
    assert "screenfull.isEnabled" in epub and '$("#fullscreen").remove()' in epub
    assert "screenfull.isEnabled" in read(JS / "kthoom.js")
    # Narrow screens close the contents panel after a pick.
    assert ".toc_link, #bookmarks a, #searchResults a" in epub


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
    from cps.cwa_functions import (library_refresh, cwa_stats, cwa_check_status, cwa_settings,
                                   cwa_internal)
    from cps.editbooks import editbook
    from cps.about import about
    from cps.search_metadata import meta
    from cps.tasks_status import tasks
    from cps.duplicates import duplicates
    from cps.gdrive import gdrive
    for bp in (library_refresh, cwa_stats, cwa_check_status, cwa_settings, cwa_internal,
               editbook, about, meta, tasks, duplicates, gdrive):
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
    ("cbz", 'class="lily-reader lily-comic"'),
    ("txt", 'class="lily-reader lily-txt"'),
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
    if fmt in ("epub", "cbz", "mp3", "pdf"):
        assert f"/ajax/progress/{book_id}" in html


@pytest.mark.unit
def test_advanced_search_renders(client):
    env, c, _ = client
    resp = c.get("/advsearch")
    assert resp.status_code == 200, resp.data[:300]
    html = resp.get_data(as_text=True)
    assert 'class="lp-row' in html
    assert 'name="include_tag"' in html and 'name="exclude_tag"' in html
    assert '<option class="tags_click" value="1">Fiction</option>' in html


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
