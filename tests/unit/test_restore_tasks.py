# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Restores run as background tasks: the request only queues them, the task
validates, takes a safety copy, swaps databases in and rolls back on failure."""

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from cps.services.worker import STAT_FAIL, STAT_FINISH_SUCCESS
from cps.tasks import db_backup as task_mod
from cps.tasks import restore as restore_mod


def _db(path, value):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
    con.execute("DELETE FROM t")
    con.execute("INSERT INTO t VALUES (?)", (value,))
    con.commit()
    con.close()


def _value(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT v FROM t").fetchone()[0]
    finally:
        con.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    live_dir = tmp_path / "config"
    live_dir.mkdir()
    root = tmp_path / "backups"
    snap = root / "20260101_030000"
    snap.mkdir(parents=True)
    live = {n: str(live_dir / n) for n in ("app.db", "cwa.db", "metadata.db")}
    for n, p in live.items():
        _db(p, "live-" + n)
        _db(str(snap / n), "snap-" + n)
    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(locks))
    monkeypatch.setattr(task_mod, "get_backup_root", lambda: str(root))
    monkeypatch.setattr(task_mod, "get_backup_sources", lambda: dict(live))
    reloaded = []
    monkeypatch.setattr(task_mod.TaskRestoreDatabaseSnapshot, "_reload", lambda self, dbs: reloaded.append(list(dbs)))
    return {"root": root, "snap": snap, "live": live, "reloaded": reloaded}


@pytest.mark.unit
def test_snapshot_restore_takes_safety_copy_and_swaps(env):
    assert restore_mod.mark_restore_queued()
    assert not restore_mod.mark_restore_queued()  # only one restore at a time
    task = task_mod.TaskRestoreDatabaseSnapshot("20260101_030000", ["cwa.db", "metadata.db"])
    task.start(None)
    assert task.stat == STAT_FINISH_SUCCESS, task.error
    assert not restore_mod.restore_in_progress()
    assert _value(env["live"]["cwa.db"]) == "snap-cwa.db"
    assert _value(env["live"]["metadata.db"]) == "snap-metadata.db"
    assert _value(env["live"]["app.db"]) == "live-app.db"  # not selected
    safety = [d for d in os.listdir(env["root"]) if d.endswith("_pre-restore")]
    assert len(safety) == 1
    assert _value(str(env["root"] / safety[0] / "cwa.db")) == "live-cwa.db"
    assert env["reloaded"] == [["cwa.db", "metadata.db"]]


@pytest.mark.unit
def test_snapshot_restore_refuses_corrupt_snapshot(env):
    (env["snap"] / "metadata.db").write_bytes(b"garbage" * 1000)
    task = task_mod.TaskRestoreDatabaseSnapshot("20260101_030000")
    task.start(None)
    assert task.stat == STAT_FAIL
    for n, p in env["live"].items():
        assert _value(p) == "live-" + n
    assert not [d for d in os.listdir(env["root"]) if d.endswith("_pre-restore")]


@pytest.mark.unit
def test_snapshot_restore_rolls_back_on_failure(env, monkeypatch):
    real = task_mod.restore_sqlite_db
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if dst == env["live"]["metadata.db"] and len(calls) == 3:
            raise RuntimeError("disk full")
        return real(src, dst)

    monkeypatch.setattr(task_mod, "restore_sqlite_db", flaky)
    task = task_mod.TaskRestoreDatabaseSnapshot("20260101_030000")
    task.start(None)
    assert task.stat == STAT_FAIL and "rolled back" in task.error
    for n, p in env["live"].items():
        assert _value(p) == "live-" + n


@pytest.mark.unit
def test_snapshot_restore_aborts_when_ingest_is_busy(env):
    import fcntl
    holder = open(os.path.join(tempfile.gettempdir(), "ingest_processor.lock"), "a+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        task = task_mod.TaskRestoreDatabaseSnapshot("20260101_030000")
        task.start(None)
        assert task.stat == STAT_FAIL and "ingest processor" in task.error
        assert _value(env["live"]["app.db"]) == "live-app.db"
    finally:
        holder.close()
    # The cover enforcer's existence lock was not left behind
    assert not os.path.exists(os.path.join(tempfile.gettempdir(), "cover_enforcer.lock"))


@pytest.mark.unit
def test_library_restore_handles_timeout(env, monkeypatch, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    _db(str(lib / "metadata.db"), "m")
    monkeypatch.setattr(restore_mod.config, "config_calibre_dir", str(lib), raising=False)
    monkeypatch.setattr(restore_mod.ub, "app_DB_path", env["live"]["app.db"])
    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path / "cfg"))
    monkeypatch.setattr(restore_mod.calibre_db, "dispose", lambda: None, raising=False)
    reconnects = []
    monkeypatch.setattr(restore_mod.calibre_db, "reconnect_db", lambda *a: reconnects.append(1), raising=False)
    monkeypatch.setattr(restore_mod, "get_calibre_binarypath", lambda name: "/bin/true")

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(restore_mod.subprocess, "run", slow)
    task = restore_mod.TaskRestoreCalibreLibrary()
    task.start(None)
    assert task.stat == STAT_FAIL and "timed out" in task.error
    assert reconnects == [1]
    assert not restore_mod.restore_in_progress()
    backups = list((tmp_path / "cfg" / "backup").glob("restore_*"))
    assert len(backups) == 1 and (backups[0] / "metadata.db.bak").is_file()
    assert "timed out" in (backups[0] / "restore.log").read_text()


@pytest.mark.unit
def test_restore_cleanup_removes_only_rows_of_missing_books(tmp_path):
    """calibredb restore_database keeps book ids, so only rows of books that are gone go."""
    meta = str(tmp_path / "metadata.db")
    con = sqlite3.connect(meta)
    con.execute("CREATE TABLE books (id INTEGER PRIMARY KEY)")
    con.executemany("INSERT INTO books VALUES (?)", [(1,), (2,)])
    con.commit()
    con.close()
    path = str(tmp_path / "app.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE downloads (id INTEGER PRIMARY KEY, book_id INTEGER, user_id INTEGER)")
    con.execute("CREATE TABLE book_shelf_link (id INTEGER PRIMARY KEY, book_id INTEGER, shelf INTEGER)")
    con.execute("CREATE TABLE web_reader_progress (id INTEGER PRIMARY KEY, user_id INTEGER, book_id INTEGER, percent REAL)")
    con.execute("CREATE TABLE user (id INTEGER)")
    con.executemany("INSERT INTO downloads (book_id, user_id) VALUES (?, 1)", [(1,), (3,)])
    con.executemany("INSERT INTO book_shelf_link (book_id, shelf) VALUES (?, 1)", [(2,), (9,)])
    con.executemany("INSERT INTO web_reader_progress (user_id, book_id, percent) VALUES (1, ?, 0.5)", [(1,), (7,)])
    con.execute("INSERT INTO user VALUES (1)")
    con.commit()
    con.close()

    removed = restore_mod.remove_orphan_book_rows(path, meta)
    assert removed == {"downloads": 1, "book_shelf_link": 1, "web_reader_progress": 1}
    con = sqlite3.connect(path)
    try:
        assert [r[0] for r in con.execute("SELECT book_id FROM downloads")] == [1]
        assert [r[0] for r in con.execute("SELECT book_id FROM book_shelf_link")] == [2]
        assert [r[0] for r in con.execute("SELECT book_id FROM web_reader_progress")] == [1]
        assert con.execute("SELECT COUNT(*) FROM user").fetchone()[0] == 1
    finally:
        con.close()


@pytest.mark.unit
def test_restore_cleanup_leaves_everything_when_the_library_came_back_empty(tmp_path):
    meta = str(tmp_path / "metadata.db")
    con = sqlite3.connect(meta)
    con.execute("CREATE TABLE books (id INTEGER PRIMARY KEY)")
    con.close()
    path = str(tmp_path / "app.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE book_read_link (id INTEGER PRIMARY KEY, book_id INTEGER)")
    con.execute("INSERT INTO book_read_link (book_id) VALUES (5)")
    con.commit()
    con.close()
    assert restore_mod.remove_orphan_book_rows(path, meta) == {}
    con = sqlite3.connect(path)
    try:
        assert con.execute("SELECT COUNT(*) FROM book_read_link").fetchone()[0] == 1
    finally:
        con.close()


@pytest.mark.unit
def test_snapshot_restore_lists_book_folders_the_restored_db_does_not_know(tmp_path):
    from cps import library_orphans
    lib = tmp_path / "lib"
    for rel in ("A/Old (1)", "A/New (2)", "B/Empty (3)", ".lily-trash/20260101T000000_4"):
        (lib / rel).mkdir(parents=True)
    (lib / "A" / "Old (1)" / "old.epub").write_text("x")
    (lib / "A" / "New (2)" / "new.epub").write_text("x")
    (lib / "A" / "New (2)" / "cover.jpg").write_text("x")
    (lib / ".lily-trash" / "20260101T000000_4" / "t.epub").write_text("x")
    meta = lib / "metadata.db"
    con = sqlite3.connect(meta)
    con.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, path TEXT)")
    con.execute("INSERT INTO books VALUES (1, 'A/Old (1)')")
    con.commit()
    con.close()
    app_db = tmp_path / "app.db"
    con = sqlite3.connect(app_db)
    con.execute("CREATE TABLE book_read_link (id INTEGER PRIMARY KEY, book_id INTEGER)")
    con.executemany("INSERT INTO book_read_link (book_id) VALUES (?)", [(1,), (2,)])
    con.commit()
    con.close()
    cfg = tmp_path / "cfg"
    cfg.mkdir()

    note = restore_mod.reconcile_after_restore(["metadata.db"], str(app_db), str(meta), str(lib), str(cfg),
                                               "snapshot 20260101_030000")
    assert "1 book folder" in str(note)
    report = library_orphans.load_report(str(cfg))
    assert report["folders"] == ["A/New (2)"] and report["source"] == "snapshot 20260101_030000"
    assert library_orphans.book_files(str(lib / "A" / "New (2)")) == ["new.epub"]
    con = sqlite3.connect(app_db)
    try:
        assert [r[0] for r in con.execute("SELECT book_id FROM book_read_link")] == [1]
    finally:
        con.close()
    # Nothing out of step: no note, and the old report is cleared
    (lib / "A" / "New (2)" / "new.epub").unlink()
    (lib / "A" / "New (2)" / "cover.jpg").unlink()
    assert restore_mod.reconcile_after_restore(["metadata.db"], str(app_db), str(meta), str(lib), str(cfg), "") == ""
    assert library_orphans.load_report(str(cfg)) == {}
