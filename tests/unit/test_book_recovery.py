import json
import os
import sqlite3
import uuid

import pytest

from .lily_env import lily_env, ADMIN_PASSWORD


@pytest.fixture
def env(tmp_path, temp_cwa_db, monkeypatch):
    monkeypatch.setenv("BOOK_RECOVERY_DIR", str(tmp_path / "recovery"))
    from cps.tasks import restore
    monkeypatch.setattr(restore, "SERVICE_LOCKS", tuple(
        (name, str(tmp_path / filename), existence_lock)
        for name, filename, existence_lock in restore.SERVICE_LOCKS
    ))
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        yield e


def _login(env, name=None, password=ADMIN_PASSWORD):
    client = env.app.test_client()
    client.post("/login", data={"username": name or env.admin().name, "password": password})
    return client


def _meta(env):
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.create_function("title_sort", 1, lambda t: t)
    con.create_function("uuid4", 0, lambda: str(uuid.uuid4()))
    return con


def _book_dir(env, book_id):
    from cps import calibre_db
    book = calibre_db.get_book(book_id)
    return env.library_dir / book.path


def _write_files(env, book_id, files):
    folder = _book_dir(env, book_id)
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (folder / name).write_bytes(content)


def _data_row(env, book_id, fmt):
    return _meta(env).execute(
        "SELECT id, name FROM data WHERE book=? AND format=?", (book_id, fmt)).fetchone()


