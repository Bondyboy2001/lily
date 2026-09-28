# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""cwa.db schema sync is additive only: unknown columns survive, missing ones are
added, nothing is renamed by position, and explicit migrations run exactly once."""

import sqlite3
import sys
from pathlib import Path

import pytest

scripts_dir = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))

import cwa_db as cwa_db_module
from cwa_db import CWA_DB, invalidate_schema_cache, parse_schema_columns


@pytest.fixture
def cwa_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path))
    monkeypatch.delenv("NETWORK_SHARE_MODE", raising=False)
    invalidate_schema_cache()
    yield tmp_path
    invalidate_schema_cache()


def _columns(path, table):
    con = sqlite3.connect(path)
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info('{table}')")]
    finally:
        con.close()


def _reopen(cwa_dir):
    invalidate_schema_cache()
    return CWA_DB()


@pytest.mark.unit
def test_unknown_settings_column_is_preserved(cwa_dir):
    CWA_DB().close()
    db_file = str(cwa_dir / "cwa.db")
    con = sqlite3.connect(db_file)
    con.execute("ALTER TABLE cwa_settings ADD COLUMN future_setting TEXT DEFAULT 'keep me' NOT NULL")
    con.execute("UPDATE cwa_settings SET future_setting='user value'")
    con.commit()
    con.close()

    db = _reopen(cwa_dir)
    try:
        assert "future_setting" in _columns(db_file, "cwa_settings")
        assert db.cur.execute("SELECT future_setting FROM cwa_settings").fetchone()[0] == "user value"
        # Saving settings must still work with the extra column present
        db.update_cwa_settings({"auto_backup_imports": 0})
        assert db.cwa_settings["auto_backup_imports"] is False
    finally:
        db.close()


@pytest.mark.unit
def test_unknown_stats_column_is_preserved(cwa_dir):
    CWA_DB().close()
    db_file = str(cwa_dir / "cwa.db")
    con = sqlite3.connect(db_file)
    con.execute("ALTER TABLE cwa_import ADD COLUMN newer_col TEXT")
    con.commit()
    con.close()
    _reopen(cwa_dir).close()
    assert "newer_col" in _columns(db_file, "cwa_import")


@pytest.mark.unit
def test_missing_columns_are_added(cwa_dir):
    CWA_DB().close()
    db_file = str(cwa_dir / "cwa.db")
    con = sqlite3.connect(db_file)
    con.execute("ALTER TABLE cwa_settings DROP COLUMN db_backup_keep_count")
    # Old cwa_import without original_backed_up
    con.execute("DROP TABLE cwa_import")
    con.execute("CREATE TABLE cwa_import(id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, "
                "timestamp TEXT NOT NULL, filename TEXT NOT NULL)")
    con.commit()
    con.close()

    db = _reopen(cwa_dir)
    try:
        assert "db_backup_keep_count" in _columns(db_file, "cwa_settings")
        assert db.cwa_settings["db_backup_keep_count"] == 7
        assert "original_backed_up" in _columns(db_file, "cwa_import")
    finally:
        db.close()


@pytest.mark.unit
def test_no_positional_rename(cwa_dir):
    """Same column count, different names: previously renamed `legacy_name` to the
    schema's column at that position. Now the data stays under its own name."""
    CWA_DB().close()
    db_file = str(cwa_dir / "cwa.db")
    con = sqlite3.connect(db_file)
    con.execute("DROP TABLE cwa_import")
    con.execute("CREATE TABLE cwa_import(id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, "
                "timestamp TEXT NOT NULL, filename TEXT NOT NULL, legacy_name TEXT NOT NULL DEFAULT '')")
    con.execute("INSERT INTO cwa_import(timestamp, filename, legacy_name) VALUES ('t', 'f', 'precious')")
    con.commit()
    con.close()

    _reopen(cwa_dir).close()
    cols = _columns(db_file, "cwa_import")
    assert "legacy_name" in cols
    assert "original_backed_up" in cols  # added, not renamed into
    con = sqlite3.connect(db_file)
    try:
        assert con.execute("SELECT legacy_name FROM cwa_import").fetchone()[0] == "precious"
    finally:
        con.close()


@pytest.mark.unit
def test_column_lookup_is_exact_identifier(cwa_dir):
    db = CWA_DB()
    try:
        columns = parse_schema_columns(db.tables)
        # "timestamp" must resolve inside its own table, not scan_timestamp elsewhere
        assert columns["cwa_duplicate_cache"]["scan_timestamp"].startswith("scan_timestamp ")
        assert "timestamp" not in columns["cwa_duplicate_cache"]
        # Comment text stripped, quoted defaults kept intact
        assert "--" not in columns["cwa_settings"]["duplicate_auto_resolve_cooldown_minutes"]
        assert columns["cwa_settings"]["duplicate_format_priority"].endswith("NOT NULL")
        # A substring of a real setting name is not a setting
        assert db.add_missing_setting("backup") is False
    finally:
        db.close()


@pytest.mark.unit
def test_explicit_migrations_run_once_in_order(cwa_dir, monkeypatch):
    calls = []
    migrations = [
        (2, "second", lambda cur: calls.append(2)),
        (1, "first", lambda cur: (calls.append(1), cur.execute("ALTER TABLE cwa_import ADD COLUMN m1 TEXT"))),
    ]
    monkeypatch.setattr(cwa_db_module, "MIGRATIONS", migrations)
    CWA_DB().close()
    _reopen(cwa_dir).close()
    assert calls == [1, 2]
    db_file = str(cwa_dir / "cwa.db")
    assert "m1" in _columns(db_file, "cwa_import")
    con = sqlite3.connect(db_file)
    try:
        assert [r[0] for r in con.execute("SELECT version FROM cwa_schema_migrations ORDER BY version")] == [1, 2]
    finally:
        con.close()


@pytest.mark.unit
def test_failed_migration_rolls_back_and_stops(cwa_dir, monkeypatch):
    def boom(cur):
        cur.execute("ALTER TABLE cwa_import ADD COLUMN half_done TEXT")
        raise RuntimeError("fail")

    later = []
    monkeypatch.setattr(cwa_db_module, "MIGRATIONS", [(1, "boom", boom), (2, "later", lambda cur: later.append(1))])
    CWA_DB().close()
    db_file = str(cwa_dir / "cwa.db")
    assert "half_done" not in _columns(db_file, "cwa_import")
    assert later == []
    con = sqlite3.connect(db_file)
    try:
        assert con.execute("SELECT COUNT(*) FROM cwa_schema_migrations").fetchone()[0] == 0
    finally:
        con.close()
