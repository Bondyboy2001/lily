# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The Trash: deleting a book moves it (files and rows) to <library>/.lily-trash, and the
Trash page puts it back. Pure parts run on a plain sqlite copy of the empty library;
the rest goes through the real Flask routes on a temp library with real files."""

import errno
import json
import os
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from cps import trash_store as store
from tests.unit.lily_env import EMPTY_LIBRARY_DB, lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------- pure

def _connect(path):
    con = sqlite3.connect(path)
    con.create_function("title_sort", 1, lambda t: t)
    con.create_function("uuid4", 0, lambda: str(uuid.uuid4()))
    return con


@pytest.fixture
def meta_db(tmp_path):
    path = tmp_path / "metadata.db"
    shutil.copy(EMPTY_LIBRARY_DB, path)
    con = _connect(path)
    cur = con.cursor()
    cur.execute("INSERT INTO books (id, title, sort, author_sort, path, uuid, has_cover) "
                "VALUES (1, 'Dune', 'Dune', 'Herbert, Frank', 'Frank Herbert/Dune (1)', 'u-1', 1)")
    cur.execute("INSERT INTO authors (name, sort) VALUES ('Frank Herbert', 'Herbert, Frank')")
    cur.execute("INSERT INTO books_authors_link (book, author) VALUES (1, 1)")
    cur.execute("INSERT INTO tags (name) VALUES ('SF')")
    cur.execute("INSERT INTO books_tags_link (book, tag) VALUES (1, 1)")
    cur.execute("INSERT INTO series (name, sort) VALUES ('Dune', 'Dune')")
    cur.execute("INSERT INTO books_series_link (book, series) VALUES (1, 1)")
    cur.execute("INSERT INTO languages (lang_code) VALUES ('eng')")
    cur.execute("INSERT INTO books_languages_link (book, lang_code, item_order) VALUES (1, 1, 0)")
    cur.execute("INSERT INTO ratings (rating) VALUES (8)")
    cur.execute("INSERT INTO books_ratings_link (book, rating) VALUES (1, 1)")
    cur.execute("INSERT INTO publishers (name) VALUES ('Chilton')")
    cur.execute("INSERT INTO books_publishers_link (book, publisher) VALUES (1, 1)")
    cur.execute("INSERT INTO identifiers (book, type, val) VALUES (1, 'isbn', '9780441013593')")
    cur.execute("INSERT INTO comments (book, text) VALUES (1, 'Spice.')")
    cur.execute("INSERT INTO data (book, format, uncompressed_size, name) VALUES (1, 'EPUB', 10, 'Dune - Frank Herbert')")
    # A normalized (tags-like) and a plain custom column
    cur.execute("INSERT INTO custom_columns (label, name, datatype, is_multiple, normalized) "
                "VALUES ('shelfmark', 'Shelfmark', 'text', 1, 1)")
    cur.execute("CREATE TABLE custom_column_1 (id INTEGER PRIMARY KEY, value TEXT NOT NULL COLLATE NOCASE, link TEXT NOT NULL DEFAULT '', UNIQUE(value))")
    cur.execute("CREATE TABLE books_custom_column_1_link (id INTEGER PRIMARY KEY, book INTEGER NOT NULL, value INTEGER NOT NULL, UNIQUE(book, value))")
    cur.execute("INSERT INTO custom_column_1 (value) VALUES ('Attic')")
    cur.execute("INSERT INTO books_custom_column_1_link (book, value) VALUES (1, 1)")
    cur.execute("INSERT INTO custom_columns (label, name, datatype, is_multiple, normalized) "
                "VALUES ('pages', 'Pages', 'int', 0, 0)")
    cur.execute("CREATE TABLE custom_column_2 (id INTEGER PRIMARY KEY, book INTEGER, value INTEGER NOT NULL, UNIQUE(book))")
    cur.execute("INSERT INTO custom_column_2 (book, value) VALUES (1, 412)")
    con.commit()
    yield con
    con.close()


def _summary(con, book_id):
    q = lambda sql: [r[0] for r in con.execute(sql, (book_id,)).fetchall()]  # noqa: E731
    return {
        "book": con.execute("SELECT title, sort, uuid, has_cover FROM books WHERE id=?", (book_id,)).fetchone(),
        "authors": q("SELECT a.name FROM authors a JOIN books_authors_link l ON l.author=a.id WHERE l.book=?"),
        "tags": q("SELECT t.name FROM tags t JOIN books_tags_link l ON l.tag=t.id WHERE l.book=?"),
        "series": q("SELECT s.name FROM series s JOIN books_series_link l ON l.series=s.id WHERE l.book=?"),
        "langs": q("SELECT g.lang_code FROM languages g JOIN books_languages_link l ON l.lang_code=g.id WHERE l.book=?"),
        "rating": q("SELECT r.rating FROM ratings r JOIN books_ratings_link l ON l.rating=r.id WHERE l.book=?"),
        "publisher": q("SELECT p.name FROM publishers p JOIN books_publishers_link l ON l.publisher=p.id WHERE l.book=?"),
        "ids": q("SELECT type || ':' || val FROM identifiers WHERE book=?"),
        "comment": q("SELECT text FROM comments WHERE book=?"),
        "formats": q("SELECT format FROM data WHERE book=?"),
        "cc1": q("SELECT c.value FROM custom_column_1 c JOIN books_custom_column_1_link l ON l.value=c.id WHERE l.book=?"),
        "cc2": q("SELECT value FROM custom_column_2 WHERE book=?"),
    }


def _delete_book(con, book_id):
    con.execute("DELETE FROM books WHERE id=?", (book_id,))  # Calibre's trigger removes the link rows
    con.execute("DELETE FROM books_custom_column_1_link WHERE book=?", (book_id,))
    con.execute("DELETE FROM custom_column_2 WHERE book=?", (book_id,))
    for table in ("authors", "tags", "series", "publishers", "custom_column_1"):
        con.execute("DELETE FROM %s" % table)  # orphaned entities are cleaned up by Lily too
    con.commit()


def test_capture_and_restore_rows_round_trip_keeps_id(meta_db):
    ex = store.SqliteExecutor(meta_db)
    before = _summary(meta_db, 1)
    snapshot = json.loads(json.dumps(store.capture_book_rows(ex, 1)))  # survives the JSON file
    _delete_book(meta_db, 1)
    assert meta_db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0

    new_id, path = store.restore_book_rows(ex, snapshot)
    meta_db.commit()
    assert (new_id, path) == (1, "Frank Herbert/Dune (1)")
    assert _summary(meta_db, 1) == before


def test_restore_takes_a_new_id_when_the_old_one_is_taken(meta_db):
    ex = store.SqliteExecutor(meta_db)
    snapshot = store.capture_book_rows(ex, 1)
    # Someone else got id 1 meanwhile (e.g. metadata.db was restored from a snapshot)
    meta_db.execute("UPDATE books SET title='Other', path='X/Other (1)' WHERE id=1")
    new_id, path = store.restore_book_rows(ex, snapshot)
    assert new_id != 1
    assert path == "Frank Herbert/Dune (%d)" % new_id
    assert _summary(meta_db, new_id)["tags"] == ["SF"]


def test_app_rows_skip_users_and_shelves_that_are_gone(tmp_path):
    con = sqlite3.connect(tmp_path / "app.db")
    con.executescript("""
        CREATE TABLE user (id INTEGER PRIMARY KEY);
        CREATE TABLE shelf (id INTEGER PRIMARY KEY);
        CREATE TABLE book_shelf_link (id INTEGER PRIMARY KEY, book_id INTEGER, "order" INTEGER, shelf INTEGER);
        CREATE TABLE book_read_link (id INTEGER PRIMARY KEY, book_id INTEGER, user_id INTEGER, read_status INTEGER);
        INSERT INTO user VALUES (1); INSERT INTO user VALUES (2);
        INSERT INTO shelf VALUES (5); INSERT INTO shelf VALUES (6);
        INSERT INTO book_shelf_link (book_id, "order", shelf) VALUES (7, 1, 5), (7, 2, 6);
        INSERT INTO book_read_link (book_id, user_id, read_status) VALUES (7, 1, 1), (7, 2, 2);
    """)
    ex = store.SqliteExecutor(con)
    captured = store.capture_app_rows(ex, 7)
    con.executescript("DELETE FROM book_shelf_link; DELETE FROM book_read_link; DELETE FROM user WHERE id=2; DELETE FROM shelf WHERE id=6;")
    store.restore_app_rows(ex, captured, 9)
    assert con.execute("SELECT book_id, shelf FROM book_shelf_link").fetchall() == [(9, 5)]
    assert con.execute("SELECT book_id, user_id, read_status FROM book_read_link").fetchall() == [(9, 1, 1)]


def _entry(root, entry_id, days_ago, now):
    folder, manifest = store.entry_paths(str(root), entry_id)
    os.makedirs(folder)
    with open(os.path.join(folder, "book.epub"), "w") as f:
        f.write("x")
    stamp = datetime.fromtimestamp(now, timezone.utc) - timedelta(days=days_ago)
    store.write_manifest(manifest, {"kind": "book", "book_id": 1, "title": entry_id, "trashed_at": stamp.isoformat()})


def test_purge_removes_only_entries_older_than_n_days(tmp_path):
    root = tmp_path / store.TRASH_DIRNAME
    now = time.time()
    _entry(root, "20260101T000000_1", 31, now)
    _entry(root, "20260201T000000_2", 29.5, now)
    assert store.purge(str(root), 0, now=now) == []  # 0 keeps everything
    assert store.purge(str(root), 30, now=now) == ["20260101T000000_1"]
    assert sorted(os.listdir(root)) == ["20260201T000000_2", "20260201T000000_2.json"]
    [left] = store.list_entries(str(root), now=now, retention_days=30)
    assert left["days_left"] == 1 and left["has_files"]


def test_purge_task_uses_the_retention_setting(tmp_path):
    from cps.tasks.trash_purge import TaskPurgeTrash
    from cps.services.worker import STAT_FINISH_SUCCESS
    root = tmp_path / store.TRASH_DIRNAME
    now = time.time()
    _entry(root, "20260101T000000_1", 40, now)
    _entry(root, "20260301T000000_2", 1, now)
    task = TaskPurgeTrash(root=str(root), days=30)
    task.start(None)
    assert task.stat == STAT_FINISH_SUCCESS
    assert [e["id"] for e in store.list_entries(str(root))] == ["20260301T000000_2"]


def test_entry_ids_are_validated():
    for bad in ("../etc", "20260101T000000_1/..", "", "x_1", "20260101T000000_1.json"):
        assert not store.valid_entry_id(bad)
    assert store.valid_entry_id("20260101T000000_12_EPUB")


def test_move_across_filesystems_copies_and_tolerates_nfs_placeholders(tmp_path, monkeypatch):
    src = tmp_path / "lib" / "A" / "B (1)"
    src.mkdir(parents=True)
    (src / "book.epub").write_text("x")
    (src / ".nfs000123").write_text("open elsewhere")
    real_rename, real_rmtree = os.rename, shutil.rmtree

    def no_rename(a, b):
        raise OSError(errno.EXDEV, "cross-device link")

    def stubborn_rmtree(path, onexc=None):
        # NFS refuses to unlink a silly-renamed file that is still open
        for name in os.listdir(path):
            p = os.path.join(path, name)
            if name.startswith(".nfs"):
                onexc(os.unlink, p, OSError(errno.EBUSY, "busy"))
            else:
                os.unlink(p)
        onexc(os.rmdir, path, OSError(errno.ENOTEMPTY, "not empty"))

    monkeypatch.setattr(store.os, "rename", no_rename)
    monkeypatch.setattr(store.shutil, "rmtree", stubborn_rmtree)
    dest = tmp_path / "lib" / ".lily-trash" / "20260101T000000_1"
    store.move_path(str(src), str(dest))
    assert (dest / "book.epub").read_text() == "x"
    assert os.listdir(src) == [".nfs000123"]  # only the placeholder is left behind
    monkeypatch.setattr(store.os, "rename", real_rename)
    monkeypatch.setattr(store.shutil, "rmtree", real_rmtree)


def test_library_mirror_skips_the_trash(tmp_path):
    import library_mirror
    lib = tmp_path / "lib"
    (lib / "A" / "B (1)").mkdir(parents=True)
    (lib / "A" / "B (1)" / "b.epub").write_text("x")
    (lib / store.TRASH_DIRNAME / "20260101T000000_2").mkdir(parents=True)
    (lib / store.TRASH_DIRNAME / "20260101T000000_2" / "c.epub").write_text("y")
    planned = [os.path.relpath(src, lib) for src, _dest, _size in library_mirror.plan_mirror(str(lib), str(tmp_path / "m"))]
    assert planned == [os.path.join("A", "B (1)", "b.epub")]


# ---------------------------------------------------------------------------- app

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path / "cfg"))
    (tmp_path / "cfg").mkdir()
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(e.app)
        from cps import editbooks
        monkeypatch.setattr(editbooks, "_queue_duplicate_scan_after_change", lambda ids=None: None)
        yield e


def _admin(env):
    c = env.app.test_client()
    c.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
    return c


def _trash_root(env):
    return env.library_dir / store.TRASH_DIRNAME


def _book_exists(env, book_id):
    con = sqlite3.connect(env.library_dir / "metadata.db")
    try:
        return con.execute("SELECT COUNT(*) FROM books WHERE id=?", (book_id,)).fetchone()[0] == 1
    finally:
        con.close()


def _entries(env):
    return store.list_entries(str(_trash_root(env)))


def test_delete_moves_book_to_trash_and_restore_brings_it_back(env):
    from cps import ub
    admin = _admin(env)
    book_id = env.add_library_book("Dune", author="Frank Herbert", tags=("SF",), series="Dune",
                                   publisher="Chilton", identifiers={"isbn": "9780441013593"}, comment="Spice.")
    keep_id = env.add_library_book("Emma", author="Jane Austen")
    folder = env.library_dir / "Frank Herbert" / f"Dune ({book_id})"
    files_before = sorted(os.listdir(folder))
    me = env.admin()
    admin.post("/shelf/create", data={"title": "Favs"})
    shelf_id = ub.session.query(ub.Shelf).filter(ub.Shelf.name == "Favs").one().id
    admin.post("/shelf/add_selected_to_shelf", json={"shelf_id": shelf_id, "book_ids": [book_id]})
    ub.session.add(ub.ReadBook(user_id=me.id, book_id=book_id, read_status=ub.ReadBook.STATUS_FINISHED))
    ub.session.add(ub.WebReaderProgress(user_id=me.id, book_id=book_id, cfi="epubcfi(/6/4)", percent=0.42))
    ub.session.commit()

    resp = admin.post(f"/ajax/delete/{book_id}")
    assert resp.status_code == 200 and "moved to the Trash" in resp.get_data(as_text=True)
    assert not folder.exists() and not folder.parent.exists()  # empty author folder removed too
    assert not _book_exists(env, book_id) and _book_exists(env, keep_id)
    ub.session.expire_all()
    assert ub.session.query(ub.BookShelf).filter(ub.BookShelf.book_id == book_id).count() == 0
    assert ub.session.query(ub.WebReaderProgress).filter(ub.WebReaderProgress.book_id == book_id).count() == 0

    [entry] = _entries(env)
    assert entry["kind"] == "book" and entry["book_id"] == book_id and entry["title"] == "Dune"
    assert sorted(os.listdir(_trash_root(env) / entry["id"])) == files_before
    html = admin.get("/admin/trash").get_data(as_text=True)
    assert "Dune" in html and "Frank Herbert" in html

    resp = admin.post(f"/admin/trash/restore/{entry['id']}")
    assert resp.status_code == 302
    assert sorted(os.listdir(folder)) == files_before
    assert _entries(env) == []
    detail = admin.get(f"/book/{book_id}").get_data(as_text=True)
    assert "Dune" in detail and "Chilton" in detail and "SF" in detail
    ub.session.expire_all()
    link = ub.session.query(ub.BookShelf).filter(ub.BookShelf.book_id == book_id).one()
    assert link.shelf == shelf_id
    assert ub.session.query(ub.ReadBook).filter(ub.ReadBook.book_id == book_id).one().read_status == 1
    assert ub.session.query(ub.WebReaderProgress).filter(ub.WebReaderProgress.book_id == book_id).one().percent == 0.42
    con = sqlite3.connect(env.library_dir / "metadata.db")
    try:
        assert con.execute("SELECT val FROM identifiers WHERE book=?", (book_id,)).fetchone()[0] == "9780441013593"
        assert con.execute("SELECT text FROM comments WHERE book=?", (book_id,)).fetchone()[0] == "Spice."
    finally:
        con.close()


def test_bulk_delete_moves_every_selected_book_to_trash(env):
    admin = _admin(env)
    ids = [env.add_library_book(t, author="Bulk Author") for t in ("One", "Two", "Three")]
    resp = admin.post("/ajax/deleteselectedbooks", json={"selections": ids[:2]})
    assert json.loads(resp.data) == {"success": True}
    assert [_book_exists(env, i) for i in ids] == [False, False, True]
    entries = _entries(env)
    assert sorted(e["book_id"] for e in entries) == sorted(ids[:2])
    assert all(e["reason"] == "bulk delete" and e["has_files"] for e in entries)
    assert sorted(os.listdir(env.library_dir / "Bulk Author")) == [f"Three ({ids[2]})"]


def test_merge_moves_the_merged_book_to_trash(env):
    admin = _admin(env)
    target = env.add_library_book("Persuasion", author="Jane Austen")
    other = env.add_library_book("Persuasion Copy", author="Jane Austen", files=("metamorphosis.txt",))
    resp = admin.post("/ajax/mergebooks", json={"Merge_books": [target, other]})
    assert json.loads(resp.data) == {"success": True}
    assert not _book_exists(env, other)
    [entry] = _entries(env)
    assert entry["book_id"] == other and entry["reason"] == "merged into %d" % target
    target_files = os.listdir(env.library_dir / "Jane Austen" / f"Persuasion ({target})")
    assert any(f.endswith(".txt") for f in target_files)  # the TXT was merged in before the delete


def test_format_delete_goes_to_trash_and_can_be_restored(env):
    admin = _admin(env)
    book_id = env.add_library_book("Ulysses", author="James Joyce",
                                   files=("test_minimal_valid.epub", "metamorphosis.txt"))
    folder = env.library_dir / "James Joyce" / f"Ulysses ({book_id})"
    admin.post(f"/delete/{book_id}/TXT")
    assert not any(f.endswith(".txt") for f in os.listdir(folder))
    [entry] = _entries(env)
    assert entry["kind"] == "format" and entry["formats"] == ["TXT"]
    admin.post(f"/admin/trash/restore/{entry['id']}")
    assert any(f.endswith(".txt") for f in os.listdir(folder))
    con = sqlite3.connect(env.library_dir / "metadata.db")
    try:
        assert sorted(r[0] for r in con.execute("SELECT format FROM data WHERE book=?", (book_id,))) == ["EPUB", "TXT"]
    finally:
        con.close()


def test_delete_now_and_settings(env):
    admin = _admin(env)
    book_id = env.add_library_book("Gone", author="Someone")
    admin.post(f"/ajax/delete/{book_id}")
    [entry] = _entries(env)
    admin.post(f"/admin/trash/delete/{entry['id']}")
    assert _entries(env) == [] and os.listdir(_trash_root(env)) == []
    assert admin.post("/admin/trash/delete/..%2Fmetadata.db").status_code == 404

    admin.post("/admin/trash/settings", data={"trash_retention_days": "7"})
    from cps import trash
    assert trash.get_retention_days() == 7
    admin.post("/admin/trash/settings", data={"trash_retention_days": "-3"})
    assert trash.get_retention_days() == 7


def test_trash_routes_are_admin_only(env):
    env.add_user("plain", password="pw")
    user = env.app.test_client()
    user.post("/login", data={"username": "plain", "password": "pw"})
    for path in ("/admin/trash",):
        assert user.get(path).status_code in (302, 403)
    for path in ("/admin/trash/empty", "/admin/trash/settings", "/admin/trash/restore/20260101T000000_1",
                 "/admin/trash/delete/20260101T000000_1", "/admin/trash/unreferenced"):
        assert user.post(path).status_code in (302, 403)


def test_failed_row_delete_puts_the_folder_back(env, monkeypatch):
    from cps import editbooks
    admin = _admin(env)
    book_id = env.add_library_book("Fragile", author="Some One")
    folder = env.library_dir / "Some One" / f"Fragile ({book_id})"

    from cps import ub
    ub.session.add(ub.ReadBook(user_id=env.admin().id, book_id=book_id, read_status=ub.ReadBook.STATUS_FINISHED))
    ub.session.commit()

    def boom(book_id, book):
        # Like delete_whole_book: app.db rows go (and are committed) before metadata.db fails
        ub.session.query(ub.ReadBook).filter(ub.ReadBook.book_id == book_id).delete()
        ub.session.commit()
        raise RuntimeError("database is locked")

    monkeypatch.setattr(editbooks, "delete_whole_book", boom)
    admin.post(f"/ajax/delete/{book_id}")
    assert folder.is_dir() and _book_exists(env, book_id)
    assert _entries(env) == []
    ub.session.expire_all()
    assert ub.session.query(ub.ReadBook).filter(ub.ReadBook.book_id == book_id).one().read_status == 1


def test_unreferenced_folders_can_be_sent_back_through_ingest(env, tmp_path, monkeypatch):
    from cps import library_orphans
    from cps.cwa_functions import ingest
    ingest_dir = tmp_path / "ingest"
    ingest_dir.mkdir()
    monkeypatch.setattr(ingest, "get_ingest_dir", lambda: str(ingest_dir))
    folder = env.library_dir / "Lost Author" / "Lost Book (99)"
    folder.mkdir(parents=True)
    (folder / "Lost Book - Lost Author.epub").write_text("epub")
    (folder / "cover.jpg").write_text("jpg")
    (folder / "metadata.opf").write_text("<package/>")
    library_orphans.save_report(str(tmp_path / "cfg"), ["Lost Author/Lost Book (99)", "../../etc"], "snapshot x")
    admin = _admin(env)
    html = admin.get("/admin/trash").get_data(as_text=True)
    assert "Lost Author/Lost Book (99)" in html and "snapshot x" in html

    admin.post("/admin/trash/unreferenced", data={"action": "reimport", "folders": ["Lost Author/Lost Book (99)"]})
    assert os.listdir(ingest_dir) == ["Lost Book - Lost Author.epub"]
    assert not folder.exists()
    [entry] = _entries(env)
    assert entry["kind"] == "orphan"
    assert library_orphans.load_report(str(tmp_path / "cfg"))["folders"] == ["../../etc"]

    admin.post(f"/admin/trash/restore/{entry['id']}")
    assert sorted(os.listdir(folder)) == ["Lost Book - Lost Author.epub", "cover.jpg", "metadata.opf"]

    admin.post("/admin/trash/unreferenced", data={"action": "reimport", "folders": ["../../etc"]})
    assert os.listdir(ingest_dir) == ["Lost Book - Lost Author.epub"]  # path outside the library refused
    admin.post("/admin/trash/unreferenced", data={"action": "dismiss"})
    assert library_orphans.load_report(str(tmp_path / "cfg")) == {}


def test_restore_survives_calibre9_rows_left_behind_by_the_delete(meta_db):
    # Calibre 9 adds books_pages_link, filled by an insert trigger on books. Its row is only
    # removed by ON DELETE CASCADE, which needs foreign_keys on, so a delete leaves it behind.
    meta_db.executescript("""
        CREATE TABLE books_pages_link (book INTEGER PRIMARY KEY, pages INTEGER DEFAULT 0 NOT NULL,
            FOREIGN KEY (book) REFERENCES books(id) ON DELETE CASCADE);
        CREATE TRIGGER books_pages_link_create_trigger AFTER INSERT ON books FOR EACH ROW
            BEGIN INSERT INTO books_pages_link(book) VALUES(NEW.id); END;
        INSERT INTO books_pages_link (book, pages) VALUES (1, 412);
    """)
    ex = store.SqliteExecutor(meta_db)
    snapshot = json.loads(json.dumps(store.capture_book_rows(ex, 1)))
    _delete_book(meta_db, 1)
    assert meta_db.execute("SELECT COUNT(*) FROM books_pages_link WHERE book=1").fetchone()[0] == 1

    new_id, _path = store.restore_book_rows(ex, snapshot)
    meta_db.commit()
    assert new_id == 1
    assert _summary(meta_db, 1)["tags"] == ["SF"]
    # The trigger's fresh row (Calibre recounts pages itself)
    assert meta_db.execute("SELECT COUNT(*) FROM books_pages_link WHERE book=1").fetchone()[0] == 1
