# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Reader bookmarks: /ajax/bookmarks/<id>/<format>, several per book, per user."""

import pytest

from .lily_env import lily_env, ADMIN_PASSWORD


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        yield e


def _login(env, name=None, password=ADMIN_PASSWORD):
    client = env.app.test_client()
    client.post("/login", data={"username": name or env.admin().name, "password": password})
    return client


def _keys(client, url):
    resp = client.get(url)
    assert resp.status_code == 200, resp.data[:200]
    return [b["key"] for b in resp.get_json()["bookmarks"]]


@pytest.mark.unit
class TestReaderBookmarks:
    def test_several_bookmarks_per_book(self, env):
        client = _login(env)
        bid = env.add_book("Marked", fmt="EPUB")
        url = f"/ajax/bookmarks/{bid}/EPUB"
        assert client.get(url).get_json() == {"bookmarks": []}
        first = client.post(url, json={"key": "epubcfi(/6/4!/4/2/1:0)", "label": "  Chapter\n One ",
                                       "excerpt": "It was a dark night"})
        assert first.status_code == 201
        assert first.get_json()["label"] == "Chapter One"
        assert client.post(url, json={"key": "epubcfi(/6/8!/4/2/1:0)"}).status_code == 201
        assert _keys(client, url) == ["epubcfi(/6/4!/4/2/1:0)", "epubcfi(/6/8!/4/2/1:0)"]
        # The same position again is the bookmark already saved, not a second row.
        again = client.post(url, json={"key": "epubcfi(/6/4!/4/2/1:0)"})
        assert again.status_code == 200 and again.get_json()["id"] == first.get_json()["id"]
        assert len(_keys(client, url)) == 2

    def test_remove_by_delete_or_post(self, env):
        from cps import ub
        client = _login(env)
        bid = env.add_book("Unmarked", fmt="EPUB")
        url = f"/ajax/bookmarks/{bid}/epub"
        one = client.post(url, json={"key": "epubcfi(/6/2)"}).get_json()["id"]
        two = client.post(url, json={"key": "epubcfi(/6/6)"}).get_json()["id"]
        assert client.delete(f"{url}/{one}").status_code == 204
        assert _keys(client, url) == ["epubcfi(/6/6)"]
        assert client.post(f"{url}/{two}/remove").status_code == 204
        assert _keys(client, url) == []
        # Already gone is still fine.
        assert client.delete(f"{url}/{two}").status_code == 204
        assert ub.session.query(ub.Bookmark).filter_by(book_id=bid).count() == 0

    @pytest.mark.parametrize("fmt", ["PDF", "DJVU", "DJV"])
    def test_paged_formats_take_pages(self, env, fmt):
        client = _login(env)
        bid = env.add_book("Paged " + fmt, fmt=fmt)
        url = f"/ajax/bookmarks/{bid}/{fmt}"
        assert client.post(url, json={"key": "epubcfi(/6/4)"}).status_code == 400
        assert client.post(url, json={"key": "page:0"}).status_code == 400
        assert client.post(url, json={"key": "page:12"}).status_code == 201
        assert client.post(url, json={"key": "page:3"}).status_code == 201
        assert _keys(client, url) == ["page:12", "page:3"]

    def test_guards(self, env):
        client = _login(env)
        bid = env.add_book("Guarded", fmt="EPUB")
        url = f"/ajax/bookmarks/{bid}/EPUB"
        for body in ({"key": ""}, {"key": 3}, {"key": "page:3"}, {"key": "epubcfi(" + "x" * 5000 + ")"}, []):
            assert client.post(url, json=body).status_code == 400, body
        assert client.post(url, data={"key": "epubcfi(/6/2)"}).status_code == 400
        # A format the book doesn't have, one without bookmarks, and a missing book.
        assert client.get(f"/ajax/bookmarks/{bid}/PDF").status_code == 404
        assert client.get(f"/ajax/bookmarks/{bid}/MP3").status_code == 404
        assert client.get("/ajax/bookmarks/999999/EPUB").status_code == 404

    def test_signed_out_gets_nothing(self, env):
        bid = env.add_book("Private", fmt="EPUB")
        resp = env.app.test_client().get(f"/ajax/bookmarks/{bid}/EPUB")
        assert resp.status_code in (302, 401, 403)

    def test_bookmarks_are_per_user(self, env):
        client = _login(env)
        bid = env.add_book("Shared Book", fmt="EPUB")
        url = f"/ajax/bookmarks/{bid}/EPUB"
        mine = client.post(url, json={"key": "epubcfi(/6/2)"}).get_json()["id"]
        env.add_user("reader2", password="reader2-pw-1")
        other = _login(env, "reader2", "reader2-pw-1")
        assert _keys(other, url) == []
        # Someone else's bookmark can't be removed.
        assert other.delete(f"{url}/{mine}").status_code == 204
        assert _keys(client, url) == ["epubcfi(/6/2)"]

    def test_single_bookmark_rows_still_listed(self, env):
        # Rows from when a book had one bookmark: no label or excerpt, format in any case.
        from cps import ub
        client = _login(env)
        bid = env.add_book("Old Mark", fmt="EPUB")
        ub.session.add(ub.Bookmark(user_id=env.admin().id, book_id=bid, format="EPUB",
                                   bookmark_key="epubcfi(/6/10)"))
        ub.session.commit()
        got = client.get(f"/ajax/bookmarks/{bid}/epub").get_json()["bookmarks"]
        assert [(b["key"], b["label"], b["excerpt"]) for b in got] == [("epubcfi(/6/10)", "", "")]
        assert client.post(f"/ajax/bookmarks/{bid}/EPUB", json={"key": "epubcfi(/6/12)"}).status_code == 201
        assert len(_keys(client, f"/ajax/bookmarks/{bid}/EPUB")) == 2


@pytest.mark.unit
def test_bookmark_columns_are_added_to_old_databases(tmp_path):
    import sqlalchemy
    from sqlalchemy.orm import sessionmaker
    from cps import ub
    engine = sqlalchemy.create_engine("sqlite:///" + str(tmp_path / "app.db"))
    with engine.begin() as con:
        con.execute(sqlalchemy.text("CREATE TABLE bookmark (id INTEGER PRIMARY KEY, user_id INTEGER, "
                                    "book_id INTEGER, format VARCHAR, bookmark_key VARCHAR)"))
        con.execute(sqlalchemy.text("INSERT INTO bookmark (user_id, book_id, format, bookmark_key) "
                                    "VALUES (1, 2, 'EPUB', 'epubcfi(/6/2)')"))
    session = sessionmaker(bind=engine)()
    ub.migrate_bookmark_table(engine, session)
    row = session.query(ub.Bookmark).one()
    assert row.bookmark_key == "epubcfi(/6/2)" and row.label is None and row.excerpt is None
    session.close()
