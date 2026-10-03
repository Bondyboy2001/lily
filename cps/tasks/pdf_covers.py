# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Task that makes every PDF book's cover its first page again (Settings → Metadata → Redo
PDF covers), with no metadata lookups, so it is much quicker than a full rebuild (about a quarter of a
second a PDF).

It exists so a better page centring (cps/pdf_cover.py) reaches the covers already made: each
PDF is rendered and trimmed with the current cropper, and only a cover whose new crop differs
is rewritten. Covers picked by hand are kept, as cover_job says.

The task is a TaskRebuildMetadata for its bookkeeping (checked/total/covers, the status line,
Stop) and for _cover_changed, which keeps a changed cover's lookup fresh instead of letting
the next rebuild look the book up again. It shares no more: no authors tidy, no providers, no
saved progress (there is nothing to carry on), no duplicate-index touch (covers don't affect
it)."""

import sys

from flask_babel import lazy_gettext as N_
from sqlalchemy import func

from cps import db, logger, pdf_cover
from cps.tasks.metadata_rebuild import TaskRebuildMetadata, _cover_job
from cps.ub import init_db_thread
sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from cwa_db import CWA_DB  # noqa: E402

log = logger.create()


class TaskRedoPdfCovers(TaskRebuildMetadata):
    def __init__(self):
        super().__init__(workers=1)
        self.message = N_('Redoing PDF covers')

    @property
    def name(self):
        return str(N_('Redo PDF covers'))

    def run(self, worker_thread):
        from cps.metadata_helper import library_lock
        try:
            init_db_thread()
        except Exception as ex:
            log.debug("Redo covers: no user database for this thread: %s", ex)
        cdb = db.CalibreDB(expire_on_commit=False, init=True)
        try:
            self._store = CWA_DB()
        except Exception as ex:
            log.warning("Redo covers: lookups will not be kept fresh: %s", ex)
        try:
            if not pdf_cover.available():
                self._handleError(N_('PDF covers can’t be made here: ImageMagick is missing'))
                return
            with library_lock:
                book_ids = [row[0] for row in cdb.session.query(db.Data.book)
                            .filter(func.upper(db.Data.format) == 'PDF')
                            .distinct().order_by(db.Data.book).all()]
            self.total = len(book_ids)
            for book_id in book_ids:
                if self.stop_requested:
                    break
                try:
                    with library_lock:
                        book_db = db.CalibreDB(expire_on_commit=False, init=True)
                        try:
                            job = _cover_job(book_db, book_id)
                        finally:
                            book_db.session.close()
                    if pdf_cover.try_fix_cover(job, book_id):
                        with library_lock:
                            self._cover_changed(cdb, book_id)
                except Exception as ex:
                    # One book going wrong must not end the run
                    with library_lock:
                        cdb.session.rollback()
                    log.error("Redo covers: book %s failed: %s", book_id, ex, exc_info=True)
                self._count()
            if self.stop_requested:
                self.message = N_('Stopped: %(checked)s of %(total)s PDFs checked, %(covers)s covers redone',
                                  checked=self.checked, total=self.total, covers=self.covers)
                return
        finally:
            with library_lock:
                cdb.session.close()
            if self._store:
                self._store.close()
        self.message = N_('Done: %(total)s PDFs checked, %(covers)s covers redone',
                          total=self.total, covers=self.covers)
        log.info("Redo PDF covers finished: %s PDFs checked, %s covers redone", self.total, self.covers)
        self._handleSuccess()

    def _count(self):
        self.checked += 1
        self.progress = self.checked / self.total
        self.message = N_('%(checked)s of %(total)s PDFs checked, %(covers)s covers redone',
                          checked=self.checked, total=self.total, covers=self.covers)
