# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""SQL statement counts for a page of books (cps/db.py Books relationships + fill_indexpage).

Guards the loading strategy: relationships are fetched with one ``WHERE book IN (...)`` query
each (no N+1, and no re-running of the filtered/paginated Books query per relationship, which
``lazy='subquery'`` did), and card-only pages skip what cards never render.
"""

import base64
import re
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import event

from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


@contextmanager
def _capture_sql():
    from cps import db
    statements = []

    def _record(conn, cursor, statement, params, context, executemany):
        statements.append(" ".join(statement.split()))

    engine = db.CalibreDB.engine
    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _record)


def _library(tmp_path, n_books):
    tmp_path.mkdir(parents=True, exist_ok=True)
    env_cm = lily_env(tmp_path)
    env = env_cm.__enter__()
    for i in range(n_books):
        env.add_book(f"Book {i}", author=f"Author {i}", tags=("shared", f"tag {i}"), lang="eng",
                     timestamp=datetime(2026, 1, 1 + i, tzinfo=timezone.utc))
    return env_cm, env


def _opds_new_statements(tmp_path, n_books):
    env_cm, env = _library(tmp_path, n_books)
    try:
        headers = {"Authorization": "Basic " + base64.b64encode(
            f"{env.admin().name}:{ADMIN_PASSWORD}".encode()).decode()}
        with _capture_sql() as statements:
            resp = env.app.test_client().get("/opds/new", headers=headers)
        assert resp.status_code == 200
        assert resp.data.count(b"<entry>") == n_books
        return statements
    finally:
        env_cm.__exit__(None, None, None)


def _relationship_loads(statements):
    """Statements that load a Books relationship (everything but the count and the page query)."""
    return [s for s in statements
            if not s.startswith("SELECT count(") and not re.match(r"SELECT books\.id AS books_id\b", s)]


def test_opds_page_query_count_does_not_grow_with_books(tmp_path):
    small = _opds_new_statements(tmp_path / "small", 3)
    large = _opds_new_statements(tmp_path / "large", 12)
    assert len(small) == len(large), "per-book queries (N+1) on the OPDS acquisition feed"


def test_relationships_load_by_primary_key_not_by_rerunning_the_page_query(tmp_path):
    statements = _opds_new_statements(tmp_path, 5)
    loads = _relationship_loads(statements)
    # one query per Books relationship (authors, tags, data, identifiers), each keyed on the
    # page's book ids; descriptions (comments) are gone, so neither loaded nor read
    assert len(loads) == 4, loads
    for statement in loads:
        assert " IN (" in statement, statement
        assert "FROM (SELECT" not in statement, f"relationship load re-runs the page query: {statement}"
    assert not any("FROM comments" in s for s in statements)


def test_cards_only_page_skips_relationships_cards_do_not_render(tmp_path):
    from cps.cw_login import login_user
    from cps import calibre_db, db

    env_cm, env = _library(tmp_path, 4)
    try:
        with env.app.test_request_context("/"):
            login_user(env.admin())
            with _capture_sql() as statements:
                entries, __ = calibre_db.fill_indexpage(1, 0, db.Books, True, [db.Books.timestamp.desc()],
                                                            True, 0, cards_only=True)
                # what image.html's book_card reads must already be loaded
                for entry in entries:
                    assert entry.Books.authors and entry.Books.data
            loaded = " ".join(statements)
        assert len(entries) == 4
        for table in ("comments", "tags", "identifiers", "publishers", "languages", "ratings"):
            assert f"FROM {table}" not in loaded and f"JOIN {table} " not in loaded, table
        # count + page query + authors/data
        assert len(_relationship_loads(statements)) <= 4, statements
    finally:
        env_cm.__exit__(None, None, None)
