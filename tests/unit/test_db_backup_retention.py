# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Grandfather-father-son retention, verification marks and the shrink check."""

import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import db_backup as mod  # noqa: E402

pytestmark = pytest.mark.unit


def _library(path, books):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS books (id INTEGER PRIMARY KEY, title TEXT)")
    con.execute("DELETE FROM books")
    con.executemany("INSERT INTO books (title) VALUES (?)", [(f"b{i}",) for i in range(books)])
    con.commit()
    con.close()


def _snap(root, when, books=10, verified=True):
    d = root / when.strftime("%Y%m%d_%H%M%S")
    d.mkdir(parents=True)
    _library(str(d / "metadata.db"), books)
    if verified:
        mod.mark_verified(str(d), {"metadata.db": 1})
    return d


def _names(paths):
    return sorted(os.path.basename(p) for p in paths)


def test_select_retained_daily_weekly_monthly():
    start = datetime(2026, 1, 1, 3)
    snaps = [(start + timedelta(days=d)).strftime("/r/%Y%m%d_%H%M%S") for d in range(120)]
    kept = mod.select_retained(snaps, daily=7, weekly=4, monthly=6)
    names = _names(kept)
    # 7 newest days
    assert all(n in names for n in [os.path.basename(s) for s in snaps[-7:]])
    # Newest of each of the last 4 ISO weeks (2026-04-30 is a Thursday, week 18);
    # week 17's Sunday (04-26) is also a daily one
    for day in ("20260426", "20260419", "20260412"):  # Sundays closing weeks 17, 16, 15
        assert f"{day}_030000" in names
    assert "20260405_030000" not in names  # week 14: fifth week back
    # Newest of each month: Jan..Apr (only 4 months of history)
    for day in ("20260131", "20260228", "20260331"):
        assert f"{day}_030000" in names
    assert len(names) == 7 + 2 + 3


def test_select_retained_counts_only_daily_when_tiers_off():
    snaps = [f"/r/2026010{i}_030000" for i in range(1, 10)]
    assert mod.select_retained(snaps, 3) == set(snaps[-3:])
    assert mod.select_retained(snaps, 0, 0, 0) == set()


def test_select_retained_ignores_unparseable_names_for_weekly_and_monthly():
    snaps = ["/r/20261399_030000", "/r/20260101_030000"]
    assert mod.select_retained(snaps, 0, weekly=2, monthly=2) == {"/r/20260101_030000"}


def test_monthly_tier_keeps_month_ends_beyond_daily(tmp_path):
    start = datetime(2026, 1, 1, 3)
    for d in range(100):
        _snap(tmp_path, start + timedelta(days=d), verified=False)
    mod.rotate_snapshots(str(tmp_path), 7, weekly=0, monthly=6)
    names = _names(mod.list_snapshots(str(tmp_path)))
    assert names[:3] == ["20260131_030000", "20260228_030000", "20260331_030000"]
    assert len(names) == 3 + 7


@pytest.mark.parametrize("value,expected", [(4, 4), ("6", 6), (0, 0), ("0", 0), (-1, 4), (None, 4),
                                            ("x", 4), (True, 4), (5000, 4)])
def test_normalize_tier_count(value, expected):
    assert mod.normalize_tier_count(value, 4) == expected


def test_rotation_never_deletes_newest_verified_snapshot(tmp_path):
    start = datetime(2026, 1, 1, 3)
    good = _snap(tmp_path, start, verified=True)
    for d in range(1, 6):
        _snap(tmp_path, start + timedelta(days=d), verified=False)
    mod.rotate_snapshots(str(tmp_path), 2)
    names = _names(mod.list_snapshots(str(tmp_path)))
    assert names == [good.name, "20260105_030000", "20260106_030000"]
    assert (good / "metadata.db").is_file()


def test_rotation_also_protects_newest_verified_non_suspicious(tmp_path):
    start = datetime(2026, 1, 1, 3)
    good = _snap(tmp_path, start)
    for d in range(1, 4):
        s = _snap(tmp_path, start + timedelta(days=d))
        mod.mark_suspicious(str(s), "shrunk")
    mod.rotate_snapshots(str(tmp_path), 1)
    names = _names(mod.list_snapshots(str(tmp_path)))
    assert names == [good.name, "20260104_030000"]


def test_check_shrink_against_previous_good_snapshot(tmp_path):
    start = datetime(2026, 1, 1, 3)
    a = _snap(tmp_path, start, books=100)
    b = _snap(tmp_path, start + timedelta(days=1), books=50)
    mod.mark_suspicious(str(b), "x")
    c = _snap(tmp_path, start + timedelta(days=2), books=85)
    previous = [str(a), str(b)]
    reason = mod.check_shrink(str(c), previous)
    assert reason and "85" in reason and "100" in reason and a.name in reason
    assert mod.check_shrink(str(c), previous, ratio=0.8) is None
    # No baseline (first snapshot, or no readable metadata.db): nothing to compare
    assert mod.check_shrink(str(c), []) is None
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "metadata.db").write_bytes(b"junk")
    assert mod.check_shrink(str(tmp_path / "x"), previous) is None


def test_count_books_unreadable(tmp_path):
    assert mod.count_books(str(tmp_path / "missing.db")) is None
    con = sqlite3.connect(tmp_path / "other.db")
    con.execute("CREATE TABLE t (x)")
    con.close()
    assert mod.count_books(str(tmp_path / "other.db")) is None


