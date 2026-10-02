"""app.db loses the tables and columns of removed features once (ub.migrate_drop_removed_schema),
with a copy kept beside it, and every user, shelf and read status survives."""
import sqlite3

import pytest
from sqlalchemy import create_engine

pytestmark = pytest.mark.unit


def _old_app_db(path):
    """An app.db shaped like one from before the cleanup."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE user (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, name VARCHAR(64), email VARCHAR(120),
            role SMALLINT, password VARCHAR, kindle_mail VARCHAR(120), locale VARCHAR(2),
            sidebar_view INTEGER, default_language VARCHAR(3), denied_tags VARCHAR,
            allowed_tags VARCHAR, denied_column_value VARCHAR, allowed_column_value VARCHAR,
            view_settings JSON, hardcover_token VARCHAR, theme INTEGER,
            force_password_change BOOLEAN DEFAULT 0, totp_secret VARCHAR,
            UNIQUE (name), UNIQUE (email), UNIQUE (hardcover_token));
        INSERT INTO user (id, name, email, role, password, kindle_mail, locale, sidebar_view,
                          view_settings, hardcover_token, theme, totp_secret)
            VALUES (1, 'harry', 'h@example.org', 1, 'hash', 'k@kindle.com', 'de', 7, '{}', 'tok', 1, 'secret'),
                   (5, 'reader', 'r@example.org', 0, 'hash2', NULL, 'en', 3, '{}', NULL, 0, NULL);
        CREATE TABLE shelf (id INTEGER NOT NULL PRIMARY KEY, uuid VARCHAR, name VARCHAR,
            is_public INTEGER, user_id INTEGER, kobo_sync BOOLEAN, created DATETIME,
            last_modified DATETIME, FOREIGN KEY(user_id) REFERENCES user (id));
        INSERT INTO shelf (id, name, user_id, kobo_sync) VALUES (3, 'Papers', 1, 1);
        CREATE TABLE book_read_link (id INTEGER NOT NULL PRIMARY KEY, book_id INTEGER,
            user_id INTEGER, read_status INTEGER NOT NULL, last_modified DATETIME,
            last_time_started_reading DATETIME, times_started_reading INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES user (id));
        CREATE INDEX ix_book_read_link_user_book ON book_read_link (user_id, book_id);
        INSERT INTO book_read_link (book_id, user_id, read_status, times_started_reading)
            VALUES (42, 1, 2, 3);
        CREATE TABLE settings (id INTEGER NOT NULL PRIMARY KEY, mail_server VARCHAR,
            config_ldap_dn VARCHAR, config_calibre_dir VARCHAR);
        INSERT INTO settings (id, mail_server, config_ldap_dn, config_calibre_dir)
            VALUES (1, 'smtp', 'dn', '/calibre-library');
        CREATE TABLE kobo_reading_state (id INTEGER PRIMARY KEY, user_id INTEGER);
        INSERT INTO kobo_reading_state (user_id) VALUES (1);
    """)
    con.commit()
    con.close()


def _columns(con, table):
    return {row[1] for row in con.execute('PRAGMA table_info("{}")'.format(table))}


def test_removed_tables_and_columns_are_dropped_and_rows_kept(tmp_path):
    from cps import ub
    path = tmp_path / "app.db"
    _old_app_db(path)

    ub.migrate_drop_removed_schema(create_engine("sqlite:///{}".format(path)))

    con = sqlite3.connect(path)
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "kobo_reading_state" not in tables
        assert not _columns(con, "user") & {"kindle_mail", "locale", "hardcover_token", "theme", "totp_secret"}
        assert "kobo_sync" not in _columns(con, "shelf")
        assert not _columns(con, "book_read_link") & {"last_time_started_reading", "times_started_reading"}
        assert _columns(con, "settings") == {"id", "config_calibre_dir"}
        # The user table was rebuilt (its UNIQUE hardcover_token can't be dropped in place)
        assert con.execute("SELECT id, name, email, role, sidebar_view FROM user ORDER BY id").fetchall() == [
            (1, "harry", "h@example.org", 1, 7), (5, "reader", "r@example.org", 0, 3)]
        user_sql = con.execute("SELECT sql FROM sqlite_master WHERE name='user'").fetchone()[0]
        assert "UNIQUE (name)" in user_sql and "AUTOINCREMENT" in user_sql
        assert con.execute("SELECT name, user_id FROM shelf").fetchall() == [("Papers", 1)]
        assert con.execute("SELECT book_id, user_id, read_status FROM book_read_link").fetchall() == [(42, 1, 2)]
        assert con.execute("SELECT name FROM sqlite_master WHERE name='ix_book_read_link_user_book'").fetchone()
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()

    # The app.db from before is kept beside it, untouched
    backup = sqlite3.connect(str(path) + ub.APP_DB_SCHEMA_BACKUP_SUFFIX)
    try:
        assert backup.execute("SELECT hardcover_token FROM user WHERE id=1").fetchone() == ("tok",)
    finally:
        backup.close()


def test_a_clean_app_db_is_left_alone(tmp_path):
    from cps import ub
    path = tmp_path / "app.db"
    engine = create_engine("sqlite:///{}".format(path))
    ub.Base.metadata.create_all(engine)
    ub.migrate_drop_removed_schema(engine)
    assert not (tmp_path / ("app.db" + ub.APP_DB_SCHEMA_BACKUP_SUFFIX)).exists()