@pytest.mark.unit
class TestDeleteRecovery:
    def test_nested_service_pause_retains_lock_and_releases_after_exception(self, env):
        from cps.book_recovery import paused_services
        from cps.tasks import restore

        lock_path = restore.SERVICE_LOCKS[0][1]
        with pytest.raises(ValueError, match="nested failure"):
            with paused_services():
                with paused_services():
                    with pytest.raises(RuntimeError, match="lock held"):
                        restore._acquire_service_lock(lock_path)
                    raise ValueError("nested failure")
        handle = restore._acquire_service_lock(lock_path)
        restore.release_service_locks([handle])
        with paused_services():
            with paused_services():
                pass

    def test_service_pause_is_not_shared_between_threads(self, env):
        import threading
        from cps.book_recovery import paused_services

        outcomes = []

        def attempt_pause():
            try:
                with paused_services():
                    outcomes.append("entered")
            except RuntimeError as ex:
                outcomes.append(str(ex))

        with paused_services():
            thread = threading.Thread(target=attempt_pause)
            thread.start()
            thread.join(timeout=2)
            assert not thread.is_alive()
        assert len(outcomes) == 1
        assert "ingest processor is currently running" in outcomes[0]

    def test_capture_refuses_active_ingest_lock(self, env):
        from cps import calibre_db
        from cps.book_recovery import capture_book, list_recovery
        from cps.tasks import restore

        bid = env.add_book("Locked Book", fmt="EPUB")
        _write_files(env, bid, {"Locked Book.epub": b"precious"})
        handle = restore._acquire_service_lock(restore.SERVICE_LOCKS[0][1])
        try:
            with pytest.raises(RuntimeError, match="ingest processor is currently running"):
                capture_book(calibre_db.get_book(bid))
        finally:
            restore.release_service_locks([handle])
        assert (_book_dir(env, bid) / "Locked Book.epub").read_bytes() == b"precious"
        assert list_recovery() == []

    def test_delete_restore_roundtrip(self, env):
        from cps import calibre_db, db, ub
        from cps.editbooks import delete_book_automatic
        from cps.book_recovery import restore_book, list_recovery

        admin = env.admin()
        bid = env.add_book("Round Trip", author="Author One", fmt="EPUB", tags=("KeepMe",))
        _write_files(env, bid, {"Round Trip.epub": b"epub-bytes", "cover.jpg": b"cover-bytes"})

        shelf = ub.Shelf(name="s1", user_id=admin.id)
        ub.session.add(shelf)
        ub.session.commit()
        link = ub.BookShelf(book_id=bid)
        link.ub_shelf = shelf
        ub.session.add(link)
        ub.session.add(ub.ReadBook(book_id=bid, user_id=admin.id, read_status=1))
        ub.session.add(ub.Bookmark(book_id=bid, user_id=admin.id, format="EPUB",
                                   bookmark_key="k1"))
        ub.session.add(ub.WebReaderProgress(book_id=bid, user_id=admin.id,
                                            cfi="epubcfi(/1)", percent=0.5))
        library_uuid = calibre_db.session.query(db.Library_Id).first().uuid
        ub.session.add(ub.ReaderPosition(user_id=admin.id, library_uuid=library_uuid,
                                         book_id=bid, format="EPUB", percent=0.5))
        ub.session.add(ub.ReaderPosition(user_id=admin.id, library_uuid="other-library",
                                         book_id=bid, format="EPUB", percent=0.2))
        ub.session_commit()

        _warning, rid = delete_book_automatic(calibre_db.get_book(bid))
        entries = list_recovery()
        assert [e["recovery_id"] for e in entries] == [rid]
        assert calibre_db.get_book(bid) is None
        assert ub.session.query(ub.ReaderPosition).filter_by(
            book_id=bid, library_uuid=library_uuid).count() == 0
        assert ub.session.query(ub.ReaderPosition).filter_by(
            book_id=bid, library_uuid="other-library").count() == 1
        assert not (env.library_dir / "Author One" / "Round Trip").exists()

        restored, _skipped = restore_book(rid)
        assert restored == bid
        book = calibre_db.get_book(bid)
        assert book is not None
        assert [a.name for a in book.authors] == ["Author One"]
        assert [t.name for t in book.tags] == ["KeepMe"]
        folder = env.library_dir / "Author One" / "Round Trip"
        assert (folder / "Round Trip.epub").read_bytes() == b"epub-bytes"
        assert (folder / "cover.jpg").read_bytes() == b"cover-bytes"
        assert ub.session.query(ub.ReadBook).filter_by(book_id=bid).count() == 1
        assert ub.session.query(ub.BookShelf).filter_by(book_id=bid).count() == 1
        assert ub.session.query(ub.Bookmark).filter_by(book_id=bid).count() == 1
        assert ub.session.query(ub.WebReaderProgress).filter_by(book_id=bid).count() == 1
        assert ub.session.query(ub.ReaderPosition).filter_by(
            book_id=bid, library_uuid=library_uuid).count() == 1

    def test_delete_drops_orphaned_refs_and_restore_recreates_them(self, env):
        from cps import calibre_db, db
        from cps.editbooks import delete_book_automatic
        from cps.book_recovery import restore_book

        env.admin()
        env.add_book("Keeper", author="Shared Author", fmt="EPUB", tags=("SharedTag",))
        bid = env.add_book("Solo", author="Shared Author", fmt="EPUB",
                           tags=("SharedTag", "LonelyTag"))
        _write_files(env, bid, {"Solo.epub": b"solo"})
        book = calibre_db.get_book(bid)
        book.authors.append(db.Authors("Lonely Author", "Author, Lonely"))
        calibre_db.session.commit()

        def names():
            calibre_db.session.expire_all()
            return ({a.name for a in calibre_db.session.query(db.Authors)},
                    {t.name for t in calibre_db.session.query(db.Tags)})

        _warning, rid = delete_book_automatic(calibre_db.get_book(bid))
        assert names() == ({"Shared Author"}, {"SharedTag"})

        restore_book(rid)
        assert names() == ({"Shared Author", "Lonely Author"}, {"SharedTag", "LonelyTag"})
        restored = calibre_db.get_book(bid)
        assert {a.name for a in restored.authors} == {"Shared Author", "Lonely Author"}
        assert {t.name for t in restored.tags} == {"SharedTag", "LonelyTag"}

    def test_delete_commit_failure_restores_files_and_rows(self, env, monkeypatch):
        from cps import calibre_db, ub
        import cps.book_recovery as br
        from cps.book_recovery import RecoveryError, list_recovery
        from cps.editbooks import delete_book_automatic

        admin = env.admin()
        bid = env.add_book("Keep Me", author="Author One", fmt="EPUB", tags=("TagOne",))
        _write_files(env, bid, {"Keep Me.epub": b"epub-bytes", "cover.jpg": b"cover-bytes"})
        shelf = ub.Shelf(name="s1", user_id=admin.id)
        ub.session.add(shelf)
        ub.session.commit()
        link = ub.BookShelf(book_id=bid)
        link.ub_shelf = shelf
        ub.session.add(link)
        ub.session.add(ub.ReadBook(book_id=bid, user_id=admin.id, read_status=1))
        ub.session_commit()

        real_connect = br._connect_meta

        class _FailCommit:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, *args):
                if str(sql).strip().upper() == "COMMIT":
                    raise sqlite3.OperationalError("forced commit failure")
                return self._real.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._real, name)

        monkeypatch.setattr(br, "_connect_meta",
                            lambda path: _FailCommit(real_connect(path)))
        with pytest.raises(RecoveryError):
            delete_book_automatic(calibre_db.get_book(bid))

        folder = _book_dir(env, bid)
        assert (folder / "Keep Me.epub").read_bytes() == b"epub-bytes"
        assert (folder / "cover.jpg").read_bytes() == b"cover-bytes"
        con = _meta(env)
        assert con.execute("SELECT 1 FROM books WHERE id=?", (bid,)).fetchone()
        assert con.execute("SELECT 1 FROM data WHERE book=?", (bid,)).fetchone()
        con.close()
        ub.session.rollback()
        assert ub.session.query(ub.ReadBook).filter_by(book_id=bid).count() == 1
        assert ub.session.query(ub.BookShelf).filter_by(book_id=bid).count() == 1
        assert len(list_recovery()) == 1
        assert not [p for p in env.library_dir.iterdir()
                    if p.name.startswith(".lily_delete_")]

    def test_format_delete_commit_failure_restores_file(self, env, monkeypatch):
        from cps import calibre_db
        import cps.book_recovery as br
        from cps.book_recovery import RecoveryError, capture_book, restore_book

        bid = env.add_book("Fmt Keep", author="Author One", fmt="EPUB")
        _write_files(env, bid, {"Fmt Keep.epub": b"epub-bytes", "cover.jpg": b"c"})
        rid = capture_book(calibre_db.get_book(bid), "EPUB")

        real_connect = br._connect_meta

        class _FailCommit:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, *args):
                if str(sql).strip().upper() == "COMMIT":
                    raise sqlite3.OperationalError("forced commit failure")
                return self._real.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._real, name)

        monkeypatch.setattr(br, "_connect_meta",
                            lambda path: _FailCommit(real_connect(path)))
        with pytest.raises(RecoveryError):
            br.delete_captured_book(calibre_db.get_book(bid), "epub",
                                    recovery_id=rid)

        folder = _book_dir(env, bid)
        assert (folder / "Fmt Keep.epub").read_bytes() == b"epub-bytes"
        assert (folder / "cover.jpg").read_bytes() == b"c"
        con = _meta(env)
        assert con.execute(
            "SELECT 1 FROM data WHERE book=? AND format='EPUB'", (bid,)).fetchone()
        con.close()
        with pytest.raises(RecoveryError):
            restore_book(rid)

    def test_format_delete_restore_keeps_new_metadata(self, env):
        from cps import calibre_db
        from cps.book_recovery import capture_book, restore_book

        bid = env.add_book("Format Book", author="Author Two", fmt="EPUB", tags=("t",))
        _write_files(env, bid, {"Format Book.epub": b"epub", "cover.jpg": b"c"})
        rid = capture_book(calibre_db.get_book(bid), "EPUB")
        (_book_dir(env, bid) / "Format Book.epub").unlink()
        con = _meta(env)
        con.execute("DELETE FROM data WHERE book=? AND format='EPUB'", (bid,))
        con.commit()
        con.execute("UPDATE books SET title=? WHERE id=?", ("Renamed Title", bid))
        con.commit(); con.close()
        calibre_db.session.expire_all()

        assert restore_book(rid)[0] == bid
        book = calibre_db.get_book(bid)
        assert book.title == "Renamed Title"
        assert _data_row(env, bid, "EPUB")
        assert (_book_dir(env, bid) / "Format Book.epub").read_bytes() == b"epub"

    def test_capture_failure_blocks_delete(self, env, monkeypatch):
        from cps import calibre_db
        from cps import book_recovery

        bid = env.add_book("Fragile", author="Author Three", fmt="EPUB")
        _write_files(env, bid, {"Fragile.epub": b"x"})
        monkeypatch.setattr(book_recovery, "capture_book",
                            lambda *a, **k: (_ for _ in ()).throw(book_recovery.RecoveryError("disk full")))
        from cps.editbooks import delete_book_automatic
        with pytest.raises(Exception, match="disk full"):
            delete_book_automatic(calibre_db.get_book(bid))
        assert calibre_db.get_book(bid) is not None
        assert (_book_dir(env, bid) / "Fragile.epub").exists()

    def test_malformed_traversal_and_missing_entries_refused(self, env):
        from cps.book_recovery import restore_book
        for bad in ("not-a-uuid", "../..", "x" * 40, "0" * 31 + "z"):
            with pytest.raises(Exception):
                restore_book(bad)

    def test_checksum_mismatch_refused(self, env):
        from cps import calibre_db
        from cps.book_recovery import capture_book, restore_book

        bid = env.add_book("Tampered", author="Author Four", fmt="EPUB")
        _write_files(env, bid, {"Tampered.epub": b"orig"})
        rid = capture_book(calibre_db.get_book(bid))
        target = env.library_dir.parent / "recovery" / rid / "files" / "Tampered.epub"
        target.write_bytes(b"tampered")
        with pytest.raises(Exception, match="checksum"):
            restore_book(rid)

    def test_library_uuid_mismatch_refused(self, env):
        import json as jsonlib
        from cps import calibre_db
        from cps.book_recovery import capture_book, restore_book

        bid = env.add_book("Wrong Lib", author="Author Five", fmt="EPUB")
        _write_files(env, bid, {"Wrong Lib.epub": b"e"})
        rid = capture_book(calibre_db.get_book(bid))
        manifest_path = env.library_dir.parent / "recovery" / rid / "manifest.json"
        manifest = jsonlib.loads(manifest_path.read_text())
        manifest["library_uuid"] = "different-library-uuid"
        manifest_path.write_text(jsonlib.dumps(manifest))
        with pytest.raises(Exception, match="different library"):
            restore_book(rid)

    def test_id_and_path_collisions_refused(self, env):
        from cps import calibre_db
        from cps.book_recovery import capture_book, restore_book

        bid = env.add_book("Still Here", author="Author Six", fmt="EPUB")
        _write_files(env, bid, {"Still Here.epub": b"e"})
        rid = capture_book(calibre_db.get_book(bid))
        with pytest.raises(Exception, match="already occupies"):
            restore_book(rid)

        from cps.editbooks import delete_book_automatic
        delete_book_automatic(calibre_db.get_book(bid))
        squatter = env.library_dir / "Author Six" / "Still Here"
        squatter.mkdir(parents=True)
        (squatter / "stray.epub").write_bytes(b"not ours")
        with pytest.raises(Exception, match="not empty"):
            restore_book(rid)

    def test_reused_reference_id_not_overwritten(self, env):
        from cps import calibre_db
        from cps.editbooks import delete_book_automatic
        from cps.book_recovery import capture_book, restore_book

        bid = env.add_book("Ref Book", author="Vanishing Author", fmt="EPUB")
        _write_files(env, bid, {"Ref Book.epub": b"e"})
        rid = capture_book(calibre_db.get_book(bid))
        con = _meta(env)
        old_author_id = con.execute(
            "SELECT author FROM books_authors_link WHERE book=?", (bid,)).fetchone()[0]
        con.close()
        delete_book_automatic(calibre_db.get_book(bid))
        con = _meta(env)
        con.execute("DELETE FROM authors WHERE id=?", (old_author_id,))
        con.execute("INSERT INTO authors (id, name, sort) VALUES (?, 'Squatter', 'Squatter')",
                    (old_author_id,))
        con.commit(); con.close()
        calibre_db.session.expire_all()

        restore_book(rid)
        con = _meta(env)
        names = dict(con.execute("SELECT id, name FROM authors").fetchall())
        con.close()
        assert names[old_author_id] == "Squatter"
        book = calibre_db.get_book(bid)
        assert [a.name for a in book.authors] == ["Vanishing Author"]

    def test_custom_column_roundtrip_and_mismatch(self, env):
        from cps import calibre_db
        from cps.editbooks import delete_book_automatic
        from cps.book_recovery import restore_book

        con = _meta(env)
        con.execute("INSERT INTO custom_columns (label, name, datatype, mark_for_delete, "
                    "editable, display, is_multiple, normalized) VALUES "
                    "('mood','mood','text',0,1,'{}',0,1)")
        cc_id = con.execute("SELECT max(id) FROM custom_columns").fetchone()[0]
        con.execute("CREATE TABLE custom_column_%d (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "value TEXT)" % cc_id)
        con.execute("CREATE TABLE books_custom_column_%d_link "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER, value INTEGER)" % cc_id)
        con.commit(); con.close()

        from cps import db as cdb
        row = type("CC", (), {"id": cc_id, "datatype": "text"})
        cdb.CalibreDB.setup_db_cc_classes([row])

        bid = env.add_book("CC Book", author="Author Seven", fmt="EPUB")
        con = _meta(env)
        con.execute("INSERT INTO custom_column_%d (value) VALUES ('gloomy')" % cc_id)
        val_id = con.execute("SELECT max(id) FROM custom_column_%d" % cc_id).fetchone()[0]
        con.execute("INSERT INTO books_custom_column_%d_link (book, value) VALUES (?,?)"
                    % cc_id, (bid, val_id))
        con.commit(); con.close()
        calibre_db.session.expire_all()
        _write_files(env, bid, {"CC Book.epub": b"e"})

        from cps.book_recovery import capture_book
        rid = capture_book(calibre_db.get_book(bid))
        delete_book_automatic(calibre_db.get_book(bid))
        calibre_db.session.expire_all()
        restore_book(rid)
        calibre_db.session.expire_all()
        con = _meta(env)
        restored_val = con.execute(
            "SELECT v.value FROM custom_column_%d v JOIN books_custom_column_%d_link l "
            "ON l.value=v.id WHERE l.book=?" % (cc_id, cc_id), (bid,)).fetchone()
        assert restored_val[0] == "gloomy"
        con.close()

        bid2 = env.add_book("CC Two", author="Author Eight", fmt="EPUB")
        _write_files(env, bid2, {"CC Two.epub": b"e"})
        con = _meta(env)
        con.execute("INSERT INTO custom_column_%d (value) VALUES ('x')" % cc_id)
        val_id = con.execute("SELECT max(id) FROM custom_column_%d" % cc_id).fetchone()[0]
        con.execute("INSERT INTO books_custom_column_%d_link (book, value) VALUES (?,?)"
                    % cc_id, (bid2, val_id))
        con.commit()
        rid2 = capture_book(calibre_db.get_book(bid2))
        delete_book_automatic(calibre_db.get_book(bid2))
        con.execute("DROP TABLE custom_column_%d" % cc_id)
        con.execute("DROP TABLE books_custom_column_%d_link" % cc_id)
        con.commit(); con.close()
        calibre_db.session.expire_all()
        with pytest.raises(Exception):
            restore_book(rid2)

    def test_retention_defaults_and_pruning(self, env, tmp_path):
        from cps import calibre_db
        from cps.book_recovery import capture_book, prune_book_recovery, get_recovery_retention_days

        bid = env.add_book("Old", author="Author Nine", fmt="EPUB")
        _write_files(env, bid, {"Old.epub": b"e"})
        rid = capture_book(calibre_db.get_book(bid))
        root = tmp_path / "recovery"

        assert get_recovery_retention_days() == 0
        assert prune_book_recovery(root=str(root), days=0) == []

        unrelated = root / "not-an-entry"
        unrelated.mkdir(exist_ok=True)
        link = root / "linked"
        link.symlink_to(root / rid, target_is_directory=True)
        removed = prune_book_recovery(root=str(root), days=1)
        assert removed == []

        manifest = json.loads((root / rid / "manifest.json").read_text())
        manifest["created_utc"] = "2000-01-01T00:00:00+00:00"
        (root / rid / "manifest.json").write_text(json.dumps(manifest))
        removed = prune_book_recovery(root=str(root), days=1)
        assert str(root / rid) in removed
        assert unrelated.exists()
        assert os.path.lexists(link)


