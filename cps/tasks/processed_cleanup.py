# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Retention for /config/processed_books.

With auto_backup_imports on (the default) every import is copied into
processed_books/imported, and failed ingests land in processed_books/failed.
Nothing used to clean either, so /config grew without bound. Files older than
processed_books_retention_days (default 30, 0 = keep forever) are deleted
nightly. duplicate_resolutions/ is left alone: those are the only copies of
books deleted by duplicate resolution.
"""

import os
import sys
import time

from flask_babel import lazy_gettext as N_

from cps import logger
from cps.services.worker import CalibreTask

DEFAULT_RETENTION_DAYS = 30
PROCESSED_BOOKS_ROOT = "/config/processed_books"
PRUNED_SUBDIRS = ("imported", "failed")


def normalize_retention_days(value, default: int = DEFAULT_RETENTION_DAYS) -> int:
    """Days as a non-negative int (0 = keep forever); invalid values fall back to default."""
    if isinstance(value, bool):
        return default
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return days if days >= 0 else default


def prune_processed_books(root: str, days: int, now: float | None = None,
                          subdirs=PRUNED_SUBDIRS) -> list[str]:
    """Deletes regular files under root/<subdir> last modified more than `days` ago,
    then removes directories left empty below each subdir (the subdirs themselves
    stay). days <= 0 keeps everything. Symlinks are never followed or deleted.
    Returns the removed file paths."""
    if days <= 0:
        return []
    cutoff = (now if now is not None else time.time()) - days * 86400
    removed = []
    for sub in subdirs:
        base = os.path.join(root, sub)
        if not os.path.isdir(base) or os.path.islink(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base, topdown=False):
            for name in filenames:
                path = os.path.join(dirpath, name)
                try:
                    st = os.lstat(path)
                    if os.path.islink(path) or not os.path.isfile(path):
                        continue
                    if st.st_mtime < cutoff:
                        os.remove(path)
                        removed.append(path)
                except OSError:
                    continue
            if os.path.normpath(dirpath) != os.path.normpath(base):
                try:
                    os.rmdir(dirpath)  # only succeeds when empty
                except OSError:
                    pass
    return removed


def get_retention_days() -> int:
    try:
        if '/app/calibre-web-automated/scripts/' not in sys.path:
            sys.path.insert(1, '/app/calibre-web-automated/scripts/')
        from cwa_db import CWA_DB
        with CWA_DB() as cwa_db:
            return normalize_retention_days(cwa_db.cwa_settings.get("processed_books_retention_days"))
    except Exception:
        return DEFAULT_RETENTION_DAYS


class TaskCleanProcessedBooks(CalibreTask):
    """Nightly retention cleanup of /config/processed_books/{imported,failed}."""

    job_name = "processed_cleanup"

    def __init__(self, task_message=N_('Cleaning up processed book backups'), root=PROCESSED_BOOKS_ROOT):
        super(TaskCleanProcessedBooks, self).__init__(task_message)
        self.log = logger.create()
        self.root = root

    def run(self, worker_thread):
        days = get_retention_days()
        if days <= 0:
            self.log.debug("processed_books retention disabled (keep forever)")
            self._handleSuccess()
            return
        removed = prune_processed_books(self.root, days)
        if removed:
            self.log.info("Removed %d processed_books file(s) older than %d days", len(removed), days)
        self._handleSuccess()

    @property
    def name(self):
        return "Clean Processed Books"

    @property
    def is_cancellable(self):
        return False
