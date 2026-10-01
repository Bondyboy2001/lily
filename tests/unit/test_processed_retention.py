# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Failed imports must not be pruned early just because they kept an old mtime."""

import os
import time
from datetime import datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
DAY = 86400


def _stamp(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y%m%d_%H%M%S")


def _file(path: Path, mtime: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    os.utime(path, (mtime, mtime))
    return path


def test_prune_uses_failed_name_prefix_and_mtime(tmp_path):
    from cps.tasks.processed_cleanup import prune_processed_books
    now = time.time()
    failed = tmp_path / "failed"
    # Failed 5 days ago, but the file kept a years-old mtime: must be kept
    recent_old_mtime = _file(failed / f"{_stamp(now - 5 * DAY)}_Book.epub", now - 2000 * DAY)
    # Failed 40 days ago: pruned
    old = _file(failed / f"{_stamp(now - 40 * DAY)}_safety_timeout_Old.epub", now - 40 * DAY)
    # No prefix: mtime decides
    plain_old = _file(failed / "plain.epub", now - 40 * DAY)
    # Garbage prefix: mtime decides
    bad_prefix = _file(failed / "20261399_996199_x.epub", now - 1 * DAY)
    # An imported backup whose own name happens to start with an old date is kept by its new mtime
    imported = _file(tmp_path / "imported" / "20200101_120000_scan.pdf", now - 1 * DAY)

    removed = prune_processed_books(str(tmp_path), 30, now=now)

    assert sorted(removed) == sorted([str(old), str(plain_old)])
    assert recent_old_mtime.exists() and bad_prefix.exists() and imported.exists()


def test_failed_import_gets_a_fresh_mtime(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    import ingest_processor
    failed_dir = tmp_path / "failed"
    monkeypatch.setattr(ingest_processor, "backup_destinations", {"failed": str(failed_dir)})
    src = _file(tmp_path / "ingest" / "book.epub", 1_000_000_000)
    nbp = object.__new__(ingest_processor.NewBookProcessor)
    nbp.filepath, nbp.filename = str(src), src.name

    assert nbp.move_to_failed()

    # The book plus its hidden .failure.json sidecar (ingest_failures.write_failure)
    (moved,) = [p for p in failed_dir.iterdir() if not p.name.endswith(".failure.json")]
    assert abs(moved.stat().st_mtime - time.time()) < 60


def test_auto_zip_leaves_failed_dir_alone(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    import auto_zip

    class FakeDB:
        cwa_settings = {"auto_zip_backups": True}

    monkeypatch.setattr(auto_zip, "CWA_DB", FakeDB)
    monkeypatch.setattr(auto_zip.AutoZipper, "get_books_to_zip", lambda self: {})
    zipper = auto_zip.AutoZipper()
    assert zipper.archive_dirs == [zipper.imported_dir]
    assert not any("failed" in d for d in zipper.archive_dirs)