@pytest.mark.unit
class TestMergeSafety:
    def _pair(self, env, shared=b"same-epub", extra=True):
        target = env.add_book("Target Book", author="Merge Author", fmt="EPUB")
        source = env.add_book("Source Book", author="Merge Author", fmt="EPUB")
        _write_files(env, target, {"Target Book.epub": shared, "cover.jpg": b"c"})
        files = {"Source Book.epub": shared, "cover.jpg": b"c"}
        if extra:
            con = _meta(env)
            con.execute("INSERT INTO data (book, format, uncompressed_size, name) VALUES (?,?,?,?)",
                        (source, "PDF", 5, "Source Book"))
            con.commit(); con.close()
            files["Source Book.pdf"] = b"pdf-bytes"
        return target, source, files

    def test_identical_overlap_plus_new_format_merges(self, env):
        from cps import calibre_db
        target, source, files = self._pair(env)
        _write_files(env, source, files)
        client = _login(env)
        resp = client.post("/ajax/mergebooks",
                           json={"Merge_books": [target, source]})
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["success"] is True
        src = payload["results"][0]
        assert src["status"] == "succeeded" and src["recovery_id"]
        book = calibre_db.get_book(target)
        assert {d.format for d in book.data} == {"EPUB", "PDF"}
        assert (_book_dir(env, target) / "Target Book - Merge Author.pdf").read_bytes() == b"pdf-bytes"
        assert calibre_db.get_book(source) is None

    def test_conflicting_epub_rejects_all(self, env):
        from cps import calibre_db
        target, source, files = self._pair(env)
        _write_files(env, source, files)
        (_book_dir(env, target) / "Target Book.epub").write_bytes(b"different")
        client = _login(env)
        resp = client.post("/ajax/mergebooks", json={"Merge_books": [target, source]})
        payload = resp.get_json()
        assert resp.status_code == 200 and payload["success"] is False
        assert payload["results"][0]["status"] == "failed"
        assert calibre_db.get_book(source) is not None
        assert (_book_dir(env, source) / "Source Book.epub").read_bytes() == b"same-epub"

    def test_missing_source_file_fails_preflight(self, env):
        from cps import calibre_db
        target = env.add_book("T", author="Merge Author", fmt="EPUB")
        source = env.add_book("S", author="Merge Author", fmt="EPUB")
        _write_files(env, target, {"T.epub": b"t"})
        _book_dir(env, source).mkdir(parents=True, exist_ok=True)
        client = _login(env)
        payload = client.post("/ajax/mergebooks", json={"Merge_books": [target, source]}).get_json()
        assert payload["success"] is False
        assert calibre_db.get_book(source) is not None

    def test_editor_without_delete_cannot_merge(self, env):
        from cps import constants
        env.add_user("editor", password="pw",
                     role=constants.ROLE_USER | constants.ROLE_EDIT)
        target, source, files = self._pair(env)
        _write_files(env, source, files)
        client = _login(env, "editor", "pw")
        resp = client.post("/ajax/mergebooks", json={"Merge_books": [target, source]})
        assert resp.status_code == 403

    def test_auto_resolution_fails_closed_on_capture_error(self, env, monkeypatch):
        from cps import calibre_db, book_recovery
        from cps.editbooks import delete_book_automatic

        bid = env.add_book("Auto Dup", author="Author Ten", fmt="EPUB")
        _write_files(env, bid, {"Auto Dup.epub": b"e"})
        monkeypatch.setattr(book_recovery, "capture_book",
                            lambda *a, **k: (_ for _ in ()).throw(book_recovery.RecoveryError("no space")))
        with pytest.raises(Exception):
            delete_book_automatic(calibre_db.get_book(bid))
        assert calibre_db.get_book(bid) is not None
        assert (_book_dir(env, bid) / "Auto Dup.epub").exists()

    def test_conflicting_source_only_format_rejects_all(self, env):
        from cps import calibre_db
        target = env.add_book("T", author="Merge Author", fmt="EPUB")
        s1 = env.add_book("S1", author="Merge Author", fmt="EPUB")
        s2 = env.add_book("S2", author="Merge Author", fmt="EPUB")
        _write_files(env, target, {"T.epub": b"same"})
        _write_files(env, s1, {"S1.epub": b"same", "S1.pdf": b"pdf-A"})
        _write_files(env, s2, {"S2.epub": b"same", "S2.pdf": b"pdf-B"})
        con = _meta(env)
        for sid, name in ((s1, "S1"), (s2, "S2")):
            con.execute("INSERT INTO data (book, format, uncompressed_size, name) VALUES (?,?,?,?)",
                        (sid, "PDF", 5, name))
        con.commit(); con.close()
        client = _login(env)
        payload = client.post("/ajax/mergebooks",
                              json={"Merge_books": [target, s1, s2]}).get_json()
        assert payload["success"] is False
        assert "differs" in payload["results"][0]["message"] or \
               "differs" in str(payload["results"])
        assert calibre_db.get_book(s1) is not None
        assert calibre_db.get_book(s2) is not None
        assert {d.format for d in calibre_db.get_book(target).data} == {"EPUB"}
        assert (_book_dir(env, s2) / "S2.pdf").read_bytes() == b"pdf-B"
        assert not (_book_dir(env, target) / "T - Merge Author.pdf").exists()

    def test_hidden_book_invisible_to_restricted_editor(self, env):
        from cps import constants, calibre_db
        env.add_user("restricted", password="pw",
                     role=constants.ROLE_USER | constants.ROLE_EDIT
                     | constants.ROLE_DELETE_BOOKS,
                     denied_tags="hidden")
        target = env.add_book("Open Book", author="Vis Author", fmt="EPUB")
        hidden = env.add_book("Hidden Book", author="Vis Author", fmt="EPUB",
                              tags=("hidden",))
        _write_files(env, target, {"Open Book.epub": b"o"})
        _write_files(env, hidden, {"Hidden Book.epub": b"h"})
        client = _login(env, "restricted", "pw")
        payload = client.post("/ajax/deleteselectedbooks",
                              json={"selections": [hidden]}).get_json()
        assert payload["results"][0]["status"] == "failed"
        payload = client.post("/ajax/editselectedbooks",
                              json={"selections": [hidden], "title": "X"}).get_json()
        assert payload["results"][0]["status"] == "failed"
        payload = client.post("/ajax/mergebooks",
                              json={"Merge_books": [target, hidden]}).get_json()
        by_id = {r["book_id"]: r for r in payload["results"]}
        assert by_id[hidden]["status"] == "failed"
        assert calibre_db.get_book(hidden) is not None
        assert (_book_dir(env, hidden) / "Hidden Book.epub").exists()

    def test_delete_only_user_cannot_merge(self, env):
        from cps import constants
        env.add_user("deleteonly", password="pw",
                     role=constants.ROLE_USER | constants.ROLE_DELETE_BOOKS)
        target, source, files = self._pair(env)
        _write_files(env, source, files)
        client = _login(env, "deleteonly", "pw")
        resp = client.post("/ajax/mergebooks", json={"Merge_books": [target, source]})
        assert resp.status_code == 403


