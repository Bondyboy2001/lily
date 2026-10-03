"""A save that doesn't send the description leaves it as it is. The book page's Fetch Metadata
sends it only when a result fills it; it used to be saved as the text "None"."""
import sqlite3

import pytest

from .lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        yield env


def _description(env, book_id):
    con = sqlite3.connect(env.library_dir / "metadata.db")
    try:
        row = con.execute("SELECT text FROM comments WHERE book=?", (book_id,)).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def _save(env, book_id, **extra):
    (env.library_dir / "Frank Herbert" / "Dune").mkdir(parents=True, exist_ok=True)
    client = env.app.test_client()
    client.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
    client.post(f"/admin/book/{book_id}", follow_redirects=True,
                data={"title": "Dune", "authors": "Frank Herbert", "detail_view": "1", **extra})


def test_a_save_without_the_description_keeps_it(env):
    book_id = env.add_book("Dune", author="Frank Herbert")
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.execute("INSERT INTO comments (book, text) VALUES (?, ?)", (book_id, "<p>Spice.</p>"))
    con.commit()
    con.close()
    _save(env, book_id, pubdate="1965-08-01")
    assert _description(env, book_id) == "<p>Spice.</p>"


def test_a_book_without_a_description_does_not_get_none(env):
    book_id = env.add_book("Dune", author="Frank Herbert")
    _save(env, book_id, pubdate="1965-08-01")
    assert _description(env, book_id) in (None, "")


def test_a_sent_description_is_still_saved(env):
    book_id = env.add_book("Dune", author="Frank Herbert")
    _save(env, book_id, comments="<p>Desert planet.</p>")
    assert "Desert planet." in _description(env, book_id)
