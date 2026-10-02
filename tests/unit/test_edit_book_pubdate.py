"""Saving a book's published date: a year or a year and month, as Fetch Metadata's results
often give, is a date; anything else is refused and the book keeps the date it had."""
import sqlite3

import pytest

from .lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        yield env


def _save(env, book_id, pubdate):
    # The save moves the book's folder to calibre's "Title (id)" form, so it has to exist
    (env.library_dir / "Frank Herbert" / "Dune").mkdir(parents=True, exist_ok=True)
    client = env.app.test_client()
    client.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
    response = client.post(f"/admin/book/{book_id}", follow_redirects=True,
                           data={"title": "Dune", "authors": "Frank Herbert", "pubdate": pubdate})
    con = sqlite3.connect(env.library_dir / "metadata.db")
    try:
        stored = con.execute("SELECT pubdate FROM books WHERE id=?", (book_id,)).fetchone()[0]
    finally:
        con.close()
    return response.get_data(as_text=True), stored[:10]


@pytest.mark.parametrize("typed, stored", [
    ("1965", "1965-01-01"),        # Google Books gives the year alone for older books
    ("1965-08", "1965-08-01"),
    ("1965-08-01", "1965-08-01"),
])
def test_a_year_or_a_month_is_saved_as_its_first_day(env, typed, stored):
    book_id = env.add_book("Dune", author="Frank Herbert")
    page, saved = _save(env, book_id, typed)
    assert saved == stored
    assert "does not match format" not in page and "is not a date" not in page


def test_text_that_is_no_date_is_refused_and_the_old_date_kept(env):
    book_id = env.add_book("Dune", author="Frank Herbert")  # dated 2026-01-01 by the fixture
    page, saved = _save(env, book_id, "next spring")
    assert saved == "2026-01-01"
    assert "is not a date" in page and "does not match format" not in page


def test_fetch_metadata_fills_the_date_field_with_a_whole_date():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2] / "cps/static/js/get_meta.js").read_text(encoding="utf-8")
    assert '$("#pubdate").val(fullDate(book.publishedDate))' in js
