# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Nightly database backups: consistent sqlite snapshots and rotation."""

import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

scripts_dir = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))

from db_backup import (backup_databases, list_snapshots, normalize_keep_count,
                       rotate_snapshots, sqlite_backup)


def _make_wal_db(path, rows=3):
    """Creates a WAL db whose latest rows live only in the -wal file (connection left open)."""
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO t (v) VALUES (?)", [(f"row{i}",) for i in range(rows)])
    con.commit()
    return con


def _count(path):
    con = sqlite3.connect(path)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return con.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    finally:
        con.close()


@pytest.mark.unit
def test_sqlite_backup_includes_wal_pages(tmp_path):
    src = tmp_path / "src.db"
    live = _make_wal_db(str(src), rows=5)
    try:
        assert os.path.getsize(str(src) + "-wal") > 0
        dest = tmp_path / "copy.db"
        sqlite_backup(str(src), str(dest))
        assert _count(dest) == 5
        assert not os.path.exists(str(dest) + "-wal")
        assert not os.path.exists(str(dest) + ".partial")
    finally:
        live.close()


@pytest.mark.unit
def test_sqlite_backup_missing_source_does_not_create_db(tmp_path):
    with pytest.raises(FileNotFoundError):
        sqlite_backup(str(tmp_path / "missing.db"), str(tmp_path / "out.db"))
    assert not (tmp_path / "missing.db").exists()


@pytest.mark.unit
def test_backup_databases_creates_valid_snapshots_and_rotates(tmp_path):
    live = [_make_wal_db(str(tmp_path / n)) for n in ("app.db", "cwa.db", "metadata.db")]
    root = tmp_path / "backup" / "db"
    sources = {n: str(tmp_path / n) for n in ("app.db", "cwa.db", "metadata.db")}
    try:
        start = datetime(2026, 1, 1, 3, 0, 0)
        for day in range(5):
            snapshot, done, errors = backup_databases(sources, str(root), keep=3,
                                                      now=start + timedelta(days=day))
            assert errors == {}
            assert set(done) == set(sources)
            for name in sources:
                assert _count(os.path.join(snapshot, name)) == 3
        snaps = list_snapshots(str(root))
        assert [os.path.basename(s) for s in snaps] == ["20260103_030000", "20260104_030000", "20260105_030000"]
    finally:
        for con in live:
            con.close()


@pytest.mark.unit
def test_same_second_snapshots_do_not_collide(tmp_path):
    live = _make_wal_db(str(tmp_path / "app.db"))
    try:
        now = datetime(2026, 1, 1, 3, 0, 0)
        a, _, _ = backup_databases({"app.db": str(tmp_path / "app.db")}, str(tmp_path / "b"), now=now)
        b, _, _ = backup_databases({"app.db": str(tmp_path / "app.db")}, str(tmp_path / "b"), now=now)
        assert a != b
        assert len(list_snapshots(str(tmp_path / "b"))) == 2
    finally:
        live.close()


@pytest.mark.unit
def test_total_failure_keeps_old_snapshots(tmp_path):
    root = tmp_path / "b"
    for i in range(3):
        (root / f"2026010{i + 1}_030000").mkdir(parents=True)
    snapshot, done, errors = backup_databases({"app.db": str(tmp_path / "missing.db")}, str(root), keep=1)
    assert done == {} and "app.db" in errors
    assert not os.path.exists(snapshot)
    assert len(list_snapshots(str(root))) == 3


@pytest.mark.unit
def test_rotation_ignores_unrelated_dirs(tmp_path):
    (tmp_path / "keepme").mkdir()
    for i in range(4):
        (tmp_path / f"2026010{i + 1}_030000").mkdir()
    removed = rotate_snapshots(str(tmp_path), 2)
    assert len(removed) == 2
    assert (tmp_path / "keepme").is_dir()


@pytest.mark.unit
@pytest.mark.parametrize("value,expected", [(7, 7), ("3", 3), (0, 7), (-1, 7), (None, 7), ("x", 7), (False, 7)])
def test_normalize_keep_count(value, expected):
    assert normalize_keep_count(value) == expected
