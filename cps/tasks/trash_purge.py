# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Nightly purge of Trash entries older than cwa_settings.trash_retention_days (default 30,
0 = keep until emptied by hand). See cps/trash.py."""

from flask_babel import lazy_gettext as N_

from cps import logger
from cps.services.worker import CalibreTask


class TaskPurgeTrash(CalibreTask):
    def __init__(self, task_message=N_('Emptying old items from the Trash'), root=None, days=None):
        super(TaskPurgeTrash, self).__init__(task_message)
        self.log = logger.create()
        self.root = root
        self.days = days

    def run(self, worker_thread):
        from cps import trash
        from cps import trash_store as store
        days = self.days if self.days is not None else trash.get_retention_days()
        root = self.root or trash.get_trash_root()
        if days <= 0:
            self._handleSuccess()
            return
        failed = []
        purged = store.purge(root, days, on_error=lambda entry, e: failed.append("%s (%s)" % (entry, e)))
        if purged:
            self.log.info("Deleted %d Trash item(s) older than %d days: %s", len(purged), days, ", ".join(purged))
        if failed:
            # Usually NFS placeholders of files still open elsewhere; the next run retries.
            self.log.warning("Could not delete Trash item(s): %s", "; ".join(failed))
        self._handleSuccess()

    @property
    def name(self):
        return "Purge Trash"

    @property
    def is_cancellable(self):
        return False