@pytest.fixture
def live(tmp_path):
    src = tmp_path / "live"
    src.mkdir()
    _library(str(src / "metadata.db"), 100)
    app = src / "app.db"
    con = sqlite3.connect(app)
    con.execute("CREATE TABLE user (id)")
    con.close()
    return {"metadata.db": str(src / "metadata.db"), "app.db": str(app)}, tmp_path / "backups"


def test_run_backup_verifies_marks_and_prunes(live):
    sources, root = live
    start = datetime(2026, 1, 1, 3)
    runs = [mod.run_backup(sources, str(root), mod.Retention(2, 0, 0), now=start + timedelta(days=d))
            for d in range(4)]
    assert all(r.verified and not r.errors and r.suspicious is None for r in runs)
    assert os.path.isfile(os.path.join(runs[-1].snapshot_dir, mod.VERIFIED_MARKER))
    assert _names(mod.list_snapshots(str(root))) == ["20260103_030000", "20260104_030000"]
    assert runs[-1].removed  # day 2 pruned by the last run
    info = mod.describe_snapshots(str(root))[0]
    assert info["verified"] is True and info["suspicious"] is None


def test_run_backup_marks_shrunk_library_suspicious_and_does_not_prune(live):
    sources, root = live
    start = datetime(2026, 1, 1, 3)
    for d in range(3):
        mod.run_backup(sources, str(root), mod.Retention(2, 0, 0), now=start + timedelta(days=d))
    _library(sources["metadata.db"], 40)  # 60% of the books vanish
    for d in range(3, 6):
        run = mod.run_backup(sources, str(root), mod.Retention(2, 0, 0), now=start + timedelta(days=d))
        assert run.verified and run.suspicious and "40" in run.suspicious
        assert run.removed == []
    names = _names(mod.list_snapshots(str(root)))
    assert names == ["20260102_030000", "20260103_030000", "20260104_030000",
                     "20260105_030000", "20260106_030000"]
    assert mod.describe_snapshots(str(root))[0]["suspicious"]

    # The admin confirms the deletion was intended: the next run compares with that
    # snapshot, is not suspicious, and pruning resumes
    newest = mod.list_snapshots(str(root))[-1]
    assert mod.accept_snapshot(newest) is True
    assert mod.accept_snapshot(newest) is False
    run = mod.run_backup(sources, str(root), mod.Retention(2, 0, 0), now=start + timedelta(days=6))
    assert run.suspicious is None and run.removed
    assert _names(mod.list_snapshots(str(root))) == ["20260106_030000", "20260107_030000"]


def test_run_backup_verification_failure_skips_pruning(live, monkeypatch):
    sources, root = live
    start = datetime(2026, 1, 1, 3)
    for d in range(3):
        mod.run_backup(sources, str(root), mod.Retention(1, 0, 0), now=start + timedelta(days=d))
    assert len(mod.list_snapshots(str(root))) == 1

    def broken(snapshot_dir):
        raise ValueError("restore check failed")

    monkeypatch.setattr(mod, "verify_snapshot", broken)
    run = mod.run_backup(sources, str(root), mod.Retention(1, 0, 0), now=start + timedelta(days=3))
    assert run.verified is False and run.errors["verify"] == "restore check failed"
    assert run.removed == []
    assert not os.path.exists(os.path.join(run.snapshot_dir, mod.VERIFIED_MARKER))
    assert len(mod.list_snapshots(str(root))) == 2


def test_run_backup_total_failure(tmp_path):
    run = mod.run_backup({"app.db": str(tmp_path / "missing.db")}, str(tmp_path / "b"))
    assert run.done == {} and "app.db" in run.errors and not os.path.exists(run.snapshot_dir)


def test_retention_from_settings():
    from cps.tasks.db_backup import retention_from_settings
    assert retention_from_settings({}) == mod.Retention(7, 4, 6)
    assert retention_from_settings({"db_backup_keep_count": 3, "db_backup_keep_weekly": "0",
                                    "db_backup_keep_monthly": "12"}) == mod.Retention(3, 0, 12)
    assert retention_from_settings({"db_backup_keep_weekly": "lots"}).weekly == 4


def test_backup_task_reports_suspicious_backup_as_failure(live, monkeypatch):
    from cps.services.worker import STAT_FAIL, STAT_FINISH_SUCCESS
    from cps.tasks import db_backup as task_mod
    sources, root = live
    monkeypatch.setattr(task_mod, "get_backup_root", lambda: str(root))
    monkeypatch.setattr(task_mod, "get_backup_sources", lambda: sources)
    monkeypatch.setattr(task_mod, "get_retention", lambda: mod.Retention(2, 0, 0))
    ok = task_mod.TaskBackupDatabases()
    ok.start(None)
    assert ok.stat == STAT_FINISH_SUCCESS, ok.error
    _library(sources["metadata.db"], 10)
    bad = task_mod.TaskBackupDatabases()
    bad.start(None)
    assert bad.stat == STAT_FAIL and "Suspicious" in bad.error and "10 books" in bad.error


def test_suspicious_reason_unreadable_marker(tmp_path):
    (tmp_path / mod.SUSPICIOUS_MARKER).mkdir()  # a directory can't be read as a file
    assert mod.suspicious_reason(str(tmp_path)) == "suspicious"
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / mod.SUSPICIOUS_MARKER).write_text("")
    assert mod.suspicious_reason(str(tmp_path / "s")) == "suspicious"
