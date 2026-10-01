# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Starting on an old app.db migrates it: empty_library/app.db carries an upstream
Calibre-Web schema, and ub.init_db (-> migrate_Database) plus config_sql.load_configuration
must bring it up to the current models so the app runs and people can sign in."""

import shutil
import sqlite3

import pytest

from tests.unit.lily_env import REPO, lily_env

pytestmark = pytest.mark.unit

TEMPLATE_APP_DB = REPO / "empty_library" / "app.db"


def _columns(con, table):
    return {row[1] for row in con.execute(f'PRAGMA table_info("{table}")')}


def _tables(con):
    return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    from cps import constants
    # One-time migration markers go next to the config; keep them in tmp_path.
    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))
    app_db = tmp_path / "app.db"
    shutil.copy(TEMPLATE_APP_DB, app_db)
    with sqlite3.connect(app_db) as con:
        old_user_columns = _columns(con, "user")
        old_tables = _tables(con)
    # lily_env runs ub.init_db on the existing file (the migration path) and load_configuration
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        yield env, app_db, old_user_columns, old_tables


def test_template_is_an_old_schema(migrated):
    _env, _app_db, old_user_columns, old_tables = migrated
    # If these ever exist in the template, the test below no longer exercises the migration
    assert "force_password_change" not in old_user_columns
    assert "metadata_suggestion" not in old_tables


def test_every_model_table_and_column_exists_after_migration(migrated):
    env, app_db, _old_columns, _old_tables = migrated
    from cps import ub, config_sql
    with sqlite3.connect(app_db) as con:
        tables = _tables(con)
        missing = []
        for table in list(ub.Base.metadata.sorted_tables) + [config_sql._Settings.__table__]:
            if table.name not in tables:
                missing.append(table.name)
                continue
            have = _columns(con, table.name)
            missing += [f"{table.name}.{c.name}" for c in table.columns if c.name not in have]
    assert not missing, missing


def test_new_user_columns_tables_and_indexes(migrated):
    _env, app_db, old_user_columns, old_tables = migrated
    with sqlite3.connect(app_db) as con:
        user_columns = _columns(con, "user")
        for column in ("hardcover_token", "opds_only_shelves_sync", "theme", "force_password_change"):
            assert column in user_columns, column
        assert {"random", "expiry"} <= _columns(con, "user_session")
        for table in ("archived_book", "thumbnail", "opds_shelf_exposure", "web_reader_progress",
                      "reader_position", "metadata_suggestion"):
            assert table in _tables(con), table
        indexes = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"ix_book_read_link_user_book", "ix_user_session_random_session_key"} <= indexes
    # Old columns are kept, not dropped
    assert old_user_columns <= user_columns


def test_existing_users_survive_and_a_new_user_can_sign_in(migrated):
    env, _app_db, _old_columns, _old_tables = migrated
    ub = env.ub
    names = {u.name for u in ub.session.query(ub.User)}
    assert env.admin().name in names

    env.add_user("reader", password="Read-er-123!")
    client = env.app.test_client()
    resp = client.post("/login", data={"username": "reader", "password": "Read-er-123!"})
    assert resp.status_code == 302
    assert "/login" not in resp.headers["Location"]

    bad = env.app.test_client().post("/login", data={"username": "reader", "password": "wrong"})
    assert bad.status_code != 302 or "/login" in bad.headers.get("Location", "")


def test_migration_is_idempotent(migrated, tmp_path):
    env, app_db, _old_columns, _old_tables = migrated
    from cps import ub
    with sqlite3.connect(app_db) as con:
        before = {t: _columns(con, t) for t in _tables(con)}
    ub.migrate_Database(ub.session)
    with sqlite3.connect(app_db) as con:
        after = {t: _columns(con, t) for t in _tables(con)}
    assert before == after
