# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Scoped reader positions: /ajax/progress/<id>?format=<> and the in-progress books query."""

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


def _library_uuid(env):
    import sqlite3
    con = sqlite3.connect(env.library_dir / "metadata.db")
    row = con.execute("SELECT uuid FROM library_id").fetchone()
    con.close()
    return row[0]

def _set_library_uuid(env, uuid):
    import sqlite3
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.execute("UPDATE library_id SET uuid=?", (uuid,))
    con.commit()
    con.close()

@pytest.mark.unit
class TestReaderPositionApi:
    def test_scoped_post_get_roundtrip(self, env):
        from cps import ub
        client = _login(env)
        bid = env.add_book("Scoped", fmt="EPUB")
        resp = client.post(f"/ajax/progress/{bid}?format=epub",
                           json={"cfi": "epubcfi(/6/4)", "percent": 0.4})
        assert resp.status_code == 200
        row = ub.session.query(ub.ReaderPosition).filter_by(book_id=bid).one()
        assert row.format == "epub"
        assert row.library_uuid == _library_uuid(env)
        got = client.get(f"/ajax/progress/{bid}?format=epub").get_json()
        assert got["cfi"] == "epubcfi(/6/4)" and got["format"] == "epub"
        assert got["percent"] == pytest.approx(0.4)

    def test_formats_are_independent(self, env):
        client = _login(env)
        bid = env.add_book("Two Fmt", fmt="EPUB")
        from cps import calibre_db, db
        book = calibre_db.get_book(bid)
        book.data.append(db.Data(bid, "PDF", 1, "Two Fmt"))
        calibre_db.session.commit()
        client.post(f"/ajax/progress/{bid}?format=epub",
                    json={"cfi": "epubcfi(/6/4)", "percent": 0.4})
        client.post(f"/ajax/progress/{bid}?format=pdf",
                    json={"cfi": "page:7", "percent": 0.7})
        epub = client.get(f"/ajax/progress/{bid}?format=epub").get_json()
        pdf = client.get(f"/ajax/progress/{bid}?format=pdf").get_json()
        assert epub["cfi"] == "epubcfi(/6/4)" and pdf["cfi"] == "page:7"

    @pytest.mark.parametrize("fmt", ["DJVU", "DJV"])
    def test_djvu_positions_are_pages(self, env, fmt):
        client = _login(env)
        bid = env.add_book("Scanned " + fmt, fmt=fmt)
        url = f"/ajax/progress/{bid}?format={fmt.lower()}"
        assert client.post(url, json={"cfi": "epubcfi(/6/4)", "percent": 0.1}).status_code == 400
        assert client.post(url, json={"cfi": "page:0", "percent": 0.1}).status_code == 400
        assert client.post(url, json={"cfi": "page:12", "percent": 0.5}).status_code == 200
        got = client.get(url).get_json()
        assert got["cfi"] == "page:12" and got["format"] == fmt.lower()

    def test_cfi_and_format_guards(self, env):
        client = _login(env)
        bid = env.add_book("Guarded", fmt="EPUB")
        assert client.post(f"/ajax/progress/{bid}?format=epub",
                           json={"cfi": "page:3", "percent": 0.1}).status_code == 400
        assert client.post(f"/ajax/progress/{bid}?format=mobi",
                           json={"cfi": "epubcfi(/1)", "percent": 0.1}).status_code == 400
        assert client.post(f"/ajax/progress/{bid}?format=epub&format=pdf",
                           json={"cfi": "epubcfi(/1)", "percent": 0.1,
                                 "format": "epub"}).status_code == 400
        assert client.post(f"/ajax/progress/{bid}?format=epub",
                           json={"cfi": "epubcfi(/1)", "percent": 2}).status_code == 400
        assert client.post(f"/ajax/progress/{bid}?format=epub",
                           json={"cfi": "", "percent": 0.1}).status_code == 400
        assert client.post(f"/ajax/progress/{bid}?format=",
                           json={"cfi": "epubcfi(/1)", "percent": 0.1}).status_code == 400
        assert client.post(f"/ajax/progress/{bid}?format=epub",
                           json={"cfi": "epubcfi(/1)", "percent": 0.1,
                                 "format": "pdf"}).status_code == 400

    def test_format_type_guards(self, env):
        from cps import ub
        client = _login(env)
        bid = env.add_book("Typed", fmt="EPUB")
        for bad in ({"a": 1}, 3, True, ""):
            resp = client.post(f"/ajax/progress/{bid}",
                               json={"cfi": "epubcfi(/1)", "percent": 0.1,
                                     "format": bad})
            assert resp.status_code == 400, bad
        assert ub.session.query(ub.ReaderPosition).filter_by(book_id=bid).count() == 0
        assert ub.session.query(ub.WebReaderProgress).filter_by(book_id=bid).count() == 0

    def test_missing_library_identity_refuses(self, env):
        client = _login(env)
        bid = env.add_book("NoLib", fmt="EPUB")
        original = _library_uuid(env)
        _set_library_uuid(env, "")
        try:
            assert client.get(f"/ajax/progress/{bid}?format=epub").status_code == 503
            assert client.post(f"/ajax/progress/{bid}?format=epub",
                               json={"cfi": "epubcfi(/1)", "percent": 0.1}).status_code == 503
        finally:
            _set_library_uuid(env, original)

    def test_legacy_rows_bound_to_first_library(self, env):
        from cps import ub
        client = _login(env)
        bid = env.add_book("Bound", fmt="EPUB")
        admin = env.admin()
        ub.session.add(ub.WebReaderProgress(user_id=admin.id, book_id=bid,
                                            cfi="epubcfi(/3)", percent=0.3))
        ub.session_commit()
        lib_a = _library_uuid(env)
        got = client.get(f"/ajax/progress/{bid}?format=epub").get_json()
        assert got["cfi"] == "epubcfi(/3)"
        binding = ub.session.query(ub.ReaderLegacyLibrary).filter_by(id=1).one()
        assert binding.library_uuid == lib_a
        _set_library_uuid(env, "lib-B-uuid")
        try:
            got_b = client.get(f"/ajax/progress/{bid}?format=epub").get_json()
            assert got_b["cfi"] is None
            binding = ub.session.query(ub.ReaderLegacyLibrary).filter_by(id=1).one()
            assert binding.library_uuid == lib_a
        finally:
            _set_library_uuid(env, lib_a)

    def test_invalid_legacy_cfi_not_seeded(self, env):
        from cps import ub
        client = _login(env)
        bid = env.add_book("BadSeed", fmt="EPUB")
        admin = env.admin()
        ub.session.add(ub.WebReaderProgress(user_id=admin.id, book_id=bid,
                                            cfi="not-a-cfi", percent=0.4))
        ub.session_commit()
        got = client.get(f"/ajax/progress/{bid}?format=epub").get_json()
        assert got["cfi"] is None

    def test_legacy_path_still_uses_web_reader_progress(self, env):
        from cps import ub
        client = _login(env)
        bid = env.add_book("Legacy", fmt="EPUB")
        resp = client.post(f"/ajax/progress/{bid}",
                           json={"cfi": "epubcfi(/9)", "percent": 0.9})
        assert resp.status_code == 200
        assert ub.session.query(ub.WebReaderProgress).filter_by(book_id=bid).count() == 1
        assert ub.session.query(ub.ReaderPosition).filter_by(book_id=bid).count() == 0

    def test_legacy_seed_only_when_type_matches(self, env):
        from cps import ub
        client = _login(env)
        bid = env.add_book("Seed", fmt="EPUB")
        admin = env.admin()
        ub.session.add(ub.WebReaderProgress(user_id=admin.id, book_id=bid,
                                            cfi="epubcfi(/3)", percent=0.3))
        ub.session_commit()
        got = client.get(f"/ajax/progress/{bid}?format=epub").get_json()
        assert got["cfi"] == "epubcfi(/3)"
        bid2 = env.add_book("Seed2", fmt="EPUB")
        ub.session.add(ub.WebReaderProgress(user_id=admin.id, book_id=bid2,
                                            cfi="page:12", percent=0.5))
        ub.session_commit()
        got2 = client.get(f"/ajax/progress/{bid2}?format=epub").get_json()
        assert got2["cfi"] is None

    def test_positions_not_shared_between_users(self, env):
        client = _login(env)
        bid = env.add_book("Private", fmt="EPUB")
        client.post(f"/ajax/progress/{bid}?format=epub",
                    json={"cfi": "epubcfi(/6)", "percent": 0.6})
        env.add_user("reader2", password="reader2-pw-1")
        client2 = env.app.test_client()
        client2.post("/login", data={"username": "reader2", "password": "reader2-pw-1"})
        got = client2.get(f"/ajax/progress/{bid}?format=epub").get_json()
        assert got["cfi"] is None

