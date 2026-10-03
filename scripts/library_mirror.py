# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""One-way, copy-only mirror of the Calibre library's book files and covers.

Nightly database snapshots (db_backup.py) cover metadata.db but not the books
themselves. This copies every file under the library to a second folder, keeping
relative paths, and only transfers what is new or changed (different size, or
newer modification time). It never deletes anything from the mirror, so a book
removed from the library by mistake is still there; and it never touches the
library. metadata.db is skipped because the snapshots handle it consistently.

Kept free of Flask/cps imports so it can be used from the web app and from tests.
"""

import os
import shutil
from collections.abc import Callable

# The live database is snapshotted properly by db_backup.py; copying it file-by-file
# can capture a torn WAL-mode database.
SKIP_NAMES = frozenset({"metadata.db", "metadata.db-wal", "metadata.db-shm", "metadata.db-journal"})
SKIP_SUFFIXES = (".partial",)
# Headroom left on the destination volume after the copy
FREE_SPACE_MARGIN = 256 * 1024 * 1024
_MTIME_SLACK = 2.0  # seconds; some filesystems store coarse timestamps


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


def _copy_atomic(source: str, target: str) -> None:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    partial = target + ".partial"
    try:
        shutil.copy2(source, partial)
        os.replace(partial, target)
    finally:
        if os.path.exists(partial):
            os.remove(partial)


def mirror_library(src: str, dest: str, progress: Callable[[int, int], None] | None = None) -> dict:
    """Copies new and changed library files into dest. Returns
    {'copied': n, 'bytes': n, 'errors': {relative path: message}}.

    Raises MirrorError up front for an unsafe destination, a missing library, or not
    enough free space (nothing is copied in that case)."""
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
    copied, copied_bytes, errors = 0, 0, {}
    for i, (source, target, size) in enumerate(plan, 1):
        try:
            _copy_atomic(source, target)
            copied += 1
            copied_bytes += size
        except OSError as e:
            errors[os.path.relpath(source, src)] = str(e)
        if progress:
            progress(i, len(plan))
    return {"copied": copied, "bytes": copied_bytes, "errors": errors}
