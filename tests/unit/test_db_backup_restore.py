# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Per-database snapshot rotation, snapshot listing, integrity checks and restore."""

import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

scripts_dir = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))

from db_backup import (backup_databases, check_integrity, list_snapshots, resolve_snapshot, restore_sqlite_db,
                       rotate_snapshots)


def _db(path, value, wal=True):
    con = sqlite3.connect(path)
    if wal:
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


@pytest.mark.unit
def test_rotation_is_per_database_and_keeps_last_good_copy(tmp_path):
    """metadata.db backups fail from day 3 on; its last good copies must survive
    rotation even though newer snapshots (with only app.db) keep arriving."""
    app, meta = tmp_path / "app.db", tmp_path / "metadata.db"
    _db(str(app), "a")
    _db(str(meta), "m")
    root = tmp_path / "b"
    start = datetime(2026, 1, 1, 3, 0, 0)
    for day in range(10):
        sources = {"app.db": str(app), "metadata.db": str(meta) if day < 3 else str(tmp_path / "gone.db")}
        backup_databases(sources, str(root), keep=2, now=start + timedelta(days=day))

    snaps = {os.path.basename(s): sorted(os.listdir(s)) for s in list_snapshots(str(root))}
    with_meta = [n for n, files in snaps.items() if "metadata.db" in files]
    with_app = [n for n, files in snaps.items() if "app.db" in files]
    assert with_meta == ["20260102_030000", "20260103_030000"]
    assert with_app == ["20260109_030000", "20260110_030000"]
    # Directories whose databases were all pruned are gone
    assert set(snaps) == set(with_meta) | set(with_app)


@pytest.mark.unit
def test_rotation_skips_pre_restore_snapshots(tmp_path):
    for i in range(4):
        d = tmp_path / f"2026010{i + 1}_030000"
        d.mkdir()
        _db(str(d / "app.db"), str(i), wal=False)
    safety = tmp_path / "20260101_020000_pre-restore"
    safety.mkdir()
    _db(str(safety / "app.db"), "s", wal=False)
    rotate_snapshots(str(tmp_path), 1)
    assert safety.is_dir() and (safety / "app.db").is_file()
    assert [os.path.basename(s) for s in list_snapshots(str(tmp_path))] == ["20260104_030000"]


@pytest.mark.unit
@pytest.mark.parametrize("name", ["../etc", "20260101_030000/../x", "", "20260101_030000\n", "foo"])
def test_resolve_snapshot_rejects_bad_names(tmp_path, name):
    (tmp_path / "20260101_030000").mkdir()
    with pytest.raises((ValueError, FileNotFoundError)):
        resolve_snapshot(str(tmp_path), name)


@pytest.mark.unit
def test_check_integrity_rejects_corrupt_file(tmp_path):
    bad = tmp_path / "bad.db"
    good = tmp_path / "good.db"
    _db(str(good), "x", wal=False)
    data = bytearray(good.read_bytes())
    # Keep the header, trash the table b-tree page
    data[4096:4200] = b"\xff" * 104
    bad.write_bytes(bytes(data))
    check_integrity(str(good))
    with pytest.raises(Exception):
        check_integrity(str(bad))


@pytest.mark.unit
def test_restore_replaces_live_wal_db_and_keeps_wal_mode(tmp_path):
    live = tmp_path / "app.db"
    _db(str(live), "old")
    snap = tmp_path / "snap.db"
    _db(str(snap), "restored", wal=False)
    reader = sqlite3.connect(str(live))  # another open connection, like the web app's
    try:
        restore_sqlite_db(str(snap), str(live))
        assert reader.execute("SELECT v FROM t").fetchone()[0] == "restored"
    finally:
        reader.close()
    assert _value(str(live)) == "restored"
    con = sqlite3.connect(str(live))
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        con.close()


@pytest.mark.unit
def test_restore_refuses_corrupt_snapshot_and_leaves_live_untouched(tmp_path):
    live = tmp_path / "app.db"
    _db(str(live), "old")
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a database" * 100)
    with pytest.raises(Exception):
        restore_sqlite_db(str(bad), str(live))
    assert _value(str(live)) == "old"


@pytest.mark.unit
def test_prune_processed_books(tmp_path):
    from cps.tasks.processed_cleanup import normalize_retention_days, prune_processed_books
    now = time.time()
    old, new = now - 40 * 86400, now - 5 * 86400
    for sub in ("imported", "failed", "duplicate_resolutions"):
        (tmp_path / sub / "nested").mkdir(parents=True)
        for name, mtime in (("old.epub", old), ("new.epub", new), ("nested/old.epub", old)):
            p = tmp_path / sub / name
            p.write_text("x")
            os.utime(p, (mtime, mtime))

    assert prune_processed_books(str(tmp_path), 0, now=now) == []
    removed = prune_processed_books(str(tmp_path), 30, now=now)
    assert len(removed) == 2
    # The retired import copies aren't pruned by age: the nightly task removes the folder whole
    assert (tmp_path / "imported" / "old.epub").exists()
    for sub in ("failed",):
        assert (tmp_path / sub / "new.epub").exists()
        assert not (tmp_path / sub / "old.epub").exists()
        assert not (tmp_path / sub / "nested").exists()
        assert (tmp_path / sub).is_dir()
    # Duplicate-resolution backups are never pruned
    assert (tmp_path / "duplicate_resolutions" / "old.epub").exists()

    from cps.tasks.processed_cleanup import remove_import_copies
    assert remove_import_copies(str(tmp_path)) is True
    assert not (tmp_path / "imported").exists() and (tmp_path / "failed").is_dir()
    assert (tmp_path / "duplicate_resolutions" / "old.epub").exists()
    assert remove_import_copies(str(tmp_path)) is False

    assert normalize_retention_days("30") == 30
    assert normalize_retention_days("0") == 0
    assert normalize_retention_days("-3") == 30
    assert normalize_retention_days("junk") == 30


def test_verify_snapshot_restores_to_scratch(tmp_path):
    from db_backup import verify_snapshot
    src = tmp_path / "metadata.db"
    _db(str(src), "x")
    snap, done, errors = backup_databases({"metadata.db": str(src)}, str(tmp_path / "bk"))
    assert not errors
    assert verify_snapshot(snap) == {"metadata.db": 1}


def test_verify_snapshot_rejects_corrupt_and_empty(tmp_path):
    from db_backup import verify_snapshot
    snap = tmp_path / "20260101_000000"
    snap.mkdir()
    with pytest.raises(FileNotFoundError):
        verify_snapshot(str(snap))
    (snap / "app.db").write_bytes(b"not a database" * 100)
    with pytest.raises(Exception):
        verify_snapshot(str(snap))
