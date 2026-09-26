# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import os
import sys

from flask_babel import lazy_gettext as N_

from cps import config, logger, ub
from cps.services.worker import CalibreTask

if '/app/calibre-web-automated/scripts/' not in sys.path:
    sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from db_backup import backup_databases, normalize_keep_count, DEFAULT_KEEP_COUNT, BACKUP_SUBDIR


def get_config_dir() -> str:
    # Same resolution as scripts/cwa_db.py (CWA_DB_PATH only differs in tests)
    return os.environ.get("CWA_DB_PATH", "/config")


def get_backup_sources() -> dict:
    """{file name in snapshot: live path} for app.db, cwa.db and the library's metadata.db."""
    config_dir = get_config_dir()
    sources = {
        "app.db": ub.app_DB_path or os.path.join(config_dir, "app.db"),
        "cwa.db": os.path.join(config_dir, "cwa.db"),
    }
    if config.config_calibre_dir:
        sources["metadata.db"] = os.path.join(config.config_calibre_dir, "metadata.db")
    return sources


def get_keep_count() -> int:
    try:
        from cwa_db import CWA_DB
        with CWA_DB() as cwa_db:
            return normalize_keep_count(cwa_db.cwa_settings.get("db_backup_keep_count", DEFAULT_KEEP_COUNT))
    except Exception:
        return DEFAULT_KEEP_COUNT


class TaskBackupDatabases(CalibreTask):
    """Nightly consistent snapshots of app.db, cwa.db and metadata.db into /config/backup/db/<timestamp>/."""

    def __init__(self, task_message=N_('Backing up databases')):
        super(TaskBackupDatabases, self).__init__(task_message)
        self.log = logger.create()

    def run(self, worker_thread):
        backup_root = os.path.join(get_config_dir(), BACKUP_SUBDIR)
        keep = get_keep_count()
        snapshot_dir, done, errors = backup_databases(get_backup_sources(), backup_root, keep)
        for name, err in errors.items():
            self.log.error("Database backup of %s failed: %s", name, err)
        if done:
            self.log.info("Backed up %s to %s (keeping last %d)", ", ".join(sorted(done)), snapshot_dir, keep)
        if errors:
            self._handleError("Database backup failed for: " + ", ".join(
                "{} ({})".format(name, err) for name, err in sorted(errors.items())))
        else:
            self._handleSuccess()

    @property
    def name(self):
        return "Backup Databases"

    @property
    def is_cancellable(self):
        return False
