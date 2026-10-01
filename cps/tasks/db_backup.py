# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Tasks that snapshot app.db, cwa.db and metadata.db, and restore them from a snapshot."""

import os
import sys

from flask_babel import lazy_gettext as N_

from cps import config, logger, ub
from cps.services.worker import CalibreTask

if '/app/calibre-web-automated/scripts/' not in sys.path:
    sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from db_backup import (normalize_keep_count, normalize_tier_count, run_backup, Retention, DEFAULT_KEEP_COUNT,
                       DEFAULT_KEEP_WEEKLY, DEFAULT_KEEP_MONTHLY, BACKUP_SUBDIR, check_integrity,
                       create_pre_restore_snapshot, resolve_snapshot, restore_sqlite_db)

from cps.tasks.restore import RestoreTask

# Restore order: settings/stats first, the library last (it triggers a reconnect)
RESTORABLE_DBS = ("cwa.db", "app.db", "metadata.db")


def get_config_dir() -> str:
    # Same resolution as scripts/cwa_db.py (CWA_DB_PATH only differs in tests)
    return os.environ.get("CWA_DB_PATH", "/config")


def _configured_backup_dir() -> str:
    try:
        from cwa_db import CWA_DB
        with CWA_DB() as cwa_db:
            value = cwa_db.cwa_settings.get("db_backup_dir") or ""
    except Exception:
        return ""
    if isinstance(value, list):
        # get_cwa_settings() splits comma-containing strings into lists
        value = ",".join(value)
    return str(value).strip()


def get_backup_root() -> str:
    """Where snapshots live: cwa_settings.db_backup_dir, else the DB_BACKUP_DIR env
    var, else /config/backup/db. Use a different volume from /config so losing
    /config doesn't lose the backups too."""
    return (_configured_backup_dir() or os.environ.get("DB_BACKUP_DIR")
            or os.path.join(get_config_dir(), BACKUP_SUBDIR))


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


def retention_from_settings(settings: dict) -> Retention:
    """The daily/weekly/monthly snapshot counts from cwa_settings."""
    return Retention(
        daily=normalize_keep_count(settings.get("db_backup_keep_count", DEFAULT_KEEP_COUNT)),
        weekly=normalize_tier_count(settings.get("db_backup_keep_weekly"), DEFAULT_KEEP_WEEKLY),
        monthly=normalize_tier_count(settings.get("db_backup_keep_monthly"), DEFAULT_KEEP_MONTHLY))


def get_retention() -> Retention:
    try:
        from cwa_db import CWA_DB
        with CWA_DB() as cwa_db:
            return retention_from_settings(cwa_db.cwa_settings)
    except Exception:
        return Retention()


class TaskBackupDatabases(CalibreTask):
    """Nightly consistent snapshots of app.db, cwa.db and metadata.db into <backup root>/<timestamp>/.

    Each snapshot is verified by restoring it to scratch, checked for a shrunken
    library, and only then are old snapshots pruned (see db_backup.run_backup)."""

    job_name = "db_backup"

    def __init__(self, task_message=N_('Backing up databases')):
        super(TaskBackupDatabases, self).__init__(task_message)
        self.log = logger.create()

    def run(self, worker_thread):
        backup_root = get_backup_root()
        retention = get_retention()
        result = run_backup(get_backup_sources(), backup_root, retention)
        errors = result.errors
        for name, err in errors.items():
            if name == "verify":
                self.log.error("Backup verification failed for %s: %s", result.snapshot_dir, err)
            else:
                self.log.error("Database backup of %s failed: %s", name, err)
        if result.done:
            self.log.info("Backed up %s to %s (keeping %d daily, %d weekly, %d monthly; pruned %d path(s))",
                          ", ".join(sorted(result.done)), result.snapshot_dir, retention.daily,
                          retention.weekly, retention.monthly, len(result.removed))
        if result.suspicious:
            # Not pruned: the older snapshots may be the only copies of the missing books
            self.log.warning("Suspicious backup %s: %s. Old snapshots were not pruned; accept the snapshot "
                             "on the Database Backups page if this was intended.", result.snapshot_dir,
                             result.suspicious)
        problems = []
        if errors:
            problems.append("Database backup failed for: " + ", ".join(
                "{} ({})".format(name, err) for name, err in sorted(errors.items())))
        if result.suspicious:
            problems.append("Suspicious backup, old snapshots kept: " + result.suspicious)
        if problems:
            self._handleError("; ".join(problems))
        else:
            self._handleSuccess()

    @property
    def name(self):
        return "Backup Databases"

    @property
    def is_cancellable(self):
        return False


