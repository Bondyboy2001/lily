# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Database restores as background tasks.

Restores used to run inside the web request: `calibredb restore_database` can
take up to 20 minutes, and blocking subprocess calls under gevent (without
monkey-patching) froze every other request and failed /health. They now run on
the WorkerThread and the request returns immediately.
"""

import fcntl
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
from datetime import datetime

from flask_babel import lazy_gettext as N_

from cps import config, logger, ub, calibre_db
from cps.embed_helper import get_calibre_binarypath
from cps.services.worker import CalibreTask

# One restore at a time (library or snapshot); checked by the routes before queueing
# and held by the task while it runs.
_RESTORE_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
_restore_pending = threading.Event()

# Background services that write metadata.db / cwa.db. (lock file, existence-style?)
SERVICE_LOCKS = (
    ("ingest processor", "ingest_processor.lock", False),
    ("cover enforcer", "cover_enforcer.lock", True),
)

# app.db tables that reference Calibre book ids through a `book_id` column. Tables
# left over from removed features (Kobo) are cleaned too when they still exist.
#
# `calibredb restore_database` keeps every book's id: it reads it from the
# "Title (id)" folder name and recreates the row with force_id. So after a rebuild
# these rows still point at the right books, and only the rows of books that did not
# come back (no folder or no metadata.opf) are removed. The same rule is applied after
# a snapshot restore, where ids of books added after the snapshot will be handed out
# again to new imports and must not inherit someone's shelves or reading progress.
BOOK_LINKED_APP_TABLES = (
    "book_shelf_link", "book_read_link", "bookmark", "web_reader_progress", "archived_book",
    "downloads", "hardcover_match_queue", "metadata_suggestion", "hardcover_book_blacklist",
    "kobo_synced_books", "kobo_reading_state", "kobo_statistics", "kobo_annotation_sync",
)


def restore_in_progress() -> bool:
    """True while a restore is queued or running."""
    return _restore_pending.is_set() or _RESTORE_LOCK.locked()


def mark_restore_queued() -> bool:
    """Reserves the single restore slot. Returns False if one is already queued/running."""
    with _STATE_LOCK:
        if _restore_pending.is_set() or _RESTORE_LOCK.locked():
            return False
        _restore_pending.set()
        return True


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


class RestoreTask(CalibreTask):
    """Holds the restore slot and pauses the background services while running."""

    def __init__(self, task_message):
        super(RestoreTask, self).__init__(task_message)
        self.log = logger.create()

    def run(self, worker_thread):
        if not _RESTORE_LOCK.acquire(blocking=False):
            self._handleError("Another restore is already running")
            return
        _restore_pending.clear()
        handles = []
        try:
            try:
                handles = acquire_service_locks()
            except RuntimeError as e:
                self.log.error("Restore aborted: %s", e)
                self._handleError("Restore aborted: %s" % e)
                return
            self.do_restore()
        finally:
            release_service_locks(handles)
            _RESTORE_LOCK.release()

    def do_restore(self):
        raise NotImplementedError

    @property
    def is_cancellable(self):
        return False


def remove_orphan_book_rows(app_db_path, metadata_db_path):
    """Deletes rows of BOOK_LINKED_APP_TABLES whose book_id is not a book in metadata.db,
    in one transaction. Returns {table: rows removed}. Does nothing when metadata.db has
    no books (an empty rebuild must not take everyone's shelves and progress with it)."""
    con = sqlite3.connect(app_db_path, timeout=30)
    try:
        con.execute("ATTACH DATABASE ? AS lib", (metadata_db_path,))
        try:
            if not con.execute("SELECT COUNT(*) FROM lib.books").fetchone()[0]:
                return {}
        except sqlite3.DatabaseError:
            return {}
        existing = {r[0] for r in con.execute("SELECT name FROM main.sqlite_master WHERE type='table'")}
        removed = {}
        with con:
            for table in BOOK_LINKED_APP_TABLES:
                if table not in existing:
                    continue
                columns = {r[1] for r in con.execute('PRAGMA main.table_info("%s")' % table)}
                if "book_id" not in columns:
                    continue
                cur = con.execute('DELETE FROM main."%s" WHERE book_id NOT IN (SELECT id FROM lib.books)' % table)
                if cur.rowcount:
                    removed[table] = cur.rowcount
        return removed
    finally:
        con.close()


def reconcile_after_restore(restored, app_db_path, metadata_db_path, books_dir, config_dir, source):
    """After a snapshot restore: drops app.db rows of books the library no longer has and
    lists book folders the restored metadata.db doesn't reference (the Trash page shows
    them and can import them again). books_dir None means the configured library's.
    Returns a short note for the task message, or ''. Never raises."""
    log = logger.create()
    if books_dir is None:
        try:
            books_dir = config.get_book_path()
        except Exception:
            books_dir = getattr(config, "config_calibre_dir", None)
    if ("metadata.db" in restored or "app.db" in restored) and metadata_db_path and os.path.exists(app_db_path):
        try:
            removed = remove_orphan_book_rows(app_db_path, metadata_db_path)
            if removed:
                log.info("Removed app.db rows of books not in the restored library: %s", removed)
        except Exception as e:
            log.error("Could not clean up app.db rows after the restore: %s", e)
    if "metadata.db" not in restored or not metadata_db_path:
        return ""
    try:
        from cps.library_orphans import record_after_restore
        folders = record_after_restore(books_dir, metadata_db_path, config_dir, source)
    except Exception as e:
        log.error("Could not look for book folders missing from the restored library: %s", e)
        return ""
    if not folders:
        return ""
    log.warning("%d book folder(s) are not in the restored metadata.db: %s", len(folders), ", ".join(folders))
    return N_("%(n)d book folder(s) are not in the restored library; import them again from the Trash page",
              n=len(folders))


class TaskRestoreCalibreLibrary(RestoreTask):
    """Last-resort rebuild of metadata.db from the library's OPF files via
    `calibredb restore_database`, then drops app.db rows of books that did not come back."""

    def __init__(self, task_message=N_('Restoring Calibre library database')):
        super(TaskRestoreCalibreLibrary, self).__init__(task_message)

    @property
    def name(self):
        return "Restore Calibre Library"

    def _run_logged(self, cmd, label, timeout, log_path):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            with open(log_path, "a", encoding="utf-8") as log_file:
                log_file.write("\n[%s] timed out after %ss\n" % (label, timeout))
            raise RuntimeError("%s timed out after %s seconds" % (label, timeout)) from e
        self.log.info("calibredb %s output: %s\n%s", label, result.stdout, result.stderr)
        if result.returncode != 0:
            self.log.warning("calibredb %s returned code %s", label, result.returncode)
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write("\n[%s]\n" % label)
            log_file.write(result.stdout or "")
            log_file.write(result.stderr or "")
        return result

    def do_restore(self):
        library_dir = config.config_calibre_dir
        if not library_dir:
            self._handleError("Calibre library path is not configured")
            return
        metadata_path = os.path.join(library_dir, "metadata.db")
        app_db_path = ub.app_DB_path or "/config/app.db"
        for path in (metadata_path, app_db_path):
            if not os.path.exists(path):
                self._handleError("Database not found: %s" % path)
                return

        # 1. Safety copies (sqlite backup API: consistent even with pending -wal pages)
        # CWA_DB_PATH only differs from /config in tests (same as scripts/cwa_db.py)
        backup_dir = os.path.join(os.environ.get("CWA_DB_PATH", "/config"), "backup",
                                  "restore_%s" % datetime.now().strftime('%Y%m%d_%H%M%S'))
        os.makedirs(backup_dir, exist_ok=True)
        if '/app/calibre-web-automated/scripts/' not in sys.path:
            sys.path.insert(1, '/app/calibre-web-automated/scripts/')
        from db_backup import sqlite_backup
        sqlite_backup(metadata_path, os.path.join(backup_dir, "metadata.db.bak"))
        sqlite_backup(app_db_path, os.path.join(backup_dir, "app.db.bak"))
        self.progress = 0.1

        log_path = os.path.join(backup_dir, "restore.log")
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write("Restore started at %s\n" % datetime.now().isoformat())

        # Close active sessions to reduce lock contention
        try:
            calibre_db.dispose()
        except Exception as e:
            self.log.warning("Failed to dispose sessions before restore: %s", e)

        calibredb_binary = get_calibre_binarypath("calibredb") or "/app/calibre/calibredb"
        check_cmd = [calibredb_binary, "check_library", "--with-library", library_dir]
        restore_cmd = [calibredb_binary, "restore_database", "--with-library", library_dir, "--really-do-it"]
        try:
            # 2. check_library (pre), 3. restore_database
            self._run_logged(check_cmd, "check_library pre", 300, log_path)
            self.progress = 0.2
            result = self._run_logged(restore_cmd, "restore_database", 1200, log_path)
            if result.returncode != 0:
                self._handleError("Restore failed: %s (backups in %s)" % ((result.stderr or "").strip()[-500:], backup_dir))
                return
            self.progress = 0.8

            # 4. Drop app.db rows of books that did not come back (ids are kept, see above)
            try:
                removed = remove_orphan_book_rows(app_db_path, metadata_path)
                if removed:
                    self.log.info("Removed app.db rows of books missing after the restore: %s", removed)
            except Exception as e:
                self.log.error("Failed to clean up book-linked app.db rows: %s", e)
                self._handleError("Restore completed but app.db cleanup failed: %s (backups in %s)" % (e, backup_dir))
                return

            # 5. check_library (post)
            self._run_logged(check_cmd, "check_library post", 300, log_path)
        finally:
            # 6. Reconnect CalibreDB to clear stale sessions (also after a failure,
            # since the sessions were disposed above)
            try:
                calibre_db.reconnect_db(config, ub.app_DB_path)
            except Exception as e:
                self.log.error("Failed to reconnect CalibreDB after restore: %s", e)

        self.log.info("Library restore complete; backups and restore log in %s", backup_dir)
        self.message = N_('Restored Calibre library database. Backups and log: %(dir)s', dir=backup_dir)
        self._handleSuccess()
