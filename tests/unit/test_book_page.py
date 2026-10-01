# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The book page and home page reading paths: which format Read opens, Continue · n%, next in
series, Convert to EPUB, one-tap shelves and the Up next row."""

import re
import sqlite3
from types import SimpleNamespace

import pytest

from cps.helper import check_read_formats, readable_formats
from .lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


# -- pure helpers -------------------------------------------------------------------------

def test_readable_formats_are_ordered_best_first_and_skip_formats_without_a_reader():
    assert readable_formats(["TXT", "MOBI", "PDF", "CBZ", "KEPUB", "EPUB", "MP3", "DJVU"]) == \
        ["epub", "kepub", "pdf", "djvu", "mp3"]
    assert readable_formats(["MOBI", "AZW3", "FB2", "HTML", "DOCX"]) == []


def test_check_read_formats_reads_the_book_data():
    book = SimpleNamespace(data=[SimpleNamespace(format="PDF"), SimpleNamespace(format="EPUB")])
    assert check_read_formats(book) == ["epub", "pdf"]


# -- through the app ----------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path))  # the book page reads Lily settings from cwa.db
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(e.app)
        yield e


def _login(env, name=None, password=ADMIN_PASSWORD):
    client = env.app.test_client()
    resp = client.post("/login", data={"username": name or env.admin().name, "password": password})
    assert resp.status_code in (200, 302)
    return client


def _sql(env, statement, args=()):
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.create_function("title_sort", 1, lambda t: t)
    try:
        cur = con.execute(statement, args)
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _add_format(env, book_id, fmt):
    _sql(env, "INSERT INTO data (book, format, uncompressed_size, name) VALUES (?,?,?,?)",
         (book_id, fmt, 10, f"file{book_id}"))


def _put_in_series(env, book_id, series, index):
    _sql(env, "INSERT OR IGNORE INTO series (name, sort) VALUES (?, ?)", (series, series))
    con = sqlite3.connect(env.library_dir / "metadata.db")
    series_id = con.execute("SELECT id FROM series WHERE name=?", (series,)).fetchone()[0]
    con.close()
    _sql(env, "INSERT INTO books_series_link (book, series) VALUES (?, ?)", (book_id, series_id))
    _sql(env, "UPDATE books SET series_index=? WHERE id=?", (index, book_id))


def _page(client, url):
    resp = client.get(url)
    assert resp.status_code == 200, resp.data[:300]
    return resp.get_data(as_text=True)


def test_read_button_opens_the_best_format_in_the_same_tab(env):
    book = env.add_book("Two Formats", fmt="PDF")
    _add_format(env, book, "EPUB")
    html = _page(_login(env), f"/book/{book}")
    button = re.search(r'<a id="readbtn"[^>]*>.*?</a>', html, re.S).group(0)
    assert f'href="/read/{book}/epub"' in button and "_blank" not in button
    assert re.search(r"</span>Read\s*</a>", button)


def test_continue_shows_the_saved_percentage(env):
    book = env.add_book("Half Read")
    ub = env.ub
    ub.session.add(ub.WebReaderProgress(user_id=env.admin().id, book_id=book, cfi="epubcfi(/6/2)", percent=0.42))
    ub.session.commit()
    html = _page(_login(env), f"/book/{book}")
    assert "Continue · 42%" in html


def test_unreadable_book_offers_convert_to_epub_to_editors_only(env):
    book = env.add_book("Kindle Only", fmt="MOBI")
    admin = _login(env)
    html = _page(admin, f"/book/{book}")
    assert 'id="readbtn"' not in html and "No readable format" in html
    assert 'id="convert-epub-btn"' in html and f'action="/book/{book}/convert-epub"' in html

    from cps import constants
    env.add_user("reader", password="pw", role=constants.ROLE_VIEWER)
    html = _page(_login(env, "reader", "pw"), f"/book/{book}")
    assert "No readable format" in html and "convert-epub-btn" not in html
    assert _login(env, "reader", "pw").post(f"/book/{book}/convert-epub").status_code == 403


def test_convert_is_offered_beside_read_for_a_mobi_without_epub(env):
    book = env.add_book("Pdf And Mobi", fmt="PDF")
    _add_format(env, book, "MOBI")
    html = _page(_login(env), f"/book/{book}")
    assert f'href="/read/{book}/pdf"' in html and 'id="convert-epub-btn"' in html
    epub_book = env.add_book("Has Epub", fmt="EPUB")
    _add_format(env, epub_book, "MOBI")
    assert "convert-epub-btn" not in _page(_login(env), f"/book/{epub_book}")


def test_convert_queues_the_existing_conversion_from_the_best_source(env, monkeypatch):
    book = env.add_book("To Convert", fmt="FB2")
    _add_format(env, book, "AZW3")
    calls = []
    from cps import helper
    monkeypatch.setattr(helper, "convert_book_format", lambda *args, **kw: calls.append(args) or None)
    resp = _login(env).post(f"/book/{book}/convert-epub", follow_redirects=True)
    assert resp.status_code == 200
    assert calls and calls[0][0] == book and calls[0][2:4] == ("AZW3", "EPUB")
    assert "Tasks page" in resp.get_data(as_text=True)


def test_next_in_series_links_the_next_higher_index(env):
    first, third, second = (env.add_book(t) for t in ("Book One", "Book Three", "Book Two"))
    for book, index in ((first, 1.0), (third, 3.0), (second, 2.0)):
        _put_in_series(env, book, "Saga", index)
    client = _login(env)
    html = _page(client, f"/book/{first}")
    assert f'href="/book/{second}">Next: Book Two (#2)' in html
    assert "Book Three (#3)" in _page(client, f"/book/{second}")
    assert 'id="next-in-series"' not in _page(client, f"/book/{third}")


def test_cover_quick_read_only_for_readable_formats(env):
    readable = env.add_book("Readable", fmt="EPUB")
    unreadable = env.add_book("Unreadable", fmt="AZW3")
    html = _page(_login(env), "/")
    assert f'href="/read/{readable}/epub"' in html
    assert f"/read/{unreadable}/" not in html


def test_shelf_menu_toggles_shelves_and_creates_want_to_read_on_first_use(env):
    book = env.add_book("Shelved")
    client = _login(env)
    menu = client.get(f"/shelf/book/{book}").get_json()
    assert menu["shelves"][0] == {"id": None, "name": "Want to read", "default": True, "in_shelf": False}

    added = client.post(f"/shelf/add/want-to-read/{book}").get_json()
    assert added["in_shelf"] and added["shelf_id"]
    shelf_id = added["shelf_id"]
    menu = client.get(f"/shelf/book/{book}").get_json()
    assert menu["shelves"][0]["id"] == shelf_id and menu["shelves"][0]["in_shelf"]
    assert len(menu["shelves"]) == 1  # the default shelf is not listed twice

    # Adding again is harmless; removing takes it off.
    assert client.post(f"/shelf/add/{shelf_id}/{book}").status_code == 200
    ub = env.ub
    ub.session.expire_all()
    assert ub.session.query(ub.BookShelf).filter(ub.BookShelf.shelf == shelf_id).count() == 1
    assert client.post(f"/shelf/remove/{shelf_id}/{book}").get_json()["in_shelf"] is False
    ub.session.expire_all()
    assert ub.session.query(ub.BookShelf).filter(ub.BookShelf.shelf == shelf_id).count() == 0


def test_other_users_cannot_change_a_private_shelf(env):
    book = env.add_book("Mine")
    shelf_id = _login(env).post(f"/shelf/add/want-to-read/{book}").get_json()["shelf_id"]
    env.add_user("other", password="pw2")
    other = _login(env, "other", "pw2")
    assert other.post(f"/shelf/remove/{shelf_id}/{book}").status_code == 403
    assert other.post(f"/shelf/add/{shelf_id}/{book}").status_code == 403
    assert all(s["id"] != shelf_id for s in other.get(f"/shelf/book/{book}").get_json()["shelves"])
    assert other.post("/shelf/add/4242/1").status_code == 404


def test_up_next_row_shows_want_to_read_below_continue_reading(env):
    waiting = env.add_book("Waiting Book")
    started = env.add_book("Started Book")
    client = _login(env)
    assert "Up next" not in _page(client, "/")  # no shelf yet, no row
    for book in (waiting, started):
        client.post(f"/shelf/add/want-to-read/{book}")
    ub = env.ub
    ub.session.add(ub.ReadBook(user_id=env.admin().id, book_id=started, read_status=ub.ReadBook.STATUS_IN_PROGRESS))
    ub.session.commit()

    html = _page(client, "/")
    up_next = html[html.index('id="up-next-heading"'):html.index('class="discover load-more"')]
    assert "Waiting Book" in up_next and "Started Book" not in up_next
    assert html.index('id="continue-reading-heading"') < html.index('id="up-next-heading"')
    # Continue Reading goes straight back into the reader.
    cont = html[html.index('id="continue-reading-heading"'):html.index('id="up-next-heading"')]
    assert f'href="/read/{started}/epub"' in cont
