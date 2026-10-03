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
def test_missing_columns_are_added(cwa_dir):
    CWA_DB().close()
    db_file = str(cwa_dir / "cwa.db")
    con = sqlite3.connect(db_file)
    con.execute("ALTER TABLE cwa_settings DROP COLUMN db_backup_keep_count")
    # Old cwa_enforcement without trigger_type
    con.execute("DROP TABLE cwa_enforcement")
    con.execute("CREATE TABLE cwa_enforcement(id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, "
                "timestamp TEXT NOT NULL, book_id INTEGER NOT NULL, book_title TEXT NOT NULL, "
                "author TEXT NOT NULL, file_path TEXT NOT NULL)")
    con.commit()
    con.close()

    db = _reopen(cwa_dir)
    try:
        assert "db_backup_keep_count" in _columns(db_file, "cwa_settings")
        assert db.cwa_settings["db_backup_keep_count"] == 7
        assert "trigger_type" in _columns(db_file, "cwa_enforcement")
    finally:
        db.close()


@pytest.mark.unit
def test_no_positional_rename(cwa_dir):
    """Same column count, different names: previously renamed `legacy_name` to the
    schema's column at that position. Now the data stays under its own name."""
    CWA_DB().close()
    db_file = str(cwa_dir / "cwa.db")
    con = sqlite3.connect(db_file)
    con.execute("DROP TABLE cwa_enforcement")
    con.execute("CREATE TABLE cwa_enforcement(id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, "
                "timestamp TEXT NOT NULL, book_id INTEGER NOT NULL, book_title TEXT NOT NULL, "
                "author TEXT NOT NULL, file_path TEXT NOT NULL, legacy_name TEXT NOT NULL DEFAULT '')")
    con.execute("INSERT INTO cwa_enforcement(timestamp, book_id, book_title, author, file_path, legacy_name) "
                "VALUES ('t', 1, 'b', 'a', 'f', 'precious')")
    con.commit()
    con.close()

    _reopen(cwa_dir).close()
    cols = _columns(db_file, "cwa_enforcement")
    assert "legacy_name" in cols
    assert "trigger_type" in cols  # added, not renamed into
    con = sqlite3.connect(db_file)
    try:
        assert con.execute("SELECT legacy_name FROM cwa_enforcement").fetchone()[0] == "precious"
    finally:
        con.close()


@pytest.mark.unit
def test_the_old_import_log_is_dropped(cwa_dir):
    con = sqlite3.connect(cwa_dir / "cwa.db")
    con.execute("CREATE TABLE cwa_import(id INTEGER PRIMARY KEY, timestamp TEXT, filename TEXT)")
    con.commit()
    con.close()

    CWA_DB().close()
    con = sqlite3.connect(cwa_dir / "cwa.db")
    try:
        assert con.execute("SELECT name FROM sqlite_master WHERE name = 'cwa_import'").fetchone() is None
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
        (1, "first", lambda cur: (calls.append(1), cur.execute("ALTER TABLE cwa_enforcement ADD COLUMN m1 TEXT"))),
    ]
    monkeypatch.setattr(cwa_db_module, "MIGRATIONS", migrations)
    CWA_DB().close()
    _reopen(cwa_dir).close()
    assert calls == [1, 2]
    db_file = str(cwa_dir / "cwa.db")
    assert "m1" in _columns(db_file, "cwa_enforcement")
    con = sqlite3.connect(db_file)
    try:
        assert [r[0] for r in con.execute("SELECT version FROM cwa_schema_migrations ORDER BY version")] == [1, 2]
    finally:
        con.close()


@pytest.mark.unit
def test_failed_migration_rolls_back_and_stops(cwa_dir, monkeypatch):
    def boom(cur):
        cur.execute("ALTER TABLE cwa_enforcement ADD COLUMN half_done TEXT")
        raise RuntimeError("fail")

    later = []
    monkeypatch.setattr(cwa_db_module, "MIGRATIONS", [(1, "boom", boom), (2, "later", lambda cur: later.append(1))])
    CWA_DB().close()
    db_file = str(cwa_dir / "cwa.db")
    assert "half_done" not in _columns(db_file, "cwa_enforcement")
    assert later == []
    con = sqlite3.connect(db_file)
    try:
        assert con.execute("SELECT COUNT(*) FROM cwa_schema_migrations").fetchone()[0] == 0
    finally:
        con.close()


@pytest.mark.unit
def test_a_new_library_fetches_metadata_for_new_books(cwa_dir):
    # Off by default, a fresh install imported a paper with no abstract, date or arXiv id
    db = CWA_DB()
    try:
        assert db.get_cwa_settings()["auto_metadata_fetch_enabled"] == 1
    finally:
        db.close()


