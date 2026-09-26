# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""CWA_DB construction cost: schema setup/migrations run once per process per db file."""

import sqlite3
import sys
from pathlib import Path

import pytest

scripts_dir = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))

import cwa_db as cwa_db_module
from cwa_db import CWA_DB, CWADBConnectionError


@pytest.fixture
def cwa_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path))
    monkeypatch.delenv("NETWORK_SHARE_MODE", raising=False)
    return tmp_path


@pytest.mark.unit
def test_migrations_run_once_per_process(cwa_dir, monkeypatch):
    calls = []
    original = CWA_DB.run_schema_setup

    def counting(self):
        calls.append(1)
        return original(self)

    monkeypatch.setattr(CWA_DB, "run_schema_setup", counting)
    first = CWA_DB()
    second = CWA_DB()
    try:
        assert len(calls) == 1
        # Second instance is fully usable and sees the same settings
        assert second.cwa_settings == first.cwa_settings
        assert second.tables and second.schema and second.cwa_default_settings
    finally:
        first.close()
        second.close()


@pytest.mark.unit
def test_migrations_rerun_when_db_file_replaced(cwa_dir, monkeypatch):
    calls = []
    original = CWA_DB.run_schema_setup
    monkeypatch.setattr(CWA_DB, "run_schema_setup", lambda self: (calls.append(1), original(self)))
    CWA_DB().close()
    (cwa_dir / "cwa.db").unlink()
    for sidecar in ("cwa.db-wal", "cwa.db-shm"):
        (cwa_dir / sidecar).unlink(missing_ok=True)
    db = CWA_DB()
    try:
        assert len(calls) == 2
        db.cur.execute("SELECT COUNT(*) FROM cwa_settings")
        assert db.cur.fetchone()[0] == 1
    finally:
        db.close()


@pytest.mark.unit
def test_invalidate_schema_cache_forces_rerun(cwa_dir, monkeypatch):
    calls = []
    original = CWA_DB.run_schema_setup
    monkeypatch.setattr(CWA_DB, "run_schema_setup", lambda self: (calls.append(1), original(self)))
    CWA_DB().close()
    cwa_db_module.invalidate_schema_cache(str(cwa_dir / "cwa.db"))
    CWA_DB().close()
    assert len(calls) == 2


@pytest.mark.unit
def test_wal_enabled_by_default(cwa_dir):
    with CWA_DB() as db:
        assert db.cur.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert db.cur.execute("PRAGMA busy_timeout").fetchone()[0] == 30000


@pytest.mark.unit
def test_wal_skipped_in_network_share_mode(cwa_dir, monkeypatch):
    monkeypatch.setenv("NETWORK_SHARE_MODE", "true")
    with CWA_DB() as db:
        assert db.cur.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal"


@pytest.mark.unit
def test_context_manager_closes_connection(cwa_dir):
    with CWA_DB() as db:
        con = db.con
    with pytest.raises(sqlite3.ProgrammingError):
        con.execute("SELECT 1")
    db.close()  # idempotent


@pytest.mark.unit
def test_connect_failure_raises_instead_of_exiting(tmp_path, monkeypatch):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    monkeypatch.setenv("CWA_DB_PATH", str(blocker))
    with pytest.raises(CWADBConnectionError):
        CWA_DB()
    # Existing `except sqlite3.Error` handlers keep catching it
    assert issubclass(CWADBConnectionError, sqlite3.Error)


@pytest.mark.unit
def test_backup_keep_count_setting_is_integer(cwa_dir):
    with CWA_DB() as db:
        assert db.cwa_settings["db_backup_keep_count"] == 7
        assert not isinstance(db.cwa_settings["db_backup_keep_count"], bool)
