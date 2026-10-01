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

Retention is grandfather-father-son: the newest `daily` snapshots, plus the
newest snapshot of each of the last `weekly` ISO weeks and `monthly` calendar
months (see select_retained). run_backup() proves each new snapshot restorable
(verify_snapshot) and marks it with a .verified file; the newest verified
snapshot is never pruned. If the new metadata.db holds fewer than 90% of the
books in the previous good snapshot it is marked .suspicious and nothing is
pruned, so an accidental mass deletion can't rotate the good copies away.

Kept free of Flask/cps imports so it can be used from the web app, from
scripts and from tests alike.
"""

import json
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

DEFAULT_KEEP_COUNT = 7
DEFAULT_KEEP_WEEKLY = 4
DEFAULT_KEEP_MONTHLY = 6
MAX_TIER_COUNT = 1000
# A new metadata.db with fewer books than this share of the previous good one is suspicious
SHRINK_RATIO = 0.9
VERIFIED_MARKER = ".verified"
SUSPICIOUS_MARKER = ".suspicious"
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


def normalize_tier_count(value, default: int) -> int:
    """Coerces a weekly/monthly retention setting to an int in 0..MAX_TIER_COUNT
    (0 turns the tier off), falling back to default."""
    if isinstance(value, bool):
        return default
    try:
        count = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return count if 0 <= count <= MAX_TIER_COUNT else default


@dataclass(frozen=True)
class Retention:
    """How many snapshots of each tier to keep (see select_retained)."""
    daily: int = DEFAULT_KEEP_COUNT
    weekly: int = DEFAULT_KEEP_WEEKLY
    monthly: int = DEFAULT_KEEP_MONTHLY


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


def select_retained(snapshots: list[str], daily: int, weekly: int = 0, monthly: int = 0) -> set[str]:
    """Grandfather-father-son selection over snapshot paths (oldest first).

    Keeps the newest `daily` snapshots, plus the newest snapshot in each of the
    `weekly` most recent ISO weeks and of the `monthly` most recent calendar
    months that have one. Tiers overlap: this week's newest snapshot is also a
    daily one. Snapshots whose name has no parseable timestamp only count as daily.
    """
    keep = set(snapshots[-daily:]) if daily > 0 else set()
    tiers = ((weekly, lambda t: tuple(t.isocalendar())[:2]), (monthly, lambda t: (t.year, t.month)))
    for count, bucket in tiers:
        if count <= 0:
            continue
        seen: set = set()
        for snap in reversed(snapshots):
            ts = snapshot_timestamp(snap)
            if ts is None:
                continue
            key = bucket(ts)
            if key in seen:
                continue
            if len(seen) >= count:
                break
            seen.add(key)
            keep.add(snap)
    return keep


def is_verified(snapshot_dir: str) -> bool:
    return os.path.isfile(os.path.join(snapshot_dir, VERIFIED_MARKER))


def suspicious_reason(snapshot_dir: str) -> str | None:
    """The reason a snapshot was marked suspicious, or None."""
    try:
        with open(os.path.join(snapshot_dir, SUSPICIOUS_MARKER), encoding="utf-8") as f:
            return f.read().strip() or "suspicious"
    except FileNotFoundError:
        return None
    except OSError:
        return "suspicious"


def mark_verified(snapshot_dir: str, tables: dict, now: datetime | None = None) -> None:
    with open(os.path.join(snapshot_dir, VERIFIED_MARKER), "w", encoding="utf-8") as f:
        json.dump({"verified_at": (now or datetime.now()).isoformat(timespec="seconds"), "tables": tables}, f)


def mark_suspicious(snapshot_dir: str, reason: str) -> None:
    with open(os.path.join(snapshot_dir, SUSPICIOUS_MARKER), "w", encoding="utf-8") as f:
        f.write(reason)


def accept_snapshot(snapshot_dir: str) -> bool:
    """Clears the suspicious mark (the admin confirmed the change was intended), so
    the snapshot becomes the baseline for the next shrink check and pruning resumes.
    Returns whether a mark was removed."""
    try:
        os.remove(os.path.join(snapshot_dir, SUSPICIOUS_MARKER))
        return True
    except FileNotFoundError:
        return False


def protected_snapshots(snapshots: list[str]) -> set[str]:
    """Snapshots rotation must never remove: the newest verified one, and the newest
    verified one that isn't suspicious (they differ while a shrink is unresolved)."""
    protected = set()
    for snap in reversed(snapshots):
        if is_verified(snap):
            protected.add(snap)
            break
    for snap in reversed(snapshots):
        if is_verified(snap) and suspicious_reason(snap) is None:
            protected.add(snap)
            break
    return protected