@pytest.mark.unit
class TestInProgressReadingPositions:
    def test_scoped_position_wins_and_supplies_format(self, env):
        from cps import ub
        admin = env.admin()
        bid = env.add_book("Multi", fmt="EPUB")
        from cps import calibre_db, db
        book = calibre_db.get_book(bid)
        book.data.append(db.Data(bid, "PDF", 1, "Multi"))
        calibre_db.session.commit()
        ub.session.add(ub.ReadBook(user_id=admin.id, book_id=bid,
                                   read_status=ub.ReadBook.STATUS_IN_PROGRESS))
        ub.session.add(ub.WebReaderProgress(user_id=admin.id, book_id=bid,
                                            cfi="epubcfi(/2)", percent=0.2))
        lib = _library_uuid(env)
        ub.session.add(ub.ReaderPosition(user_id=admin.id, library_uuid=lib,
                                         book_id=bid, format="pdf",
                                         cfi="page:9", percent=0.9))
        ub.session_commit()
        # Continue names the scoped percent and opens the format last read in
        html = _login(env).get(f"/book/{bid}").get_data(as_text=True)
        assert "90%" in html and f"/read/{bid}/pdf" in html

    def test_missing_format_offers_no_reader_link(self, env):
        from cps import ub
        admin = env.admin()
        bid = env.add_book("Gone Format", fmt="EPUB")
        ub.session.add(ub.ReadBook(user_id=admin.id, book_id=bid,
                                   read_status=ub.ReadBook.STATUS_IN_PROGRESS))
        ub.session.add(ub.ReaderPosition(user_id=admin.id, library_uuid=_library_uuid(env),
                                         book_id=bid, format="pdf",
                                         cfi="page:3", percent=0.3))
        ub.session_commit()
        html = _login(env).get(f"/book/{bid}").get_data(as_text=True)
        assert f"/read/{bid}/pdf" not in html and f"/read/{bid}/epub" in html

    def test_other_library_positions_ignored(self, env):
        from cps import ub
        admin = env.admin()
        bid = env.add_book("Elsewhere", fmt="EPUB")
        ub.session.add(ub.ReadBook(user_id=admin.id, book_id=bid,
                                   read_status=ub.ReadBook.STATUS_IN_PROGRESS))
        ub.session.add(ub.ReaderPosition(user_id=admin.id, library_uuid="otherlib",
                                         book_id=bid, format="epub",
                                         cfi="epubcfi(/5)", percent=0.5))
        ub.session_commit()
        html = _login(env).get(f"/book/{bid}").get_data(as_text=True)
        assert "50%" not in html

    def test_multiple_formats_pick_newest(self, env):
        from datetime import datetime, timedelta, timezone
        from cps import ub, calibre_db, db
        admin = env.admin()
        lib = _library_uuid(env)
        bid = env.add_book("TwoFmtPos", fmt="EPUB")
        book = calibre_db.get_book(bid)
        book.data.append(db.Data(bid, "PDF", 1, "TwoFmtPos"))
        calibre_db.session.commit()
        ub.session.add(ub.ReadBook(user_id=admin.id, book_id=bid,
                                   read_status=ub.ReadBook.STATUS_IN_PROGRESS))
        now = datetime.now(timezone.utc)
        ub.session.add(ub.ReaderPosition(user_id=admin.id, library_uuid=lib,
                                         book_id=bid, format="epub",
                                         cfi="epubcfi(/1)", percent=0.1,
                                         last_modified=now - timedelta(hours=2)))
        ub.session.add(ub.ReaderPosition(user_id=admin.id, library_uuid=lib,
                                         book_id=bid, format="pdf",
                                         cfi="page:4", percent=0.4,
                                         last_modified=now - timedelta(hours=1)))
        ub.session.add(ub.ReaderPosition(user_id=admin.id, library_uuid="elsewhere",
                                         book_id=bid, format="epub",
                                         cfi="epubcfi(/9)", percent=0.9,
                                         last_modified=now))
        ub.session_commit()
        html = _login(env).get(f"/book/{bid}").get_data(as_text=True)
        assert "40%" in html and f"/read/{bid}/pdf" in html


@pytest.mark.unit
class TestBookPageResume:
    def test_in_progress_book_offers_to_continue(self, env):
        client = _login(env)
        bid = env.add_book("Halfway", fmt="EPUB")
        client.post(f"/ajax/progress/{bid}?format=epub",
                    json={"cfi": "epubcfi(/6/4)", "percent": 0.42})
        html = client.get(f"/book/{bid}").get_data(as_text=True)
        assert "Continue" in html and "42%" in html
        assert f'href="/read/{bid}/epub"' in html

    def test_unstarted_book_just_reads(self, env):
        client = _login(env)
        bid = env.add_book("Fresh", fmt="EPUB")
        html = client.get(f"/book/{bid}").get_data(as_text=True)
        assert "book-resume-percent" not in html
