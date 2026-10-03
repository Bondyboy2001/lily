"""Shelf CRUD/privacy and simple search, through the real Flask routes."""

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

    for book in (b1, b2):
        assert admin.post(f"/shelf/{sid}/book/{book}", json={"on": True}).get_json()["on"] is True
    assert admin.post(f"/shelf/{sid}/book/9999", json={"on": True}).status_code == 404
    # Putting a book on twice keeps one entry
    assert admin.post(f"/shelf/{sid}/book/{b1}", json={"on": True}).get_json()["count"] == 2

    html = admin.get(f"/shelf/{sid}").get_data(as_text=True)
    assert "First" in html and "Second" in html
    ub = env.ub
    ub.session.expire_all()
    assert ub.session.query(ub.BookShelf).filter(ub.BookShelf.shelf == sid).count() == 2

    assert admin.post(f"/shelf/delete/{sid}").status_code == 302
    ub.session.expire_all()
    assert ub.session.query(ub.Shelf).filter(ub.Shelf.id == sid).first() is None
    assert ub.session.query(ub.BookShelf).filter(ub.BookShelf.shelf == sid).count() == 0


def test_delete_shelf_control_is_only_on_edit_page(env):
    admin = _client(env, env.admin().name, ADMIN_PASSWORD)
    admin.post("/shelf/create", data={"title": "Editable"})
    sid = _shelf_id(env, "Editable")

    browse = admin.get(f"/shelf/{sid}")
    assert browse.status_code == 200
    html = browse.get_data(as_text=True)
    assert 'id="delete_shelf"' not in html
    assert 'id="edit_shelf"' in html

    edit = admin.get(f"/shelf/edit/{sid}")
    assert edit.status_code == 200
    html = edit.get_data(as_text=True)
    assert 'id="delete_shelf"' in html
    assert f'data-action="/shelf/delete/{sid}"' in html
    assert 'id="GeneralDeleteModal"' in html
    assert 'name="csrf_token"' in html


def test_create_shelf_has_no_delete_control(env):
    admin = _client(env, env.admin().name, ADMIN_PASSWORD)
    response = admin.get("/shelf/create")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="delete_shelf"' not in html
    assert 'id="GeneralDeleteModal"' not in html


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
    admin.post(f"/shelf/{sid}/book/{book}", json={"on": True})

    env.add_user("other", password="pw2")
    other = _client(env, "other", "pw2")
    resp = other.get(f"/shelf/{sid}", follow_redirects=False)
    assert resp.status_code == 302 and f"/shelf/{sid}" not in resp.headers["Location"]
    assert "not accessible" in other.get(f"/shelf/{sid}", follow_redirects=True).get_data(as_text=True)

    denied = other.post(f"/shelf/{sid}/book/{book}", json={"on": True})
    assert denied.status_code == 403
    other.post(f"/shelf/delete/{sid}")
    env.ub.session.expire_all()
    assert env.ub.session.query(env.ub.Shelf).filter(env.ub.Shelf.id == sid).first() is not None


def test_add_to_missing_shelf_or_without_on_flag(env):
    admin = _client(env, env.admin().name, ADMIN_PASSWORD)
    book = env.add_book("Loose")
    assert admin.post(f"/shelf/4242/book/{book}", json={"on": True}).status_code == 404
    admin.post("/shelf/create", data={"title": "Empty"})
    sid = _shelf_id(env, "Empty")
    assert admin.post(f"/shelf/{sid}/book/{book}", json={}).status_code == 400


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


def test_simple_search_finds_description_words_and_a_papers_id(env):
    import sqlite3
    admin = _client(env, env.admin().name, ADMIN_PASSWORD)
    novel = env.add_book("A Summer Book", author="Jane Roe")
    paper = env.add_book("Graph Growth", author="Sam Poe")
    env.add_book("Other Book", author="Ann Doe")
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.execute("INSERT INTO comments (book, text) VALUES (?, ?)",
                (novel, "<p><span>Nick spends a summer next door to Daisy Buchanan.</span></p>"))
    con.execute("INSERT INTO identifiers (book, type, val) VALUES (?, 'arxiv', '2601.22106')", (paper,))
    con.execute("INSERT INTO identifiers (book, type, val) VALUES (?, 'doi', '10.48550/arXiv.2601.22106')", (paper,))
    con.commit()
    con.close()

    def found(query):
        html = admin.get("/search", query_string={"query": query}, follow_redirects=True).get_data(as_text=True)
        return {t for t in ("A Summer Book", "Graph Growth", "Other Book") if t in html}

    assert found("Daisy Buchanan") == {"A Summer Book"}
    for query in ("2601.22106", "arXiv:2601.22106", "https://arxiv.org/abs/2601.22106v2",
                  "10.48550/arxiv.2601.22106"):
        assert found(query) == {"Graph Growth"}, query
    # Markup in a description and part of an id are not matches
    assert found("span") == set() and found("2601") == set()