@pytest.mark.unit
class TestBatchTruthfulness:
    def test_mixed_delete_outcomes(self, env):
        bid = env.add_book("Gone", author="Batch Author", fmt="EPUB")
        _write_files(env, bid, {"Gone.epub": b"e"})
        client = _login(env)
        payload = client.post("/ajax/deleteselectedbooks",
                              json={"selections": [bid, 9999]}).get_json()
        assert payload["success"] is False
        by_id = {r["book_id"]: r for r in payload["results"]}
        assert by_id[bid]["status"] == "succeeded" and by_id[bid]["recovery_id"]
        assert by_id[9999]["status"] == "failed"
        assert payload["summary"]["succeeded"] == 1
        assert payload["summary"]["failed"] == 1

    def test_malformed_and_permission_rejections(self, env):
        from cps import constants
        client = _login(env)
        assert client.post("/ajax/deleteselectedbooks",
                           data="not json", content_type="text/plain").status_code == 400
        assert client.post("/ajax/deleteselectedbooks",
                           json={"selections": "nope"}).status_code == 400
        assert client.post("/ajax/deleteselectedbooks",
                           json={"selections": [True, -1, "x"]}).status_code == 400
        assert client.post("/ajax/deleteselectedbooks",
                           json={"selections": []}).status_code == 400
        assert client.post("/ajax/deleteselectedbooks",
                           json={"selections": list(range(1, 2000))}).status_code == 400

        env.add_user("nodelete", password="pw",
                     role=constants.ROLE_USER | constants.ROLE_EDIT)
        bid = env.add_book("Nope", author="Batch Author", fmt="EPUB")
        _write_files(env, bid, {"Nope.epub": b"e"})
        other = _login(env, "nodelete", "pw")
        assert other.post("/ajax/deleteselectedbooks",
                          json={"selections": [bid]}).status_code == 403
        from cps import calibre_db
        assert calibre_db.get_book(bid) is not None

    def test_batch_edit_reports_missing_book(self, env):
        bid = env.add_book("EditMe", author="Batch Author", fmt="EPUB")
        _write_files(env, bid, {"EditMe.epub": b"e"})
        client = _login(env)
        payload = client.post("/ajax/editselectedbooks",
                              json={"selections": [bid, 4242], "title": "New Title"}).get_json()
        by_id = {r["book_id"]: r for r in payload["results"]}
        assert by_id[4242]["status"] == "failed"
        assert by_id[bid]["status"] == "succeeded"
        assert payload["success"] is False

    def test_batch_edit_rename_failure_is_reported(self, env, monkeypatch):
        from cps import helper
        bid = env.add_book("NoRename", author="Batch Author", fmt="EPUB")
        _write_files(env, bid, {"NoRename.epub": b"e"})
        monkeypatch.setattr(helper, "update_dir_structure", lambda *a, **k: "disk full")
        client = _login(env)
        payload = client.post("/ajax/editselectedbooks",
                              json={"selections": [bid], "title": "Moved"}).get_json()
        assert payload["success"] is False
        assert payload["results"][0]["status"] == "failed"
        assert "disk full" in payload["results"][0]["message"]

    def test_read_status_per_book_results(self, env):
        bid = env.add_book("ReadMe", author="Batch Author", fmt="EPUB")
        _write_files(env, bid, {"ReadMe.epub": b"e"})
        client = _login(env)
        payload = client.post("/ajax/readselectedbooks",
                              json={"selections": [bid, 31337], "markAsRead": True}).get_json()
        by_id = {r["book_id"]: r for r in payload["results"]}
        assert by_id[bid]["status"] == "succeeded"
        assert by_id[31337]["status"] == "failed"

    def test_strict_bool_and_field_types(self, env):
        bid = env.add_book("Strict", author="Batch Author", fmt="EPUB")
        _write_files(env, bid, {"Strict.epub": b"e"})
        client = _login(env)
        assert client.post("/ajax/archiveselectedbooks",
                           json={"selections": [bid]}).status_code == 400
        assert client.post("/ajax/archiveselectedbooks",
                           json={"selections": [bid], "archive": "yes"}).status_code == 400
        assert client.post("/ajax/readselectedbooks",
                           json={"selections": [bid]}).status_code == 400
        assert client.post("/ajax/readselectedbooks",
                           json={"selections": [bid], "markAsRead": 1}).status_code == 400
        assert client.post("/ajax/editselectedbooks",
                           json={"selections": [bid], "title": {"bad": "dict"}}).status_code == 400
        from cps import calibre_db
        assert calibre_db.get_book(bid) is not None