class TaskRestoreDatabaseSnapshot(RestoreTask):
    """Restores app.db / cwa.db / metadata.db from a nightly snapshot.

    Every snapshot file is integrity-checked first, then a safety copy of the live
    databases is written to <backup root>/<stamp>_pre-restore, then each database
    is replaced atomically through the sqlite backup API (see restore_sqlite_db).
    If a later database fails, the ones already restored are put back from the
    safety copy. Afterwards the app reconnects to metadata.db, re-runs cwa.db
    schema migrations and reloads its configuration from app.db.
    """

    def __init__(self, snapshot_name, databases=None, task_message=None):
        super(TaskRestoreDatabaseSnapshot, self).__init__(
            task_message or N_('Restoring databases from snapshot %(name)s', name=snapshot_name))
        self.snapshot_name = snapshot_name
        self.databases = list(databases) if databases else list(RESTORABLE_DBS)

    @property
    def name(self):
        return "Restore Database Snapshot"

    def do_restore(self):
        backup_root = get_backup_root()
        snapshot_dir = resolve_snapshot(backup_root, self.snapshot_name)
        live = get_backup_sources()
        targets = [n for n in RESTORABLE_DBS
                   if n in self.databases and n in live and os.path.isfile(os.path.join(snapshot_dir, n))]
        if not targets:
            self._handleError("Snapshot %s contains none of the selected databases" % self.snapshot_name)
            return

        # 1. Validate every snapshot file before touching anything
        for name in targets:
            try:
                check_integrity(os.path.join(snapshot_dir, name))
            except Exception as e:
                self._handleError("Snapshot %s/%s is not usable: %s" % (self.snapshot_name, name, e))
                return
        self.progress = 0.2

        # 2. Safety copy of the live databases (restore is refused without one)
        safety_dir, safety = create_pre_restore_snapshot({n: live[n] for n in targets}, backup_root)
        self.log.info("Pre-restore safety copy of %s written to %s", ", ".join(sorted(safety)), safety_dir)
        self.progress = 0.4

        # 3. Swap each database in
        restored = []
        try:
            for name in targets:
                restore_sqlite_db(os.path.join(snapshot_dir, name), live[name])
                restored.append(name)
                self.progress = 0.4 + 0.5 * len(restored) / len(targets)
        except Exception as e:
            self.log.error("Restoring %s failed: %s; rolling back %s from %s",
                           self.snapshot_name, e, ", ".join(restored) or "nothing", safety_dir)
            for name in restored:
                try:
                    restore_sqlite_db(safety[name], live[name])
                except Exception as rollback_error:
                    self.log.error("Rollback of %s failed: %s (safety copy: %s)", name, rollback_error, safety[name])
            self._reload(restored)
            self._handleError("Restore failed: %s. Live databases were rolled back; safety copy in %s" % (e, safety_dir))
            return

        # 4. Reconnect / reload
        self._reload(restored)
        self.log.info("Restored %s from snapshot %s (safety copy: %s)", ", ".join(restored), snapshot_dir, safety_dir)
        self.message = N_('Restored %(dbs)s from snapshot %(name)s', dbs=", ".join(restored), name=self.snapshot_name)
        self._handleSuccess()

    def _reload(self, restored):
        if "cwa.db" in restored:
            try:
                from cwa_db import invalidate_schema_cache
                invalidate_schema_cache()  # next CWA_DB() re-runs schema sync on the older file
            except Exception as e:
                self.log.warning("Could not reset cwa.db schema cache: %s", e)
        if "app.db" in restored:
            try:
                config.load()
            except Exception as e:
                self.log.warning("Could not reload configuration after app.db restore, restart Lily: %s", e)
        if "metadata.db" in restored or "app.db" in restored:
            try:
                from cps import calibre_db
                calibre_db.reconnect_db(config, ub.app_DB_path)
            except Exception as e:
                self.log.error("Failed to reconnect Calibre database after restore: %s", e)
