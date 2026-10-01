# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""One-way, copy-only mirror of the Calibre library's book files and covers.

Nightly database snapshots (db_backup.py) cover metadata.db but not the books
themselves. This copies every file under the library to a second folder, keeping
relative paths, and only transfers what is new or changed (different size, or
newer modification time). It never deletes a current file from the mirror, so a
book removed from the library by mistake is still there; and it never touches the
library. metadata.db is skipped because the snapshots handle it consistently.

A changed file doesn't simply overwrite the mirror copy: the old copy is first
moved to <mirror>/.versions/<YYYY-MM-DD>/<relative path>, and version folders
older than `version_days` are pruned. A replacement that looks like damage (an
epub/kepub/cbz that fails zip validation, or a file that lost more than half its
size) is refused and reported as suspicious, so a corrupted library file can't
replace the good copy.

Kept free of Flask/cps imports so it can be used from the web app and from tests.
"""

import os
import re
import shutil
import zipfile
from datetime import date, timedelta
from typing import Callable

# The live database is snapshotted properly by db_backup.py; copying it file-by-file
# can capture a torn WAL-mode database.
SKIP_NAMES = frozenset({"metadata.db", "metadata.db-wal", "metadata.db-shm", "metadata.db-journal"})
SKIP_SUFFIXES = (".partial",)
# Headroom left on the destination volume after the copy
FREE_SPACE_MARGIN = 256 * 1024 * 1024
_MTIME_SLACK = 2.0  # seconds; some filesystems store coarse timestamps
VERSIONS_DIR = ".versions"
DEFAULT_VERSION_DAYS = 30
_VERSION_DIR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(_\d+)?$")
# Zip-based formats whose replacement must pass zipfile validation
ZIP_SUFFIXES = (".epub", ".kepub", ".cbz")
# A replacement smaller than this share of the mirror copy is refused
SHRINK_LIMIT = 0.5


class MirrorError(Exception):
    pass


def _inside(child: str, parent: str) -> bool:
    child, parent = os.path.realpath(child), os.path.realpath(parent)
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def validate_destination(src: str, dest: str) -> None:
    """Refuses relative paths and any folder that contains, or sits inside, the library."""
    if not dest or not os.path.isabs(dest):
        raise MirrorError("The mirror folder must be an absolute path.")
    if _inside(dest, src) or _inside(src, dest):
        raise MirrorError("The mirror folder must not contain or sit inside the library folder.")


def _needs_copy(src_path: str, dest_path: str) -> bool:
    try:
        d = os.stat(dest_path)
    except FileNotFoundError:
        return True
    s = os.stat(src_path)
    return s.st_size != d.st_size or s.st_mtime > d.st_mtime + _MTIME_SLACK


def plan_mirror(src: str, dest: str) -> list[tuple[str, str, int]]:
    """[(source path, destination path, size)] for every file that needs copying."""
    plan = []
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]  # .caltrash, .DS_Store dirs, ...
        for name in filenames:
            if name in SKIP_NAMES or name.endswith(SKIP_SUFFIXES):
                continue
            path = os.path.join(dirpath, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            target = os.path.join(dest, os.path.relpath(path, src))
            try:
                if _needs_copy(path, target):
                    plan.append((path, target, os.path.getsize(path)))
            except OSError:
                continue
    return plan


def normalize_version_days(value, default: int = DEFAULT_VERSION_DAYS) -> int:
    """Days as a non-negative int (0 = keep versions forever); invalid values fall back to default."""
    if isinstance(value, bool):
        return default
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return days if days >= 0 else default


def _zip_problem(path: str) -> str | None:
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()  # reads every member and checks its CRC
    except Exception as e:  # BadZipFile, truncated data (EOFError, zlib.error), ...
        return "not a valid zip file (%s)" % (e or type(e).__name__)
    return "corrupt zip member %s" % bad if bad else None


def suspicious_replacement(source: str, target: str) -> str | None:
    """Why `source` must not replace the existing mirror copy `target`, or None.

    A file that lost more than half its size, or a zip-based book (epub, kepub, cbz)
    that fails zip validation, looks like damage rather than an edit. New files
    (no mirror copy yet) are never refused: some copy beats none."""
    try:
        old = os.path.getsize(target)
    except OSError:
        return None
    new = os.path.getsize(source)
    if old > 0 and new < old * SHRINK_LIMIT:
        return "shrank from %d to %d bytes" % (old, new)
    if source.lower().endswith(ZIP_SUFFIXES):
        return _zip_problem(source)
    return None


def _version_path(dest: str, rel: str, day: str) -> str:
    """<dest>/.versions/<day>/<rel>; a second version of the same file on the same
    day goes to <day>_1, <day>_2, ... so no earlier version is overwritten."""
    path = os.path.join(dest, VERSIONS_DIR, day, rel)
    n = 1
    while os.path.lexists(path):
        path = os.path.join(dest, VERSIONS_DIR, "%s_%d" % (day, n), rel)
        n += 1
    return path


def _copy_atomic(source: str, target: str, before_replace: Callable[[], None] | None = None) -> None:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    partial = target + ".partial"
    try:
        shutil.copy2(source, partial)
        if before_replace:
            before_replace()
        os.replace(partial, target)
    finally:
        if os.path.exists(partial):
            os.remove(partial)


def prune_versions(dest: str, days: int, today: date | None = None) -> list[str]:
    """Removes <dest>/.versions/<YYYY-MM-DD>[_n] folders dated more than `days` days
    before today. days <= 0 keeps every version. Returns the removed folders."""
    root = os.path.join(dest, VERSIONS_DIR)
    if days <= 0 or not os.path.isdir(root):
        return []
    cutoff = (today or date.today()) - timedelta(days=days)
    removed = []
    for name in sorted(os.listdir(root)):
        match = _VERSION_DIR_RE.match(name)
        path = os.path.join(root, name)
        if not match or os.path.islink(path) or not os.path.isdir(path):
            continue
        try:
            day = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if day < cutoff:
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path)
    return removed


def mirror_library(src: str, dest: str, progress: Callable[[int, int], None] | None = None,
                   version_days: int = DEFAULT_VERSION_DAYS, today: date | None = None) -> dict:
    """Copies new and changed library files into dest. Returns
    {'copied': n, 'bytes': n, 'versioned': n, 'errors': {relative path: message},
     'suspicious': {relative path: reason}, 'pruned_versions': [folders]}.

    Replaced mirror copies are kept under .versions/<today>/ (see module docstring);
    suspicious replacements are skipped and listed. Raises MirrorError up front for
    an unsafe destination, a missing library, or not enough free space (nothing is
    copied in that case)."""
    if not os.path.isdir(src):
        raise MirrorError("Library folder not found: %s" % src)
    validate_destination(src, dest)
    os.makedirs(dest, exist_ok=True)
    plan = plan_mirror(src, dest)
    needed = sum(size for _, _, size in plan)
    free = shutil.disk_usage(dest).free
    if needed + FREE_SPACE_MARGIN > free:
        raise MirrorError("Not enough free space in %s: %d MB to copy, %d MB free."
                          % (dest, needed // 2**20, free // 2**20))
    day = (today or date.today()).isoformat()
    copied, copied_bytes, versioned = 0, 0, 0
    errors: dict[str, str] = {}
    suspicious: dict[str, str] = {}
    for i, (source, target, size) in enumerate(plan, 1):
        rel = os.path.relpath(source, src)
        try:
            reason = suspicious_replacement(source, target)
            if reason:
                suspicious[rel] = reason
            else:
                previous = os.path.isfile(target)

                def keep_previous(target=target, rel=rel):
                    version = _version_path(dest, rel, day)
                    os.makedirs(os.path.dirname(version), exist_ok=True)
                    os.replace(target, version)

                _copy_atomic(source, target, keep_previous if previous else None)
                copied += 1
                copied_bytes += size
                versioned += previous
        except OSError as e:
            errors[rel] = str(e)
        if progress:
            progress(i, len(plan))
    pruned = prune_versions(dest, version_days, today)
    return {"copied": copied, "bytes": copied_bytes, "versioned": versioned, "errors": errors,
            "suspicious": suspicious, "pruned_versions": pruned}
