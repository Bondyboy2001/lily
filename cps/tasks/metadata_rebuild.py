# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Task that looks every book up again with the metadata providers (Import & Metadata → Rebuild).

It first clears tags that are not subjects (ISBNs, publisher lines, shop listing scraps), and
after each lookup centres a PDF's page-render cover on what is printed (cps/pdf_cover.py).

A book that gets new details is kept in step like an edit is (metadata_helper does it): its folder
follows a new title or first author, and with "Write edits into book files" on, the change is
queued for the files."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from flask_babel import lazy_gettext as N_

from cps import config, db, helper, logger, pdf_cover
from cps.services.worker import CalibreTask, STAT_CANCELLED, STAT_ENDED
from cps.tag_cleanup import tidy_library_tags
from cps.ub import init_db_thread

log = logger.create()

# Books looked up at once: a lookup is mostly waiting on the providers, a few seconds a book
WORKERS = 4


class TaskRebuildMetadata(CalibreTask):
    def __init__(self, workers=WORKERS):
        super(TaskRebuildMetadata, self).__init__(N_('Rebuilding metadata'))
        self.workers = workers
        self.checked = 0
        self.updated = 0
        self.covers = 0
        # Stopping marks the task ended at once, but it finishes the books under way first
        self.finished = False

    @property
    def name(self):
        return str(N_('Rebuild metadata'))

    @property
    def is_cancellable(self):
        return True

    def _stopped(self):
        return self.stat in (STAT_CANCELLED, STAT_ENDED)

    def run(self, worker_thread):
        from cps.metadata_helper import fetch_and_apply_metadata, library_lock
        try:
            init_db_thread()
        except Exception as ex:
            log.debug("Rebuild: no user database for this thread: %s", ex)
        # A session of this thread's own: the rebuild runs beside web requests
        cdb = db.CalibreDB(expire_on_commit=False, init=True)
        try:
            with library_lock:
                self._tidy_tags(cdb)
                book_ids = [row[0] for row in cdb.session.query(db.Books.id).order_by(db.Books.id).all()]
            total = len(book_ids)
            centre_covers = pdf_cover.available()
            running = {}
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                for book_id in book_ids:
                    while len(running) >= self.workers:
                        self._finish(cdb, running, total)
                    if self._stopped():
                        break
                    running[pool.submit(_look_up, fetch_and_apply_metadata, book_id, centre_covers)] = book_id
                # A stop lets the books under way finish
                while running:
                    self._finish(cdb, running, total)
            if self._stopped():
                self.message = N_('Stopped: %(checked)s of %(total)s books checked, %(updated)s updated',
                                  checked=self.checked, total=total, updated=self.updated)
                return
        finally:
            with library_lock:
                cdb.session.close()
            self.finished = True
            if self.updated:
                try:
                    from cps.duplicate_index import mark_duplicate_index_pending
                    mark_duplicate_index_pending("metadata rebuild")
                except Exception as ex:
                    log.debug("Could not mark the duplicate index pending: %s", ex)
        if self.covers:
            self.message = N_('Done: %(total)s books checked, %(updated)s updated, %(covers)s covers centred',
                              total=total, updated=self.updated, covers=self.covers)
        else:
            self.message = N_('Done: %(total)s books checked, %(updated)s updated', total=total, updated=self.updated)
        log.info("Metadata rebuild finished: %s books checked, %s updated, %s covers centred",
                 total, self.updated, self.covers)
        self._handleSuccess()

    def _finish(self, cdb, running, total):
        """Wait for a lookup to end and count it; record a centred cover."""
        from cps.metadata_helper import library_lock
        done, __ = wait(running, return_when=FIRST_COMPLETED)
        for future in done:
            book_id = running.pop(future)
            updated, centred = future.result()
            if updated:
                self.updated += 1
            if centred:
                with library_lock:
                    self._cover_changed(cdb, book_id)
            self._count(total)

    def _count(self, total):
        self.checked += 1
        self.progress = self.checked / total
        self.message = N_('%(checked)s of %(total)s books checked, %(updated)s updated',
                          checked=self.checked, total=total, updated=self.updated)

    def _tidy_tags(self, cdb):
        """Clear out tags that are not subjects first: it takes seconds, the lookups take hours."""
        self.message = N_('Tidying tags')
        try:
            changed, removed = tidy_library_tags(cdb.session)
            log.info("Rebuild: tidied the tags of %s books, removed %s unused tags", changed, removed)
        except Exception as ex:
            cdb.session.rollback()
            log.error("Rebuild: could not tidy tags: %s", ex)

    def _cover_changed(self, cdb, book_id):
        """Record a centred cover so its URL and thumbnails change with it."""
        cdb.session.expire_all()
        book = cdb.session.get(db.Books, book_id)
        if book is None:
            return
        try:
            pdf_cover.mark_cover_changed(book)
            cdb.session.commit()
        except Exception as ex:
            cdb.session.rollback()
            log.error("Rebuild: could not record the new cover of book %s: %s", book_id, ex)
        self.covers += 1
        helper.replace_cover_thumbnail_cache(book_id)


def _look_up(fetch, book_id, centre_covers):
    """One book's work in the pool: the lookup, then centring a PDF's cover on its print.

    The cover comes second so a cover the provider just set is never cropped: it no longer
    looks like the page render, so recentre_cover leaves it. Its paths are read after the
    lookup, which moves the book's folder when the title or first author changes."""
    from cps.metadata_helper import library_lock
    updated = fetch(book_id, force=True)
    if not centre_covers:
        return updated, False
    with library_lock:
        cdb = db.CalibreDB(expire_on_commit=False, init=True)
        try:
            cover = _cover_paths(cdb, book_id)
        finally:
            cdb.session.close()
    return updated, pdf_cover.try_recentre_cover(cover, book_id)


def _cover_paths(cdb, book_id):
    book = cdb.session.get(db.Books, book_id)
    return pdf_cover.cover_paths(book, config.get_book_path()) if book else None