@pytest.mark.unit
class TestRecoveryRefusals:
    def test_capture_refuses_missing_folder_and_files(self, env):
        from cps import calibre_db
        from cps.book_recovery import capture_book, RecoveryError
        bid = env.add_book("No Folder", author="Author Zero", fmt="EPUB")
        with pytest.raises(RecoveryError):
            capture_book(calibre_db.get_book(bid))
        bid2 = env.add_book("No File", author="Author Zero", fmt="EPUB")
        _book_dir(env, bid2).mkdir(parents=True, exist_ok=True)
        with pytest.raises(RecoveryError, match="missing"):
            capture_book(calibre_db.get_book(bid2))

    def test_capture_refuses_nested_symlink(self, env):
        from cps import calibre_db
        from cps.book_recovery import capture_book, RecoveryError
        bid = env.add_book("Linky", author="Author Zero", fmt="EPUB")
        folder = _book_dir(env, bid)
        _write_files(env, bid, {"Linky.epub": b"e"})
        outside = env.library_dir.parent / "outside_target"
        outside.mkdir()
        (folder / "linked_dir").symlink_to(outside, target_is_directory=True)
        with pytest.raises(RecoveryError):
            capture_book(calibre_db.get_book(bid))
        folder2 = _book_dir(env, bid)
        (folder2 / "Linky.epub").unlink()
        (folder2 / "Linky.epub").symlink_to(outside / "payload.epub")
        with pytest.raises(RecoveryError):
            capture_book(calibre_db.get_book(bid))

    def test_restore_refuses_symlinked_files_dir(self, env, tmp_path):
        from cps import calibre_db
        from cps.book_recovery import capture_book, restore_book, RecoveryError
        bid = env.add_book("FilesDir", author="Author Zero", fmt="EPUB")
        _write_files(env, bid, {"FilesDir.epub": b"e"})
        rid = capture_book(calibre_db.get_book(bid))
        entry = tmp_path / "recovery" / rid
        (entry / "files").rename(entry / "files_real")
        (entry / "files").symlink_to(entry / "files_real", target_is_directory=True)
        with pytest.raises(RecoveryError):
            restore_book(rid)

    def test_restore_refuses_forged_rel_and_dest_symlink(self, env, tmp_path):
        import json as jsonlib
        from cps import calibre_db
        from cps.book_recovery import capture_book, restore_book, RecoveryError
        bid = env.add_book("Forged", author="Author Zero", fmt="EPUB")
        _write_files(env, bid, {"Forged.epub": b"e"})
        rid = capture_book(calibre_db.get_book(bid))
        entry = tmp_path / "recovery" / rid
        manifest = jsonlib.loads((entry / "manifest.json").read_text())
        manifest["files"] = [{"rel": "../../outside.evil",
                              "sha256": manifest["files"][0]["sha256"],
                              "size": manifest["files"][0]["size"]}]
        (entry / "manifest.json").write_text(jsonlib.dumps(manifest))
        with pytest.raises(RecoveryError):
            restore_book(rid)

        bid2 = env.add_book("Squatted", author="Squat Author", fmt="EPUB")
        _write_files(env, bid2, {"Squatted.epub": b"e"})
        rid2 = capture_book(calibre_db.get_book(bid2))
        from cps.editbooks import delete_book_automatic
        delete_book_automatic(calibre_db.get_book(bid2))
        author_link = env.library_dir / "Squat Author"
        outside = tmp_path / "squat"
        outside.mkdir()
        author_link.symlink_to(outside, target_is_directory=True)
        with pytest.raises(RecoveryError):
            restore_book(rid2)
        assert not (outside / "Squatted").exists()

    def test_recovery_root_symlink_refused(self, env, tmp_path, monkeypatch):
        from cps import calibre_db
        from cps.book_recovery import capture_book, RecoveryError
        bid = env.add_book("RootLink", author="Author Zero", fmt="EPUB")
        _write_files(env, bid, {"RootLink.epub": b"e"})
        real_root = tmp_path / "real_recovery"
        real_root.mkdir()
        alias = tmp_path / "recovery"
        alias.symlink_to(real_root, target_is_directory=True)
        monkeypatch.setenv("BOOK_RECOVERY_DIR", str(alias))
        with pytest.raises(RecoveryError):
            capture_book(calibre_db.get_book(bid))

    def test_app_association_failure_rolls_back_everything(self, env, monkeypatch):
        from cps import calibre_db, ub
        import cps.book_recovery as br
        bid = env.add_book("Atomic", author="Author Zero", fmt="EPUB")
        _write_files(env, bid, {"Atomic.epub": b"e"})
        ub.session.add(ub.ReadBook(book_id=bid, user_id=env.admin().id, read_status=1))
        ub.session_commit()
        rid = br.capture_book(calibre_db.get_book(bid))
        from cps.editbooks import delete_book_automatic
        delete_book_automatic(calibre_db.get_book(bid))
        calibre_db.session.expire_all()
        book_dir = env.library_dir / "Author Zero" / "Atomic"
        assert not book_dir.exists()

        real_insert = br._insert
        def boom(con, table, row, drop_pk="id", schema=""):
            if schema:
                raise br.RecoveryError("forced app insert failure")
            return real_insert(con, table, row, drop_pk=drop_pk, schema=schema)
        monkeypatch.setattr(br, "_insert", boom)
        with pytest.raises(br.RecoveryError, match="forced"):
            br.restore_book(rid)
        assert not book_dir.exists()
        con = _meta(env)
        assert con.execute("SELECT 1 FROM books WHERE id=?", (bid,)).fetchone() is None
        con.close()

    def test_format_restore_ignores_app_associations(self, env):
        from cps import calibre_db, ub
        from cps.book_recovery import capture_book, restore_book
        admin = env.admin()
        bid = env.add_book("Fmt Only", author="Author Zero", fmt="EPUB")
        _write_files(env, bid, {"Fmt Only.epub": b"e"})
        ub.session.add(ub.ReadBook(book_id=bid, user_id=admin.id, read_status=1))
        ub.session.add(ub.WebReaderProgress(book_id=bid, user_id=admin.id,
                                            cfi="cfi", percent=0.1))
        ub.session_commit()
        rid = capture_book(calibre_db.get_book(bid), "EPUB")
        (_book_dir(env, bid) / "Fmt Only.epub").unlink()
        con = _meta(env)
        con.execute("DELETE FROM data WHERE book=? AND format='EPUB'", (bid,))
        con.execute("UPDATE books SET title='Later Title' WHERE id=?", (bid,))
        con.commit()
        con.close()
        calibre_db.session.expire_all()
        app_con = sqlite3.connect(env.app_db_path)
        before = {t: app_con.execute("SELECT * FROM %s" % t).fetchall()
                  for t in ("book_read_link", "web_reader_progress", "bookmark",
                            "book_shelf_link")}
        restore_book(rid)
        after = {t: app_con.execute("SELECT * FROM %s" % t).fetchall()
                 for t in ("book_read_link", "web_reader_progress", "bookmark",
                           "book_shelf_link")}
        app_con.close()
        assert before == after
        assert _data_row(env, bid, "EPUB")
        book = calibre_db.get_book(bid)
        assert book.title == "Later Title"

    def test_two_normalized_custom_columns_same_value_id(self, env):
        from cps import calibre_db
        from cps.editbooks import delete_book_automatic
        from cps.book_recovery import capture_book, restore_book
        con = _meta(env)
        cc_ids = []
        for label in ("cc_a", "cc_b"):
            con.execute("INSERT INTO custom_columns (label, name, datatype, mark_for_delete, "
                        "editable, display, is_multiple, normalized) VALUES "
                        "(?,?,'text',0,1,'{}',0,1)", (label, label))
            cc_id = con.execute("SELECT max(id) FROM custom_columns").fetchone()[0]
            cc_ids.append(cc_id)
            con.execute("CREATE TABLE custom_column_%d (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "value TEXT)" % cc_id)
            con.execute("CREATE TABLE books_custom_column_%d_link "
                        "(id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER, value INTEGER)" % cc_id)
        con.commit(); con.close()
        from cps import db as cdb
        rows = [type("CC", (), {"id": i, "datatype": "text"}) for i in cc_ids]
        cdb.CalibreDB.setup_db_cc_classes(rows)
        bid = env.add_book("TwoCC", author="Author Zero", fmt="EPUB")
        _write_files(env, bid, {"TwoCC.epub": b"e"})
        con = _meta(env)
        for cc_id, val in zip(cc_ids, ("v-a", "v-b")):
            con.execute("INSERT INTO custom_column_%d (value) VALUES (?)" % cc_id, (val,))
            val_id = con.execute("SELECT max(id) FROM custom_column_%d" % cc_id).fetchone()[0]
            con.execute("INSERT INTO books_custom_column_%d_link (book, value) VALUES (?,?)"
                        % cc_id, (bid, val_id))
        con.commit(); con.close()
        calibre_db.session.expire_all()
        rid = capture_book(calibre_db.get_book(bid))
        delete_book_automatic(calibre_db.get_book(bid))
        calibre_db.session.expire_all()
        restore_book(rid)
        con = _meta(env)
        got = {}
        for cc_id in cc_ids:
            got[cc_id] = con.execute(
                "SELECT v.value FROM custom_column_%d v JOIN books_custom_column_%d_link l "
                "ON l.value=v.id WHERE l.book=?" % (cc_id, cc_id), (bid,)).fetchone()[0]
        con.close()
        assert sorted(got.values()) == ["v-a", "v-b"]

    def test_custom_column_definition_mismatch_refused(self, env):
        from cps import calibre_db
        from cps.editbooks import delete_book_automatic
        from cps.book_recovery import capture_book, restore_book, RecoveryError
        con = _meta(env)
        con.execute("INSERT INTO custom_columns (label, name, datatype, mark_for_delete, "
                    "editable, display, is_multiple, normalized) VALUES "
                    "('moodx','moodx','text',0,1,'{}',0,1)")
        cc_id = con.execute("SELECT max(id) FROM custom_columns").fetchone()[0]
        con.execute("CREATE TABLE custom_column_%d (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "value TEXT)" % cc_id)
        con.execute("CREATE TABLE books_custom_column_%d_link "
                    "(id INTEGER PRIMARY KEY AUTOINCREMENT, book INTEGER, value INTEGER)" % cc_id)
        con.commit(); con.close()
        from cps import db as cdb
        cdb.CalibreDB.setup_db_cc_classes([type("CC", (), {"id": cc_id, "datatype": "text"})])
        bid = env.add_book("CC Mismatch", author="Author Zero", fmt="EPUB")
        _write_files(env, bid, {"CC Mismatch.epub": b"e"})
        con = _meta(env)
        con.execute("INSERT INTO custom_column_%d (value) VALUES ('x')" % cc_id)
        val_id = con.execute("SELECT max(id) FROM custom_column_%d" % cc_id).fetchone()[0]
        con.execute("INSERT INTO books_custom_column_%d_link (book, value) VALUES (?,?)"
                    % cc_id, (bid, val_id))
        con.commit(); con.close()
        calibre_db.session.expire_all()
        rid = capture_book(calibre_db.get_book(bid))
        delete_book_automatic(calibre_db.get_book(bid))
        con = _meta(env)
        con.execute("UPDATE custom_columns SET label='other' WHERE id=?", (cc_id,))
        con.commit(); con.close()
        with pytest.raises(RecoveryError, match="custom column"):
            restore_book(rid)
        con = _meta(env)
        assert con.execute("SELECT 1 FROM books WHERE id=?", (bid,)).fetchone() is None
        con.close()


