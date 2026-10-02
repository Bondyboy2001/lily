"""The edit form sent twice (Apply or Save pressed twice) saves once and fails neither time,
also on the first start after an update, when the one-time startup passes have run."""
import sqlite3

import pytest

from .lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        yield env


def _identifiers(env, book_id):
    con = sqlite3.connect(env.library_dir / "metadata.db")
    try:
        return con.execute("SELECT type, val FROM identifiers WHERE book=?", (book_id,)).fetchall()
    finally:
        con.close()


@pytest.mark.parametrize("title, authors", [
    ("Dune", "Frank Herbert"),
    ("Dune Messiah", "Frank Herbert"),   # the first save renames the book
    ("Dune", "F. Herbert"),              # the first save renames its author
])
def test_the_same_form_saved_twice_keeps_the_identifier_it_added(env, tmp_path, title, authors):
    from cps import calibre_db
    from cps.services import arxiv_shelf
    book_id = env.add_book("Dune", author="Frank Herbert")
    (env.library_dir / "Frank Herbert" / "Dune").mkdir(parents=True, exist_ok=True)
    # As at startup: the app's session is the one its thread is handed, and the arXiv shelf's pass runs
    calibre_db.session = calibre_db.session_factory()
    calibre_db.session.expire_on_commit = True
    arxiv_shelf.replace_papers_shelf_once(str(tmp_path / "arxiv_shelf_v1"))
    client = env.app.test_client()
    client.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
    # Fetch Metadata added an identifier row to the form; the form was then sent twice
    form = {"title": title, "authors": authors, "pubdate": "1965", "comments": "",
            "identifier-type-new": "openlibrary", "identifier-val-new": "OL893415W"}
    for _ in range(2):
        page = client.post(f"/admin/book/{book_id}", follow_redirects=True, data=form).get_data(as_text=True)
        assert "save your changes" not in page
        assert "Metadata successfully updated" in page
    assert _identifiers(env, book_id) == [("openlibrary", "OL893415W")]
