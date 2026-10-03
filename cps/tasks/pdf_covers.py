# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Task that makes every PDF book's cover its first page again (Settings → Metadata → Redo
PDF covers), with no metadata lookups, so it is much quicker than a full rebuild.

Each PDF's page 1 is rendered as it is printed (cps/pdf_cover.py), and only a cover.jpg that
holds a different picture is rewritten, so a second run changes nothing. Covers picked by hand
are kept, as cover_job says. Books are done a few at a time
(one Ghostscript each), leaving a core for the web app.

Changed covers are recorded in batches (RECORD_BATCH books a commit): on the NAS each commit
syncs the disk (~45 ms a sync), and one per book, plus a dozen per book for its thumbnails,
held the renders up. A batch flags its books as having a new cover (their last_modified moves,
so cover URLs change), deletes their cached thumbnails in one go (they are made again when next
shown) and keeps each lookup that was fresh fresh, so the next rebuild doesn't look the book up
again for its new cover.

The task is a TaskRebuildMetadata for its bookkeeping (checked/total/covers, the status line,
Stop). It shares no more: no authors tidy, no providers, no saved progress (there is nothing to
carry on), no duplicate-index touch (covers don't affect it)."""

import os
import sys
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, UTC

from flask_babel import lazy_gettext as N_
from sqlalchemy import func

from cps import db, logger, pdf_cover
from cps.tasks.metadata_rebuild import TaskRebuildMetadata, _changed_since, _cover_job
from cps.tasks.thumbnail import clear_cover_thumbnails
from cps.ub import init_db_thread
sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from cwa_db import CWA_DB  # noqa: E402

log = logger.create()

# Changed covers recorded per commit
RECORD_BATCH = 200


def default_workers():
    """One book per core but one, at most 4: rendering is CPU bound, and the web app keeps a core."""
    return max(1, min(4, (os.cpu_count() or 2) - 1))


def _redo_one(book_id):
    """One book's cover in the pool; True when it changed."""
    from cps.metadata_helper import library_lock
    with library_lock:
        book_db = db.CalibreDB(expire_on_commit=False, init=True)
        try:
            job = _cover_job(book_db, book_id)
        finally:
            book_db.session.close()
    return pdf_cover.try_fix_cover(job, book_id)


class TaskRedoPdfCovers(TaskRebuildMetadata):
    def __init__(self, workers=None):
        super().__init__(workers=workers or default_workers())
        self.message = N_('Redoing PDF covers')
        # Books whose cover.jpg changed, not yet recorded
        self._changed = []

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
            running = {}
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                for book_id in book_ids:
                    while len(running) >= self.workers:
                        self._finish_covers(cdb, running)
                    if self.stop_requested:
                        break
                    running[pool.submit(_redo_one, book_id)] = book_id
                # A stop lets the books under way finish
                while running:
                    self._finish_covers(cdb, running)
            self._record_covers(cdb)
            if self.stop_requested:
                self.message = N_('Stopped: %(checked)s of %(total)s PDFs checked, %(covers)s covers redone',
                                  checked=self.checked, total=self.total, covers=self.covers)
                return
        finally:
            # Covers already written are recorded even when the run fails part way
            if self._changed:
                try:
                    self._record_covers(cdb)
                except Exception as ex:
                    log.error("Redo covers: could not record the last covers: %s", ex)
            with library_lock:
                cdb.session.close()
            if self._store:
                self._store.close()
        self.message = N_('Done: %(total)s PDFs checked, %(covers)s covers redone',
                          total=self.total, covers=self.covers)
        log.info("Redo PDF covers finished: %s PDFs checked, %s covers redone", self.total, self.covers)
        self._handleSuccess()

    def _finish_covers(self, cdb, running):
        """Wait for a book to end and count it; a changed cover is recorded with its batch."""
        done, __ = wait(running, return_when=FIRST_COMPLETED)
        for future in done:
            book_id = running.pop(future)
            try:
                if future.result():
                    self._changed.append(book_id)
            except Exception as ex:
                # One book going wrong must not end the run
                log.error("Redo covers: book %s failed: %s", book_id, ex, exc_info=True)
            self._count()
        if len(self._changed) >= RECORD_BATCH:
            self._record_covers(cdb)

    def _record_covers(self, cdb):
        """Record the batch of changed covers in one commit: flag the books and move their
        last_modified, then drop their thumbnails and re-stamp the lookups that were fresh."""
        from cps.metadata_helper import library_lock
        book_ids, self._changed = self._changed, []
        if not book_ids:
            return
        with library_lock:
            cdb.session.expire_all()
            before = dict(cdb.session.query(db.Books.id, db.Books.last_modified)
                          .filter(db.Books.id.in_(book_ids)).all())
            try:
                cdb.session.query(db.Books).filter(db.Books.id.in_(list(before))).update(
                    {db.Books.has_cover: 1, db.Books.last_modified: datetime.now(UTC)},
                    synchronize_session=False)
                cdb.session.commit()
            except Exception as ex:
                cdb.session.rollback()
                log.error("Redo covers: could not record %s new covers: %s", len(before), ex)
                return
        self.covers += len(before)
        try:
            clear_cover_thumbnails(before)
        except Exception as ex:
            log.warning("Redo covers: could not clear old thumbnails: %s", ex)
        if self._store:
            for book_id, last_modified in before.items():
                self._restamp(book_id, last_modified)

    def _restamp(self, book_id, before):
        """Stamp a lookup that was fresh before the cover moved last_modified again, so it
        still counts as up to date."""
        try:
            record = self._store.get_metadata_lookup(book_id)
            if record and record["status"] in ("matched", "nomatch", "manual") and \
                    not _changed_since(before, record["checked_at"]):
                self._store.save_metadata_lookup(book_id, record["status"], record["source"])
        except Exception as ex:
            log.debug("Redo covers: could not re-stamp the lookup of book %s: %s", book_id, ex)

    def _count(self):
        self.checked += 1
        self.progress = self.checked / self.total
        self.message = N_('%(checked)s of %(total)s PDFs checked, %(covers)s covers redone',
                          checked=self.checked, total=self.total, covers=self.covers)