@pytest.mark.unit
class TestDeleteRevalidationAndScope:
    def test_delete_refuses_when_file_changed_after_capture(self, env):
        from cps import calibre_db
        from cps.book_recovery import RecoveryError, delete_captured_book, capture_book

        bid = env.add_book("Mutated", author="Auth", fmt="EPUB")
        _write_files(env, bid, {"Mutated.epub": b"v1", "cover.jpg": b"c"})
        rid = capture_book(calibre_db.get_book(bid))
        (_book_dir(env, bid) / "Mutated.epub").write_bytes(b"v2-newer")
        with pytest.raises(RecoveryError, match="changed since capture"):
            delete_captured_book(calibre_db.get_book(bid), recovery_id=rid)
        assert (_book_dir(env, bid) / "Mutated.epub").read_bytes() == b"v2-newer"
        assert _meta(env).execute("SELECT 1 FROM books WHERE id=?", (bid,)).fetchone()

    def test_delete_refuses_missing_captured_file(self, env):
        from cps import calibre_db
        from cps.book_recovery import RecoveryError, delete_captured_book, capture_book

        bid = env.add_book("Lost File", author="Auth", fmt="EPUB")
        _write_files(env, bid, {"Lost File.epub": b"e", "cover.jpg": b"c"})
        rid = capture_book(calibre_db.get_book(bid))
        os.remove(_book_dir(env, bid) / "cover.jpg")
        with pytest.raises(RecoveryError, match="expected file missing"):
            delete_captured_book(calibre_db.get_book(bid), recovery_id=rid)
        assert _meta(env).execute("SELECT 1 FROM books WHERE id=?", (bid,)).fetchone()

    def test_format_archive_cannot_cover_whole_book_delete(self, env):
        from cps import calibre_db
        from cps.book_recovery import RecoveryError, delete_captured_book, capture_book

        bid = env.add_book("Fmt Only", author="Auth", fmt="EPUB")
        _write_files(env, bid, {"Fmt Only.epub": b"e", "cover.jpg": b"c"})
        rid = capture_book(calibre_db.get_book(bid), "EPUB")
        with pytest.raises(RecoveryError, match="whole-book"):
            delete_captured_book(calibre_db.get_book(bid), "", recovery_id=rid)
        assert (_book_dir(env, bid) / "Fmt Only.epub").exists()

    def test_reader_positions_scoped_to_current_library(self, env):
        from cps import calibre_db, ub
        from cps.editbooks import delete_book_automatic
        from cps.book_recovery import restore_book, list_recovery

        admin = env.admin()
        bid = env.add_book("Scoped Pos", author="Auth", fmt="EPUB")
        _write_files(env, bid, {"Scoped Pos.epub": b"e"})
        lib = _meta(env).execute("SELECT uuid FROM library_id").fetchone()[0]
        ub.session.add(ub.ReaderPosition(user_id=admin.id, library_uuid=lib,
                                         book_id=bid, format="epub",
                                         cfi="epubcfi(/2)", percent=0.2))
        ub.session.add(ub.ReaderPosition(user_id=admin.id, library_uuid="other-lib",
                                         book_id=bid, format="epub",
                                         cfi="epubcfi(/9)", percent=0.9))
        ub.session_commit()

        _warning, rid = delete_book_automatic(calibre_db.get_book(bid))
        positions = {p.library_uuid: p.cfi
                     for p in ub.session.query(ub.ReaderPosition).filter_by(book_id=bid)}
        assert positions == {"other-lib": "epubcfi(/9)"}

        restored, _skipped = restore_book(rid)
        assert restored == bid
        ub.session.rollback()
        positions = {p.library_uuid: p.cfi
                     for p in ub.session.query(ub.ReaderPosition).filter_by(book_id=bid)}
        assert positions == {"other-lib": "epubcfi(/9)", lib: "epubcfi(/2)"}
        assert list_recovery()

    def test_restore_commit_failure_removes_installed_files(self, env, monkeypatch):
        from cps import calibre_db
        import cps.book_recovery as br
        from cps.book_recovery import capture_book, restore_book
        from cps.editbooks import delete_book_automatic

        bid = env.add_book("Commit Fail", author="Auth", fmt="EPUB")
        _write_files(env, bid, {"Commit Fail.epub": b"e", "cover.jpg": b"c"})
        capture_book(calibre_db.get_book(bid))
        _warning, rid = delete_book_automatic(calibre_db.get_book(bid))
        assert calibre_db.get_book(bid) is None

        real_connect = br._connect_meta

        class _FailCommit:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, *args):
                if str(sql).strip().upper() == "COMMIT":
                    raise sqlite3.OperationalError("forced commit failure")
                return self._real.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._real, name)

        monkeypatch.setattr(br, "_connect_meta",
                            lambda path: _FailCommit(real_connect(path)))
        with pytest.raises(sqlite3.OperationalError):
            restore_book(rid)
        assert not _book_dir_exists(env, bid)
        assert _meta(env).execute("SELECT 1 FROM books WHERE id=?", (bid,)).fetchone() is None

    def test_restore_queues_incremental_duplicate_scan(self, env, monkeypatch):
        from cps import calibre_db
        import cps.book_recovery as br
        import cps.editbooks as eb
        from cps.editbooks import delete_book_automatic

        bid = env.add_book("Dup Scan", author="Auth", fmt="EPUB")
        _write_files(env, bid, {"Dup Scan.epub": b"e"})
        _warning, rid = delete_book_automatic(calibre_db.get_book(bid))

        queued = []
        monkeypatch.setattr(eb, "_queue_duplicate_scan_after_change",
                            lambda ids=None: queued.append(list(ids or [])))
        br.restore_book(rid)
        assert queued == [[bid]]


