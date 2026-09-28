# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Consistent SQLite snapshots of Lily's databases.

Uses sqlite3.Connection.backup(), which copies a transactionally consistent
image of a live database including pages still in the -wal file. A plain file
copy of a WAL-mode database can silently miss committed data or be torn.

Restores go the other way through the same API (restore_sqlite_db), after a
safety copy of the live database and a PRAGMA integrity_check of the snapshot.

Backups should live on a different volume from /config (set db_backup_dir in
cwa_settings or the DB_BACKUP_DIR env var): the default /config/backup/db/ is
lost together with the databases if the /config volume is.

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
PRE_RESTORE_SUFFIX = "_pre-restore"
_ANY_SNAPSHOT_DIR_RE = re.compile(r"^\d{8}_\d{6}(_\d+)?(_pre-restore)?$")


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


def list_snapshots(backup_root: str, include_pre_restore: bool = False) -> list[str]:
    """Returns snapshot directory paths under backup_root, oldest first.

    Safety copies taken before a restore (<stamp>_pre-restore) are only included
    when include_pre_restore is set; they are never rotated automatically.
    """
    if not os.path.isdir(backup_root):
        return []
    pattern = _ANY_SNAPSHOT_DIR_RE if include_pre_restore else _SNAPSHOT_DIR_RE
    names = sorted(n for n in os.listdir(backup_root)
                   if pattern.match(n) and os.path.isdir(os.path.join(backup_root, n)))
    return [os.path.join(backup_root, n) for n in names]


def _snapshot_db_files(snapshot_dir: str) -> list[str]:
    try:
        return sorted(n for n in os.listdir(snapshot_dir)
                      if n.endswith(".db") and os.path.isfile(os.path.join(snapshot_dir, n)))
    except OSError:
        return []


def rotate_snapshots(backup_root: str, keep: int) -> list[str]:
    """Prunes snapshots per database, keeping the newest `keep` copies of each.

    Counting per database (not per directory) means a database whose recent
    backups failed keeps its last `keep` successful copies, however old, instead
    of losing them to newer snapshots that don't contain it. A snapshot
    directory is removed once no database file is left in it; empty directories
    are kept while they are among the newest `keep`. Returns removed paths
    (database files and directories).
    """
    keep = normalize_keep_count(keep)
    snapshots = list_snapshots(backup_root)
    removed = []

    by_db: dict[str, list[str]] = {}
    for snap in snapshots:
        for name in _snapshot_db_files(snap):
            by_db.setdefault(name, []).append(snap)
    for name, snaps in by_db.items():
        for snap in snaps[:-keep] if len(snaps) > keep else []:
            path = os.path.join(snap, name)
            try:
                os.remove(path)
                removed.append(path)
            except OSError:
                pass

    newest = set(snapshots[-keep:])
    for snap in snapshots:
        if snap not in newest and not _snapshot_db_files(snap):
            shutil.rmtree(snap, ignore_errors=True)
            removed.append(snap)
    return removed


def snapshot_timestamp(snapshot_dir: str) -> datetime | None:
    """Parses the YYYYmmdd_HHMMSS prefix of a snapshot directory name."""
    try:
        return datetime.strptime(os.path.basename(snapshot_dir)[:15], "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def describe_snapshots(backup_root: str) -> list[dict]:
    """Snapshots newest first, as dicts for display:
    {name, path, timestamp, pre_restore, databases: {file: size_bytes}, size}."""
    result = []
    for snap in reversed(list_snapshots(backup_root, include_pre_restore=True)):
        dbs = {}
        for name in _snapshot_db_files(snap):
            try:
                dbs[name] = os.path.getsize(os.path.join(snap, name))
            except OSError:
                continue
        name = os.path.basename(snap)
        result.append({
            "name": name,
            "path": snap,
            "timestamp": snapshot_timestamp(snap),
            "pre_restore": name.endswith(PRE_RESTORE_SUFFIX),
            "databases": dbs,
            "size": sum(dbs.values()),
        })
    return result


def resolve_snapshot(backup_root: str, name: str) -> str:
    """Returns the path of snapshot `name` under backup_root; refuses anything
    that isn't a snapshot directory name (no path traversal)."""
    if not name or not _ANY_SNAPSHOT_DIR_RE.fullmatch(name):
        raise ValueError(f"Not a snapshot name: {name!r}")
    path = os.path.join(backup_root, name)
    if not os.path.isdir(path):
        raise FileNotFoundError(f"Snapshot not found: {name}")
    return path


def check_integrity(db_path: str) -> None:
    """Raises ValueError unless `PRAGMA integrity_check` on db_path returns ok."""
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = [r[0] for r in con.execute("PRAGMA integrity_check").fetchall()]
    finally:
        con.close()
    if rows != ["ok"]:
        raise ValueError(f"Integrity check failed for {db_path}: {'; '.join(map(str, rows[:5]))}")


def restore_sqlite_db(snapshot_path: str, live_path: str, timeout: float = 60) -> None:
    """Replaces the contents of the live database with the snapshot, atomically.

    Uses the sqlite backup API with the live file as destination instead of
    renaming a file over it: every page is written in one write transaction
    under SQLite's own locking, so other connections (the web app, the ingest
    processor) either see the old database or the restored one, never a torn
    mix, and a stale -wal file can't be replayed onto a swapped-in file. The
    live database's journal mode (WAL) is kept.
    """
    check_integrity(snapshot_path)
    src = sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(live_path, timeout=timeout)
        try:
            dst.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
            journal_mode = dst.execute("PRAGMA journal_mode").fetchone()[0]
            src.backup(dst)
            if str(journal_mode).lower() == "wal":
                dst.execute("PRAGMA journal_mode=WAL")
        finally:
            dst.close()
    finally:
        src.close()
    check_integrity(live_path)


def create_pre_restore_snapshot(sources: dict, backup_root: str, now: datetime | None = None) -> tuple[str, dict]:
    """Safety copy of the live databases about to be overwritten by a restore.

    Stored as <stamp>_pre-restore under backup_root (listed, never rotated).
    Raises if any database fails to copy, since restoring without a safety copy
    is not allowed. Returns (snapshot_dir, {file_name: dest})."""
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(backup_root, stamp + PRE_RESTORE_SUFFIX)
    suffix = 1
    while os.path.exists(path):
        path = os.path.join(backup_root, f"{stamp}_{suffix}{PRE_RESTORE_SUFFIX}")
        suffix += 1
    os.makedirs(path)
    done = {}
    try:
        for name, src in sources.items():
            if src and os.path.isfile(src):
                dest = os.path.join(path, name)
                sqlite_backup(src, dest)
                done[name] = dest
    except Exception:
        shutil.rmtree(path, ignore_errors=True)
        raise
    return path, done


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