def test_search_words_split_on_spaces_and_commas_and_keep_quoted_phrases():
    from cps.db import search_words, like_pattern
    assert search_words("Dune  Herbert") == ["Dune", "Herbert"]
    assert search_words("Herbert, Frank") == ["Herbert", "Frank"]
    assert search_words('"Café Society" lee') == ["Café Society", "lee"]
    assert search_words('"unclosed quote') == ["unclosed", "quote"]
    assert search_words("dune DUNE") == ["dune"]
    assert search_words(",") == [","]
    assert len(search_words(" ".join(f"w{n}" for n in range(100)))) == 20
    assert like_pattern("10%_\\") == "%10\\%\\_\\\\%"


def test_simple_search_matches_every_word_across_fields(env):
    import sqlite3
    admin = _client(env, env.admin().name, ADMIN_PASSWORD)
    dune = env.add_book("Dune", author="Frank Herbert", tags=("Science Fiction",))
    env.add_book("Dune Messiah Notes", author="Someone Else")
    env.add_book("Children of Herbert", author="Ann Lee")
    paper = env.add_book("Graph Growth", author="Sam Poe")
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.execute("INSERT INTO comments (book, text) VALUES (?, ?)",
                (dune, "<p>A desert planet called Arrakis.</p>"))
    con.execute("INSERT INTO identifiers (book, type, val) VALUES (?, 'arxiv', '2601.22106')", (paper,))
    con.commit()
    con.close()

    def found(query):
        html = admin.get("/search", query_string={"query": query}, follow_redirects=True).get_data(as_text=True)
        return {t for t in ("Frank Herbert", "Dune Messiah Notes", "Children of Herbert", "Graph Growth")
                if t in html}

    assert found("dune herbert") == {"Frank Herbert"}
    assert found("herbert fiction") == {"Frank Herbert"}  # author + tag
    assert found("arrakis herbert") == {"Frank Herbert"}  # description + author
    assert found("dune nosuchword") == set()
    assert found("growth 2601.22106") == {"Graph Growth"}  # title + identifier
    assert found("arXiv: 2601.22106") == {"Graph Growth"}  # the whole term is one id
    # a quoted phrase must appear as it stands
    assert found('"messiah notes"') == {"Dune Messiah Notes"}
    assert found('"notes messiah"') == set()
    # LIKE wildcards typed by the user match literally
    assert found("d_ne") == set() and found("du%") == set()


def test_the_editor_returns_to_the_page_it_was_opened_from(env):
    admin = _client(env, env.admin().name, ADMIN_PASSWORD)
    book = env.add_book("Round Trip", author="Jane Roe")
    page = admin.get(f"/admin/book/{book}", headers={"Referer": "http://localhost/search?query=round"})
    html = page.get_data(as_text=True)
    assert 'name="next" value="/search?query=round"' in html
    assert 'href="/search?query=round" id="edit_cancel"' in html
    # Save goes where the "next" field says, when it is a page on this site and not the editor
    from cps.editbooks import _return_to
    with env.app.test_request_context(f"/admin/book/{book}", base_url="http://localhost"):
        assert _return_to("/search?query=round") == "/search?query=round"
        assert _return_to("http://localhost/shelf/3") == "/shelf/3"
        for bad in ("https://evil.example/x", "//evil.example/x", "/\\evil.example", f"/admin/book/{book}", "", None):
            assert _return_to(bad) is None, bad
    html = admin.get(f"/admin/book/{book}", headers={"Referer": "https://evil.example/"}).get_data(as_text=True)
    assert 'name="next"' not in html and f'href="/book/{book}" id="edit_cancel"' in html
