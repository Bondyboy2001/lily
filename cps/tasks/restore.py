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

# Background services that write metadata.db / cwa.db, and their flock(2) lock files.
SERVICE_LOCKS = (
    ("ingest processor", "ingest_processor.lock"),
    ("cover enforcer", "cover_enforcer.lock"),
)


def _acquire_service_lock(lock_path):
    """Takes a background service's flock so it can't run while the library changes.

    Returns the open handle. Raises if the service holds it. Opened with 'a+'
    (never 'w') so the holder's PID is not truncated, and the file is never
    unlinked (same contract as ProcessLock in scripts/ingest_processor.py and
    acquire_lock in scripts/cover_enforcer.py). A service that was killed holds
    nothing: the kernel drops its flock.
    """
    handle = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("lock held by another process: %s" % lock_path)
    try:
        # We hold the lock, so replacing the diagnostic PID is safe
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
    except OSError:
        pass
    return handle


def release_service_locks(handles) -> None:
    for handle in handles:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            handle.close()
        except Exception:
            pass


def acquire_service_locks():
    """Pauses the ingest processor and cover enforcer. Returns the handles to
    release; raises RuntimeError naming the busy service (nothing left held)."""
    handles = []
    for service_name, lock_name in SERVICE_LOCKS:
        try:
            handles.append(_acquire_service_lock(os.path.join(tempfile.gettempdir(), lock_name)))
        except Exception as e:
            release_service_locks(handles)
            raise RuntimeError("the %s is currently running (%s). Wait for it to finish and try again."
                               % (service_name, e))
    return handles