def rotate_snapshots(backup_root: str, keep: int, weekly: int = 0, monthly: int = 0) -> list[str]:
    """Prunes snapshots per database by grandfather-father-son retention (see
    select_retained; with weekly=monthly=0 that is simply the newest `keep`).

    Counting per database (not per directory) means a database whose recent
    backups failed keeps its last successful copies, however old, instead of
    losing them to newer snapshots that don't contain it. The newest verified
    snapshot is never touched. A snapshot directory is removed once no database
    file is left in it; empty directories are kept while they are among the
    newest `keep`. Returns removed paths (database files and directories).
    """
    keep = normalize_keep_count(keep)
    snapshots = list_snapshots(backup_root)
    protected = protected_snapshots(snapshots)
    removed = []

    by_db: dict[str, list[str]] = {}
    for snap in snapshots:
        for name in _snapshot_db_files(snap):
            by_db.setdefault(name, []).append(snap)
    for name, snaps in by_db.items():
        retained = select_retained(snaps, keep, weekly, monthly) | protected
        for snap in snaps:
            if snap in retained:
                continue
            path = os.path.join(snap, name)
            try:
                os.remove(path)
                removed.append(path)
            except OSError:
                pass

    newest = set(snapshots[-keep:]) | protected
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
    {name, path, timestamp, pre_restore, verified, suspicious (reason or None),
    databases: {file: size_bytes}, size}."""
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
            "verified": is_verified(snap),
            "suspicious": suspicious_reason(snap),
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


def verify_snapshot(snapshot_dir: str) -> dict:
    """Proves a snapshot is restorable by restoring each database into a scratch directory.

    Runs the real restore path (integrity check, sqlite backup into a fresh file, integrity
    check again) and requires the restored database to contain at least one table.
    Returns {file_name: table_count}; raises ValueError/FileNotFoundError on the first failure.
    """
    import tempfile
    files = _snapshot_db_files(snapshot_dir)
    if not files:
        raise FileNotFoundError(f"No databases in snapshot: {snapshot_dir}")
    result = {}
    with tempfile.TemporaryDirectory(prefix="lily-verify-") as scratch:
        for name in files:
            path = os.path.join(snapshot_dir, name)
            target = os.path.join(scratch, name)
            restore_sqlite_db(path, target)
            con = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
            try:
                tables = con.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            finally:
                con.close()
            if not tables:
                raise ValueError(f"Restored {name} contains no tables")
            result[name] = tables
    return result


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


def _take_snapshot(sources: dict, backup_root: str, now: datetime | None) -> tuple[str, dict, dict]:
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
    if not done:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
    return snapshot_dir, done, errors


def backup_databases(sources: dict, backup_root: str, keep: int = DEFAULT_KEEP_COUNT,
                     now: datetime | None = None, weekly: int = 0, monthly: int = 0) -> tuple[str, dict, dict]:
    """Snapshots each database in `sources` ({file_name: src_path}) into
    backup_root/<YYYYmmdd_HHMMSS>/<file_name>, then rotates old snapshots.
    No verification or shrink check; the nightly task uses run_backup().

    Missing/None sources are skipped. Returns (snapshot_dir, done, errors) where
    done maps file_name -> dest path and errors maps file_name -> message.
    Rotation only runs when at least one database was backed up, so repeated
    failures never delete the last good snapshots.
    """
    snapshot_dir, done, errors = _take_snapshot(sources, backup_root, now)
    if done:
        rotate_snapshots(backup_root, keep, weekly, monthly)
    return snapshot_dir, done, errors


def count_books(db_path: str) -> int | None:
    """Number of rows in a Calibre metadata.db's books table, or None if unreadable."""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        return int(con.execute("SELECT count(*) FROM books").fetchone()[0])
    except sqlite3.Error:
        return None
    finally:
        con.close()


def check_shrink(snapshot_dir: str, previous: list[str], ratio: float = SHRINK_RATIO) -> str | None:
    """Compares the book count of snapshot_dir/metadata.db with the newest earlier
    snapshot (from `previous`, oldest first) that isn't suspicious and has a readable
    metadata.db. Returns a warning if it dropped below `ratio` of that, else None."""
    new = count_books(os.path.join(snapshot_dir, "metadata.db"))
    if new is None:
        return None
    for snap in reversed(previous):
        if snap == snapshot_dir or suspicious_reason(snap) is not None:
            continue
        path = os.path.join(snap, "metadata.db")
        if not os.path.isfile(path):
            continue
        old = count_books(path)
        if old is None:
            continue
        if new < old * ratio:
            return (f"metadata.db has {new} books, down from {old} in snapshot {os.path.basename(snap)} "
                    f"(more than {round((1 - ratio) * 100)}% fewer)")
        return None
    return None


@dataclass
class BackupRun:
    """Outcome of run_backup()."""
    snapshot_dir: str
    done: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    verified: bool = False
    suspicious: str | None = None
    removed: list = field(default_factory=list)


def run_backup(sources: dict, backup_root: str, retention: Retention = Retention(),
               now: datetime | None = None) -> BackupRun:
    """The nightly backup: snapshot, verify, shrink check, then prune.

    The snapshot is restored into a scratch directory (verify_snapshot) and marked
    .verified when that works; a failed verification is reported in errors["verify"].
    A metadata.db with too few books (check_shrink) is marked .suspicious. Pruning
    only runs when the new snapshot verified and isn't suspicious, so neither a
    broken backup nor a library that lost most of its books can rotate the good
    snapshots away."""
    previous = list_snapshots(backup_root)
    snapshot_dir, done, errors = _take_snapshot(sources, backup_root, now)
    run = BackupRun(snapshot_dir, done, errors)
    if not done:
        return run
    try:
        mark_verified(snapshot_dir, verify_snapshot(snapshot_dir), now)
        run.verified = True
    except Exception as e:
        errors["verify"] = str(e)
    if "metadata.db" in done:
        run.suspicious = check_shrink(snapshot_dir, previous)
        if run.suspicious:
            mark_suspicious(snapshot_dir, run.suspicious)
    if run.verified and not run.suspicious:
        run.removed = rotate_snapshots(backup_root, retention.daily, retention.weekly, retention.monthly)
    return run


if __name__ == "__main__":
    # Usage: python db_backup.py <backup_root> [snapshot_name]  -- verify latest (or named) snapshot restores
    import sys
    root = sys.argv[1]
    snaps = list_snapshots(root)
    if not snaps:
        sys.exit(f"No snapshots in {root}")
    snap = resolve_snapshot(root, sys.argv[2] if len(sys.argv) > 2 else os.path.basename(snaps[-1]))
    print(snap, verify_snapshot(snap))