def _book_dir_exists(env, book_id):
    con = _meta(env)
    row = con.execute("SELECT path FROM books WHERE id=?", (book_id,)).fetchone()
    con.close()
    return row is not None and (env.library_dir / row[0]).exists()


def test_capture_requires_library_identity(env):
    from cps import calibre_db
    from cps.book_recovery import RecoveryError, capture_book, list_recovery

    bid = env.add_book("No Identity", author="Auth", fmt="EPUB")
    _write_files(env, bid, {"No Identity.epub": b"e"})
    con = _meta(env)
    original = con.execute("SELECT uuid FROM library_id").fetchone()[0]
    con.execute("UPDATE library_id SET uuid=''")
    con.commit()
    con.close()
    calibre_db.session.expire_all()
    try:
        with pytest.raises(RecoveryError, match="library identity"):
            capture_book(calibre_db.get_book(bid))
    finally:
        con = _meta(env)
        con.execute("UPDATE library_id SET uuid=?", (original,))
        con.commit()
        con.close()
    assert (_book_dir(env, bid) / "No Identity.epub").read_bytes() == b"e"
    assert list_recovery() == []


def test_thumbnail_cache_cleared_only_on_successful_delete(env, monkeypatch):
    from cps import calibre_db, helper
    import cps.book_recovery as br
    from cps.editbooks import delete_book_automatic

    cleared = []
    monkeypatch.setattr(helper, "clear_cover_thumbnail_cache",
                        lambda bid: cleared.append(bid))
    bid = env.add_book("Thumb Cache", author="Auth", fmt="EPUB")
    _write_files(env, bid, {"Thumb Cache.epub": b"e"})
    delete_book_automatic(calibre_db.get_book(bid))
    assert cleared == [bid]

    real_connect = br._connect_meta

    class _FailCommit:
        def __init__(self, real):
            self._real = real

        def execute(self, sql, *args):
            if str(sql).strip().upper() == "COMMIT":
                raise sqlite3.OperationalError("forced commit failure")
            return self._real.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._real, name)

    bid2 = env.add_book("Thumb Fail", author="Auth", fmt="EPUB")
    _write_files(env, bid2, {"Thumb Fail.epub": b"e"})
    monkeypatch.setattr(br, "_connect_meta",
                        lambda path: _FailCommit(real_connect(path)))
    with pytest.raises(Exception):
        delete_book_automatic(calibre_db.get_book(bid2))
    assert cleared == [bid]
