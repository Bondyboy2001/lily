# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Task that looks every book up again with the metadata providers (Import & Metadata → Rebuild)."""

import time

from flask_babel import lazy_gettext as N_

from cps import calibre_db, db, logger
from cps.services.worker import CalibreTask, STAT_CANCELLED, STAT_ENDED
from cps.ub import init_db_thread

log = logger.create()

# Pause between books so a whole library doesn't trip the providers' rate limits
PAUSE_SECONDS = 1.0


class TaskRebuildMetadata(CalibreTask):
    def __init__(self, pause=PAUSE_SECONDS):
        super(TaskRebuildMetadata, self).__init__(N_('Rebuilding metadata'))
        self.pause = pause
        self.checked = 0
        self.updated = 0

    @property
    def name(self):
        return str(N_('Rebuild metadata'))

    @property
    def is_cancellable(self):
        return True

    def _stopped(self):
        return self.stat in (STAT_CANCELLED, STAT_ENDED)

    def run(self, worker_thread):
        from cps.metadata_helper import fetch_and_apply_metadata
        try:
            init_db_thread()
        except Exception:
            pass
        calibre_db.ensure_session()
        book_ids = [row[0] for row in calibre_db.session.query(db.Books.id).order_by(db.Books.id).all()]
        total = len(book_ids)
        for book_id in book_ids:
            if self._stopped():
                return
            if fetch_and_apply_metadata(book_id, force=True):
                self.updated += 1
            self.checked += 1
            self.progress = self.checked / total
            self.message = N_('%(checked)s of %(total)s books checked, %(updated)s updated',
                              checked=self.checked, total=total, updated=self.updated)
            if self.checked < total and self.pause:
                time.sleep(self.pause)
        if self.updated:
            try:
                from cps.duplicate_index import mark_duplicate_index_pending
                mark_duplicate_index_pending("metadata rebuild")
            except Exception as ex:
                log.debug("Could not mark the duplicate index pending: %s", ex)
        self.message = N_('Done: %(total)s books checked, %(updated)s updated', total=total, updated=self.updated)
        log.info("Metadata rebuild finished: %s books checked, %s updated", total, self.updated)
        self._handleSuccess()
