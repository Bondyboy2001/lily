# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Restores run as background tasks: the request only queues them, the task
validates, takes a safety copy, swaps databases in and rolls back on failure."""

import ast
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
def test_wipe_book_linked_tables_skips_missing(tmp_path):
    path = str(tmp_path / "app.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE downloads (id INTEGER)")
    con.execute("CREATE TABLE user (id INTEGER)")
    con.execute("INSERT INTO downloads VALUES (1)")
    con.execute("INSERT INTO user VALUES (1)")
    con.commit()
    con.close()
    restore_mod.TaskRestoreCalibreLibrary.wipe_book_linked_tables(path)
    con = sqlite3.connect(path)
    try:
        assert con.execute("SELECT COUNT(*) FROM downloads").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM user").fetchone()[0] == 1
    finally:
        con.close()


@pytest.mark.unit
def test_restore_routes_do_not_block_on_subprocess():
    """The request handlers only queue tasks; no subprocess call runs in the request."""
    tree = ast.parse((REPO / "cps" / "admin.py").read_text())
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               and n.name in ("restore_calibre_db", "restore_db_snapshot", "db_backups")):
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert node.value.id != "subprocess", f"{fn.name} calls subprocess"


# ---------------------------------------------------------------------------- routes

@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    from .lily_env import lily_env, ADMIN_PASSWORD
    from .test_lily_reader_static import _register_remaining_blueprints
    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path / "cfg"))
    root = tmp_path / "backups"
    (root / "20260101_030000").mkdir(parents=True)
    _db(str(root / "20260101_030000" / "cwa.db"), "x")
    monkeypatch.setattr(task_mod, "get_backup_root", lambda: str(root))
    queued = []
    from cps.services.worker import WorkerThread
    monkeypatch.setattr(WorkerThread, "add", classmethod(lambda cls, user, task, hidden=False: queued.append(task)))
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        _register_remaining_blueprints(env.app)
        c = env.app.test_client()
        c.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
        yield c, queued
    restore_mod._restore_pending.clear()


@pytest.mark.unit
def test_backups_page_lists_snapshots(admin_client):
    c, _ = admin_client
    resp = c.get("/admin/db_backups")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "2026-01-01 03:00" in html and "cwa.db" in html
    assert 'name="db_backup_dir"' in html and 'name="processed_books_retention_days"' in html


@pytest.mark.unit
def test_snapshot_download_returns_zip_and_rejects_bad_names(admin_client):
    import io
    import zipfile
    c, _ = admin_client
    resp = c.get("/admin/db_backups/download/20260101_030000")
    assert resp.status_code == 200
    assert resp.mimetype == "application/zip"
    assert zipfile.ZipFile(io.BytesIO(resp.data)).namelist() == ["cwa.db"]
    assert c.get("/admin/db_backups/download/20990101_000000").status_code == 404
    assert c.get("/admin/db_backups/download/..%2Fetc").status_code == 404


@pytest.mark.unit
def test_restore_routes_queue_tasks_and_return_immediately(admin_client):
    c, queued = admin_client
    resp = c.post("/admin/db_backups/restore", data={"snapshot": "20260101_030000", "databases": ["cwa.db"]},
                  headers={"Accept": "application/json"})
    assert resp.status_code == 200 and resp.get_json()["success"] is True
    assert isinstance(queued[-1], task_mod.TaskRestoreDatabaseSnapshot)
    # A second restore is refused while the first is queued
    resp = c.post("/admin/db_backups/restore", data={"snapshot": "20260101_030000", "databases": ["cwa.db"]},
                  headers={"Accept": "application/json"})
    assert resp.status_code == 409
    restore_mod._restore_pending.clear()
    resp = c.post("/admin/db_backups/restore", data={"snapshot": "../etc", "databases": ["cwa.db"]},
                  headers={"Accept": "application/json"})
    assert resp.status_code == 404
    resp = c.post("/admin/restore_calibre_db")
    assert resp.status_code == 302
    assert isinstance(queued[-1], restore_mod.TaskRestoreCalibreLibrary)


@pytest.mark.unit
def test_backup_settings_saved(admin_client, tmp_path):
    c, _ = admin_client
    resp = c.post("/admin/db_backups/settings", data={"db_backup_dir": "/mnt/backups", "processed_books_retention_days": "14"})
    assert resp.status_code == 302
    from cwa_db import CWA_DB
    with CWA_DB() as db:
        assert db.cwa_settings["db_backup_dir"] == "/mnt/backups"
        assert db.cwa_settings["processed_books_retention_days"] == "14"
    resp = c.post("/admin/db_backups/settings", data={"db_backup_dir": "relative", "processed_books_retention_days": "14"})
    with CWA_DB() as db:
        assert db.cwa_settings["db_backup_dir"] == "/mnt/backups"


@pytest.mark.unit
def test_mirror_settings_validated_and_run_now_queues_task(admin_client, tmp_path, monkeypatch):
    from cps import config
    from cps.tasks import library_mirror as mirror_task
    c, queued = admin_client
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    monkeypatch.setattr(config, "config_calibre_dir", str(lib_dir), raising=False)

    html = c.post("/admin/db_backups/settings", follow_redirects=True,
                  data={"library_mirror_dir": str(lib_dir / "inside"), "processed_books_retention_days": "14"}).get_data(as_text=True)
    assert "must not contain or sit inside" in html
    html = c.post("/admin/db_backups/settings", follow_redirects=True,
                  data={"library_mirror_dir": "relative", "processed_books_retention_days": "14"}).get_data(as_text=True)
    assert "absolute path" in html

    assert c.post("/admin/db_backups/settings", follow_redirects=True,
                  data={"library_mirror_dir": "/mnt/mirror", "processed_books_retention_days": "14"}).status_code == 200
    assert mirror_task.get_mirror_dir() == "/mnt/mirror"
    assert 'name="library_mirror_dir"' in c.get("/admin/db_backups").get_data(as_text=True)

    c.post("/admin/db_backups/mirror")
    assert any(isinstance(t, mirror_task.TaskMirrorLibrary) for t in queued)
