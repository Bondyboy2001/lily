# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Pausing the background services that write metadata.db / cwa.db.

Book deletes and merges (cps/book_recovery.py) take these locks so the ingest
processor and cover enforcer cannot touch the library while files move.
"""

import fcntl
import os
import tempfile

# Background services that write metadata.db / cwa.db. (lock file, existence-style?)
SERVICE_LOCKS = (
    ("ingest processor", "ingest_processor.lock", False),
    ("cover enforcer", "cover_enforcer.lock", True),
)


def _acquire_service_lock(lock_path, existence_lock=False):
    """Takes a background service's lock so it can't run during a restore.

    Returns (handle, path_to_remove_on_release). Raises if the service holds it.
    Opened with 'a+' (never 'w') so the holder's PID is not truncated, and the
    file is never unlinked while a flock-based service may be using it (same
    contract as ProcessLock in scripts/ingest_processor.py).

    existence_lock: the service (cover_enforcer.py) treats the file's mere
    existence as "running" (open(..., 'x')) rather than using flock. An existing
    empty file therefore means it is running; if we create the file ourselves we
    remove it again on release.
    """
    existed = True
    if existence_lock:
        # Create atomically (like the service's open(..., 'x')) so there is no window
        # between an existence check and the open in which the service can create it.
        try:
            os.close(os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
            existed = False
        except FileExistsError:
            pass
    handle = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("lock held by another process: %s" % lock_path)
    if existence_lock and existed:
        handle.seek(0)
        content = handle.read().strip()
        if not content.isdigit():
            # Legacy 'x'-style lock (empty file) present: the service is running,
            # or crashed and left it behind (delete the file manually in that case).
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
            raise RuntimeError("lock file present: %s" % lock_path)
    try:
        # We hold the lock, so replacing the diagnostic PID is safe
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
    except OSError:
        pass
    return handle, (lock_path if existence_lock and not existed else None)


def release_service_locks(handles) -> None:
    for handle, remove_path in handles:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            handle.close()
        except Exception:
            pass
        if remove_path:
            # We created this existence-style lock ourselves; leaving it
            # behind would block the cover enforcer forever.
            try:
                os.remove(remove_path)
            except OSError:
                pass


def acquire_service_locks():
    """Pauses the ingest processor and cover enforcer. Returns the handles to
    release; raises RuntimeError naming the busy service (nothing left held)."""
    handles = []
    for service_name, lock_name, existence_lock in SERVICE_LOCKS:
        try:
            handles.append(_acquire_service_lock(os.path.join(tempfile.gettempdir(), lock_name), existence_lock))
        except Exception as e:
            release_service_locks(handles)
            raise RuntimeError("the %s is currently running (%s). Wait for it to finish and try again."
                               % (service_name, e))
    return handles
