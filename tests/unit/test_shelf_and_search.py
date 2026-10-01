"""Shelf CRUD/privacy and simple search, through the real Flask routes."""

import sqlite3

import pytest

from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path):
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(e.app)
        from cps.shelf import shelf
        if shelf.name not in e.app.blueprints:
            e.app.register_blueprint(shelf)
        yield e


def _client(env, name, password):
    c = env.app.test_client()
    assert c.post("/login", data={"username": name, "password": password}).status_code in (200, 302)
    return c


def _shelf_id(env, title):
    ub = env.ub
    ub.session.expire_all()
    return ub.session.query(ub.Shelf).filter(ub.Shelf.name == title).one().id


def test_create_shelf_add_books_and_delete(env):
    admin = _client(env, env.admin().name, ADMIN_PASSWORD)
    b1, b2 = env.add_book("First"), env.add_book("Second")
    assert admin.post("/shelf/create", data={"title": "Favs"}).status_code == 302
    sid = _shelf_id(env, "Favs")

    resp = admin.post("/shelf/add_selected_to_shelf", json={"shelf_id": sid, "book_ids": [b1, b2, 9999]})
    assert resp.status_code == 207  # partial success: the unknown id is reported, the rest are added
    again = admin.post("/shelf/add_selected_to_shelf", json={"shelf_id": sid, "book_ids": [b1]})
    assert "already" in again.get_data(as_text=True)

    html = admin.get(f"/shelf/{sid}").get_data(as_text=True)
    assert "First" in html and "Second" in html
    ub = env.ub
    ub.session.expire_all()
    assert ub.session.query(ub.BookShelf).filter(ub.BookShelf.shelf == sid).count() == 2

    assert admin.post(f"/shelf/delete/{sid}").status_code == 302
    ub.session.expire_all()
    assert ub.session.query(ub.Shelf).filter(ub.Shelf.id == sid).first() is None
    assert ub.session.query(ub.BookShelf).filter(ub.BookShelf.shelf == sid).count() == 0


def test_shelf_names_must_be_unique_per_scope(env):
    admin = _client(env, env.admin().name, ADMIN_PASSWORD)
    admin.post("/shelf/create", data={"title": "Dupe"})
    admin.post("/shelf/create", data={"title": "Dupe"})
    ub = env.ub
    ub.session.expire_all()
    assert ub.session.query(ub.Shelf).filter(ub.Shelf.name == "Dupe").count() == 1


def test_private_shelf_is_hidden_from_other_users_and_not_editable(env):
    admin = _client(env, env.admin().name, ADMIN_PASSWORD)
    book = env.add_book("Secret Book")
    admin.post("/shelf/create", data={"title": "Mine"})
    sid = _shelf_id(env, "Mine")
    admin.post("/shelf/add_selected_to_shelf", json={"shelf_id": sid, "book_ids": [book]})

    env.add_user("other", password="pw2")
    other = _client(env, "other", "pw2")
    resp = other.get(f"/shelf/{sid}", follow_redirects=False)
    assert resp.status_code == 302 and f"/shelf/{sid}" not in resp.headers["Location"]
    assert "not accessible" in other.get(f"/shelf/{sid}", follow_redirects=True).get_data(as_text=True)

    denied = other.post("/shelf/add_selected_to_shelf", json={"shelf_id": sid, "book_ids": [book]})
    assert denied.status_code == 403
    other.post(f"/shelf/delete/{sid}")
    env.ub.session.expire_all()
    assert env.ub.session.query(env.ub.Shelf).filter(env.ub.Shelf.id == sid).first() is not None


def test_add_to_missing_shelf_and_empty_selection(env):
    admin = _client(env, env.admin().name, ADMIN_PASSWORD)
    assert admin.post("/shelf/add_selected_to_shelf", json={"shelf_id": 4242, "book_ids": [1]}).status_code == 404
    admin.post("/shelf/create", data={"title": "Empty"})
    sid = _shelf_id(env, "Empty")
    assert admin.post("/shelf/add_selected_to_shelf", json={"shelf_id": sid, "book_ids": []}).status_code == 400


def test_simple_search_finds_titles_and_survives_hostile_input(env):
    admin = _client(env, env.admin().name, ADMIN_PASSWORD)
    env.add_book("The Quiet Garden", author="Jane Roe")
    env.add_book("Loud Machines", author="Sam Poe")

    resp = admin.get("/search?query=Quiet", follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200 and "The Quiet Garden" in html and "Loud Machines" not in html
    assert "Sam Poe" not in admin.get("/search?query=Roe", follow_redirects=True).get_data(as_text=True)

    for hostile in ("' OR 1=1 --", "%", "_", "\\", "<script>alert(1)</script>", "x" * 5000):
        r = admin.get("/search", query_string={"query": hostile}, follow_redirects=True)
        assert r.status_code == 200
        assert "<script>alert(1)</script>" not in r.get_data(as_text=True)
    assert admin.get("/search").status_code == 200  # empty query renders the search form


def _search_html(client, query):
    return client.get("/search", query_string={"query": query}, follow_redirects=True).get_data(as_text=True)


def test_simple_search_folds_accents_and_matches_each_word_against_any_author(env):
    admin = _client(env, env.admin().name, ADMIN_PASSWORD)
    env.add_book("Café Society", author="Ann Lee", tags=("Ångström",))
    env.add_book("Plain Book", author="Bob Stone")
    for query in ("cafe", "CAFÉ", "angstrom"):
        html = _search_html(admin, query)
        assert "Café Society" in html and "Plain Book" not in html, query

    # a second author on the same book: each search word may match a different author
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.execute("INSERT INTO authors (name, sort) VALUES ('Zed Quill', 'Quill, Zed')")
    con.execute("INSERT INTO books_authors_link (book, author) SELECT b.id, a.id FROM books b, authors a "
                "WHERE b.title='Plain Book' AND a.name='Zed Quill'")
    con.commit()
    con.close()
    html = _search_html(admin, "stone quill")
    assert "Plain Book" in html and "Café Society" not in html


def test_simple_search_statement_count_does_not_grow_with_matches(env):
    from sqlalchemy import event
    from cps import db

    admin = _client(env, env.admin().name, ADMIN_PASSWORD)

    def statements_for(query):
        seen = []

        def record(conn, cursor, statement, *args):
            seen.append(statement)
        event.listen(db.CalibreDB.engine, "before_cursor_execute", record)
        try:
            assert admin.get("/search", query_string={"query": query}, follow_redirects=True).status_code == 200
        finally:
            event.remove(db.CalibreDB.engine, "before_cursor_execute", record)
        return seen

    env.add_book("Plain Book", author="Bob Stone")
    few = statements_for("Plain")
    for i in range(12):
        env.add_book(f"Plain Extra {i}", author=f"Writer {i}")
    many = statements_for("Plain")
    assert len(many) == len(few), many
    page_queries = [s for s in many if s.lstrip().startswith("SELECT books.id AS books_id")]
    assert page_queries and all("LIMIT" in s for s in page_queries), page_queries
    assert any("count(*)" in s for s in many)
