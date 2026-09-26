# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Consistent SQLite snapshots of Lily's databases.

Uses sqlite3.Connection.backup(), which copies a transactionally consistent
image of a live database including pages still in the -wal file. A plain file
copy of a WAL-mode database can silently miss committed data or be torn.

Kept free of Flask/cps imports so it can be used from the web app, from
scripts and from tests alike.
"""

import os
import re
import shutil
import sqlite3
from datetime import datetime

DEFAULT_KEEP_COUNT = 7
BACKUP_SUBDIR = os.path.join("backup", "db")
# Directory names are timestamps, so lexical order == chronological order
_SNAPSHOT_DIR_RE = re.compile(r"^\d{8}_\d{6}(_\d+)?$")


def sqlite_backup(src_path: str, dest_path: str, timeout: float = 30) -> None:
    """Writes a consistent copy of the sqlite database at src_path to dest_path.

    The source is opened read-only so a missing file is an error rather than
    silently creating an empty database. dest_path is written via a temp file
    and renamed, so a failed backup never leaves a half-written file behind.
    """
    if not os.path.isfile(src_path):
        raise FileNotFoundError(f"Database not found: {src_path}")
    tmp_path = dest_path + ".partial"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    src = None
    try:
        src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True, timeout=timeout)
        src.execute("SELECT 1 FROM sqlite_master LIMIT 1")
    except sqlite3.OperationalError:
        if src is not None:
            src.close()
        # Read-only opens of WAL databases can fail when the -shm file can't be
        # created read-only; the file is known to exist, so a normal open is safe.
        src = sqlite3.connect(src_path, timeout=timeout)
    try:
        dst = sqlite3.connect(tmp_path)
        try:
            src.backup(dst)
            # Store the copy as a self-contained rollback-journal db (no -wal sidecar)
            dst.execute("PRAGMA journal_mode=DELETE")
        finally:
            dst.close()
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    finally:
        src.close()
    os.replace(tmp_path, dest_path)


def normalize_keep_count(value, default: int = DEFAULT_KEEP_COUNT) -> int:
    """Coerces a keep-count setting to a positive int, falling back to default."""
    try:
        keep = int(value)
    except (TypeError, ValueError):
        return default
    return keep if keep >= 1 else default


def list_snapshots(backup_root: str) -> list[str]:
    """Returns snapshot directory paths under backup_root, oldest first."""
    if not os.path.isdir(backup_root):
        return []
    names = sorted(n for n in os.listdir(backup_root)
                   if _SNAPSHOT_DIR_RE.match(n) and os.path.isdir(os.path.join(backup_root, n)))
    return [os.path.join(backup_root, n) for n in names]


def rotate_snapshots(backup_root: str, keep: int) -> list[str]:
    """Deletes all but the newest `keep` snapshot directories. Returns removed paths."""
    keep = normalize_keep_count(keep)
    snapshots = list_snapshots(backup_root)
    removed = []
    for path in snapshots[:-keep] if len(snapshots) > keep else []:
        shutil.rmtree(path, ignore_errors=True)
        removed.append(path)
    return removed


def _new_snapshot_dir(backup_root: str, now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(backup_root, stamp)
    suffix = 1
    while os.path.exists(path):
        path = os.path.join(backup_root, f"{stamp}_{suffix}")
        suffix += 1
    os.makedirs(path)
    return path


def backup_databases(sources: dict, backup_root: str, keep: int = DEFAULT_KEEP_COUNT,
                     now: datetime | None = None) -> tuple[str, dict, dict]:
    """Snapshots each database in `sources` ({file_name: src_path}) into
    backup_root/<YYYYmmdd_HHMMSS>/<file_name>, then rotates old snapshots.

    Missing/None sources are skipped. Returns (snapshot_dir, done, errors) where
    done maps file_name -> dest path and errors maps file_name -> message.
    Rotation only runs when at least one database was backed up, so repeated
    failures never delete the last good snapshots.
    """
    snapshot_dir = _new_snapshot_dir(backup_root, now)
    done, errors = {}, {}
    for name, src in sources.items():
        if not src:
            continue
        if not os.path.isfile(src):
            errors[name] = f"not found: {src}"
            continue
        dest = os.path.join(snapshot_dir, name)
        try:
            sqlite_backup(src, dest)
            done[name] = dest
        except Exception as e:
            errors[name] = str(e)
    if done:
        rotate_snapshots(backup_root, keep)
    else:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
    return snapshot_dir, done, errors
