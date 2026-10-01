# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""setup_db adds Lily's sort indexes (books.timestamp, books.pubdate) to a writable
metadata.db, so "newest" and "published" pages don't sort the whole books table."""

import sqlite3

import pytest

from tests.unit.lily_env import lily_env

pytestmark = pytest.mark.unit


def _indexes(path):
    con = sqlite3.connect(path)
    try:
        return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        con.close()


def test_sort_indexes_are_created_and_used(tmp_path):
    with lily_env(tmp_path) as env:
        from cps import calibre_db
        from sqlalchemy import text
        assert {"lily_books_timestamp_idx", "lily_books_pubdate_idx"} <= _indexes(env.library_dir / "metadata.db")
        plan = calibre_db.session.execute(text(
            "EXPLAIN QUERY PLAN SELECT id FROM books ORDER BY timestamp DESC LIMIT 60")).fetchall()
        assert "lily_books_timestamp_idx" in " ".join(str(row) for row in plan)


def test_setup_is_repeatable(tmp_path):
    with lily_env(tmp_path) as env:
        from cps import db
        db.CalibreDB._ensure_lily_indexes()  # IF NOT EXISTS: a second run is a no-op
        assert "lily_books_pubdate_idx" in _indexes(env.library_dir / "metadata.db")
