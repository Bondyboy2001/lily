# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""cwa.db activity logs: retention pruning, and history views that read only the rows they
show."""

import pytest

import cwa_db as cwa_db_module

pytestmark = pytest.mark.unit


def _seed(db, days_ago, n=1):
    for _ in range(n):
        ts = f"datetime('now', '-{days_ago} days')"
        db.cur.execute(f"INSERT INTO cwa_user_activity (user_id, event_type, timestamp) VALUES (1, 'SEARCH', {ts})")
        db.cur.execute("INSERT INTO cwa_enforcement (timestamp, book_id, book_title, author, file_path, trigger_type) "
                       f"VALUES ({ts}, 1, 't', 'a', 'p', 'auto')")
        db.cur.execute(f"INSERT INTO cwa_import (timestamp, filename, original_backed_up) VALUES ({ts}, 'f', 'no')")
    db.con.commit()


def _counts(db):
    return {t: db.cur.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in cwa_db_module.STATS_RETENTION_TABLES}


def test_prune_removes_only_rows_past_retention(temp_cwa_db, monkeypatch):
    monkeypatch.setattr(cwa_db_module, "STATS_PRUNE_BATCH", 2)  # exercise several batches
    _seed(temp_cwa_db, 400, n=5)
    _seed(temp_cwa_db, 10, n=3)
    deleted = temp_cwa_db.prune_old_stats()
    assert deleted == {t: 5 for t in cwa_db_module.STATS_RETENTION_TABLES}
    assert _counts(temp_cwa_db) == {t: 3 for t in cwa_db_module.STATS_RETENTION_TABLES}


def test_retention_comes_from_the_environment_and_zero_keeps_everything(temp_cwa_db, monkeypatch):
    _seed(temp_cwa_db, 400, n=2)
    _seed(temp_cwa_db, 40)
    monkeypatch.setenv("LILY_STATS_RETENTION_DAYS", "0")
    assert temp_cwa_db.prune_old_stats() == {}
    monkeypatch.setenv("LILY_STATS_RETENTION_DAYS", "30")
    temp_cwa_db.prune_old_stats()
    assert _counts(temp_cwa_db) == {t: 0 for t in cwa_db_module.STATS_RETENTION_TABLES}
    monkeypatch.setenv("LILY_STATS_RETENTION_DAYS", "junk")
    assert cwa_db_module.stats_retention_days() == cwa_db_module.STATS_RETENTION_DAYS


def test_history_views_return_the_newest_ten_oldest_first(temp_cwa_db):
    for i in range(15):
        temp_cwa_db.cur.execute(
            "INSERT INTO cwa_enforcement (timestamp, book_id, book_title, author, file_path, trigger_type) "
            "VALUES (?, ?, 't', 'a', ?, 'auto')", (f"2026-01-{i + 1:02d} 00:00:00", i, f"p{i}"))
        temp_cwa_db.cur.execute("INSERT INTO cwa_import (timestamp, filename, original_backed_up) VALUES (?, ?, 'no')",
                                (f"2026-01-{i + 1:02d} 00:00:00", f"f{i}"))
    temp_cwa_db.con.commit()

    short = temp_cwa_db.enforce_show(paths=False, verbose=False, web_ui=True)
    assert [row[1] for row in short] == list(range(5, 15))
    assert [row[2] for row in temp_cwa_db.enforce_show(paths=True, verbose=False, web_ui=True)][-1] == "p14"
    assert [row[1] for row in temp_cwa_db.enforce_show(paths=False, verbose=True, web_ui=True)] == list(range(15))
    assert [row[1] for row in temp_cwa_db.get_import_history(verbose=False)] == [f"f{i}" for i in range(5, 15)]
    assert len(temp_cwa_db.get_import_history(verbose=True)) == 15