@pytest.mark.unit
def test_an_existing_library_keeps_its_fetch_setting(cwa_dir):
    CWA_DB().close()
    db_file = str(cwa_dir / "cwa.db")
    con = sqlite3.connect(db_file)
    con.execute("UPDATE cwa_settings SET auto_metadata_fetch_enabled=0")
    con.commit()
    con.close()
    db = _reopen(cwa_dir)
    try:
        assert db.get_cwa_settings()["auto_metadata_fetch_enabled"] == 0
    finally:
        db.close()


@pytest.mark.unit
def test_migration_3_drops_the_settings_and_tables_of_removed_features(cwa_dir):
    db_file = str(cwa_dir / "cwa.db")
    # A cwa.db from before: a removed setting holding a value, and a removed table with rows
    con = sqlite3.connect(db_file)
    con.execute("CREATE TABLE cwa_settings (default_settings SMALLINT DEFAULT 1 NOT NULL, "
                "auto_backup_imports SMALLINT DEFAULT 0 NOT NULL, "
                "hardcover_auto_fetch_enabled SMALLINT DEFAULT 1 NOT NULL, "
                "koreader_sync_enabled SMALLINT DEFAULT 0 NOT NULL)")
    con.execute("INSERT INTO cwa_settings DEFAULT VALUES")
    con.execute("CREATE TABLE cwa_user_activity (id INTEGER PRIMARY KEY, user_id INTEGER)")
    con.execute("CREATE INDEX idx_activity_user ON cwa_user_activity(user_id)")
    con.execute("INSERT INTO cwa_user_activity (user_id) VALUES (1)")
    con.commit()
    con.close()

    db = _reopen(cwa_dir)
    try:
        columns = _columns(db_file, "cwa_settings")
        assert "hardcover_auto_fetch_enabled" not in columns and "koreader_sync_enabled" not in columns
        # Kept settings keep their values
        assert db.cur.execute("SELECT auto_backup_imports FROM cwa_settings").fetchone()[0] == 0
        tables = {r[0] for r in db.cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "cwa_user_activity" not in tables
        assert 3 in {r[0] for r in db.cur.execute("SELECT version FROM cwa_schema_migrations")}
    finally:
        db.close()
    # The cwa.db from before is kept beside it
    # The copy is named after the newest migration that was pending
    assert list(cwa_dir.glob("cwa.db.before-migration-*"))


@pytest.mark.unit
def test_migration_5_drops_the_unused_duplicate_columns(cwa_dir):
    db_file = str(cwa_dir / "cwa.db")
    con = sqlite3.connect(db_file)
    # Both tables as an older cwa.db had them
    con.execute("CREATE TABLE cwa_duplicate_cache (id INTEGER PRIMARY KEY CHECK (id = 1), "
                "scan_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, duplicate_groups_json TEXT, "
                "total_count INTEGER DEFAULT 0, scan_pending INTEGER DEFAULT 1, "
                "last_scanned_book_id INTEGER DEFAULT 0, scan_duration_seconds REAL DEFAULT 0, "
                "scan_method_used TEXT DEFAULT 'python')")
    con.execute("INSERT INTO cwa_duplicate_cache (id, total_count) VALUES (1, 7)")
    con.execute("CREATE TABLE cwa_duplicate_resolutions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, group_hash TEXT NOT NULL, group_title TEXT, "
                "group_author TEXT, kept_book_id INTEGER NOT NULL, deleted_book_ids TEXT NOT NULL, "
                "strategy TEXT NOT NULL, trigger_type TEXT NOT NULL, backed_up INTEGER DEFAULT 1, "
                "user_id INTEGER, notes TEXT)")
    con.execute("INSERT INTO cwa_duplicate_resolutions (group_hash, kept_book_id, deleted_book_ids, strategy, "
                "trigger_type) VALUES ('abc', 1, '[2]', 'newest', 'manual')")
    con.commit()
    con.close()

    db = _reopen(cwa_dir)
    try:
        assert not {"scan_duration_seconds", "scan_method_used"} & set(_columns(db_file, "cwa_duplicate_cache"))
        assert "backed_up" not in _columns(db_file, "cwa_duplicate_resolutions")
        # Rows and kept columns survive
        assert db.cur.execute("SELECT total_count FROM cwa_duplicate_cache").fetchone()[0] == 7
        assert db.cur.execute("SELECT group_hash FROM cwa_duplicate_resolutions").fetchone()[0] == "abc"
    finally:
        db.close()


@pytest.mark.unit
def test_a_new_cwa_db_runs_every_migration(cwa_dir):
    # Migration 1 pins a Hardcover setting a new cwa.db no longer has
    db = CWA_DB()
    try:
        applied = {r[0] for r in db.cur.execute("SELECT version FROM cwa_schema_migrations")}
        assert applied == {version for version, _, _ in cwa_db_module.MIGRATIONS}
        assert db.cwa_settings["duplicate_detection_enabled"] is True
    finally:
        db.close()


@pytest.mark.unit
def test_a_new_cwa_db_is_not_copied_aside(cwa_dir):
    CWA_DB().close()
    assert not list(cwa_dir.glob("cwa.db.before-migration-*"))


@pytest.mark.unit
def test_migration_6_drops_the_import_merge_setting(cwa_dir):
    db_file = str(cwa_dir / "cwa.db")
    con = sqlite3.connect(db_file)
    con.execute("CREATE TABLE cwa_settings (default_settings SMALLINT DEFAULT 1 NOT NULL, "
                "auto_backup_imports SMALLINT DEFAULT 0 NOT NULL, "
                "auto_ingest_automerge TEXT DEFAULT 'new_record' NOT NULL)")
    con.execute("INSERT INTO cwa_settings (auto_ingest_automerge) VALUES ('overwrite')")
    con.commit()
    con.close()

    db = _reopen(cwa_dir)
    try:
        assert "auto_ingest_automerge" not in _columns(db_file, "cwa_settings")
        assert "auto_ingest_automerge" not in db.get_cwa_settings()
    finally:
        db.close()


@pytest.mark.unit
def test_migration_7_clears_every_books_tags_once_after_a_backup(cwa_dir, tmp_path, monkeypatch):
    import json
    library = tmp_path / "library"
    library.mkdir()
    lib = sqlite3.connect(library / "metadata.db")
    lib.execute("CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT)")
    lib.execute("CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INTEGER, tag INTEGER)")
    lib.executemany("INSERT INTO tags VALUES (?, ?)", [(1, "Mathematics"), (2, "Springer 2011")])
    lib.executemany("INSERT INTO books_tags_link (book, tag) VALUES (?, ?)", [(1, 1), (1, 2), (2, 1)])
    lib.commit()
    lib.close()
    dirs = tmp_path / "dirs.json"
    dirs.write_text(json.dumps({"calibre_library_dir": str(library)}))
    monkeypatch.setattr(cwa_db_module, "DIRS_FILE", str(dirs))
    # An existing install: its cwa.db already has its settings
    con = sqlite3.connect(cwa_dir / "cwa.db")
    con.execute("CREATE TABLE cwa_settings (default_settings SMALLINT DEFAULT 1 NOT NULL)")
    con.execute("INSERT INTO cwa_settings DEFAULT VALUES")
    con.commit()
    con.close()

    db = _reopen(cwa_dir)
    db.close()
    lib = sqlite3.connect(library / "metadata.db")
    try:
        assert lib.execute("SELECT COUNT(*) FROM books_tags_link").fetchone()[0] == 0
        assert lib.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
        # Tags added afterwards stay: it ran once
        lib.execute("INSERT INTO tags VALUES (3, 'To read')")
        lib.execute("INSERT INTO books_tags_link (book, tag) VALUES (1, 3)")
        lib.commit()
    finally:
        lib.close()
    backup = sqlite3.connect(cwa_dir / "metadata.db.before-tag-clear")
    try:
        assert backup.execute("SELECT COUNT(*) FROM books_tags_link").fetchone()[0] == 3
    finally:
        backup.close()
    _reopen(cwa_dir).close()
    lib = sqlite3.connect(library / "metadata.db")
    try:
        assert lib.execute("SELECT name FROM tags").fetchall() == [("To read",)]
    finally:
        lib.close()


@pytest.mark.unit
def test_migration_7_leaves_a_new_installs_library_alone(tmp_path, monkeypatch):
    import json
    library = tmp_path / "library"
    library.mkdir()
    lib = sqlite3.connect(library / "metadata.db")
    lib.execute("CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT)")
    lib.execute("CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INTEGER, tag INTEGER)")
    lib.execute("INSERT INTO tags VALUES (1, 'Mathematics')")
    lib.execute("INSERT INTO books_tags_link (book, tag) VALUES (1, 1)")
    lib.commit()
    lib.close()
    dirs = tmp_path / "dirs.json"
    dirs.write_text(json.dumps({"calibre_library_dir": str(library)}))
    monkeypatch.setattr(cwa_db_module, "DIRS_FILE", str(dirs))
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    monkeypatch.setenv("CWA_DB_PATH", str(fresh))
    _reopen(fresh).close()
    lib = sqlite3.connect(library / "metadata.db")
    try:
        assert lib.execute("SELECT COUNT(*) FROM books_tags_link").fetchone()[0] == 1
    finally:
        lib.close()
