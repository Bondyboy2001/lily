# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Task that looks every book up again with the metadata providers (Import & Metadata → Rebuild).

It first clears tags that are not subjects (ISBNs, publisher lines, shop listing scraps), and
after each lookup centres a PDF's page-render cover on what is printed (cps/pdf_cover.py).

A book that gets new details is kept in step like an edit is (metadata_helper does it): its folder
follows a new title or first author, and with "Write edits into book files" on, the change is
queued for the files.

How far a run has got is saved after every book (cwa.db), so one that was stopped, or cut short by
a restart, can be carried on from there instead of starting again."""

import sys
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from flask_babel import lazy_gettext as N_

from cps import config, db, helper, logger, pdf_cover
from cps.services.worker import (CalibreTask, STAT_CANCELLED, STAT_ENDED, STAT_FAIL, STAT_FINISH_SUCCESS,
                                 STAT_STARTED, STAT_WAITING)
from cps.tag_cleanup import tidy_library_tags
from cps.ub import init_db_thread
sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from cwa_db import CWA_DB  # noqa: E402

log = logger.create()

# Books looked up at once: a lookup is mostly waiting on the providers, a few seconds a book
WORKERS = 4


def saved_progress():
    """How far an unfinished rebuild got (next_book_id, checked, updated, covers, total), or None."""
    try:
        with CWA_DB() as store:
            return store.get_rebuild_progress()
    except Exception as ex:
        log.warning("Rebuild: could not read the saved progress: %s", ex)
        return None


class TaskRebuildMetadata(CalibreTask):
    def __init__(self, workers=WORKERS, resume=False):
        super(TaskRebuildMetadata, self).__init__(N_('Rebuilding metadata'))
        self.workers = workers
        # Carry on where an unfinished run got to, rather than from the first book
        self.resume = resume
        self.checked = 0
        self.updated = 0
        self.covers = 0
        self.total = 0
        # Provider name -> books it failed to answer for, told in the status line
        self.unanswered = Counter()
        # cwa.db, where the progress is saved; opened by the thread that runs the task
        self._store = None
        # The first book not yet handed out; with the books under way, where a later run carries on
        self._unsubmitted = None
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

    @property
    def state(self):
        """running, stopping (stopped, but finishing the books under way), stopped, done or
        failed. Stop marks the task ended at once, so `stat` alone can't tell the middle two."""
        if self.stat in (STAT_WAITING, STAT_STARTED):
            return "running"
        if self.stat == STAT_FAIL:
            return "failed"
        if self.stat == STAT_FINISH_SUCCESS:
            return "done"
        return "stopped" if self.finished else "stopping"

    @property
    def status_line(self):
        """What the settings page says about this run."""
        if self.stat == STAT_WAITING:
            return N_('Waiting to start…')
        if self.state == "stopping":
            return N_('Stopping after the books under way…')
        if self.state == "failed":
            return N_('The rebuild failed: %(error)s', error=self.error or N_('see the logs'))
        return self.message

    def run(self, worker_thread):
        from cps.metadata_helper import fetch_and_apply_metadata, library_lock
        try:
            init_db_thread()
        except Exception as ex:
            log.debug("Rebuild: no user database for this thread: %s", ex)
        # A session of this thread's own: the rebuild runs beside web requests
        cdb = db.CalibreDB(expire_on_commit=False, init=True)
        try:
            self._store = CWA_DB()
        except Exception as ex:
            log.warning("Rebuild: progress will not be saved: %s", ex)
        try:
            progress = self._store.get_rebuild_progress() if self.resume and self._store else None
            with library_lock:
                if not progress:
                    self._tidy_tags(cdb)
                book_ids = [row[0] for row in cdb.session.query(db.Books.id).order_by(db.Books.id).all()]
            if progress:
                book_ids = [book_id for book_id in book_ids if book_id >= progress["next_book_id"]]
                self.checked, self.updated, self.covers = (progress[k] for k in ("checked", "updated", "covers"))
                log.info("Rebuild: carrying on after %s books, from book %s", self.checked, progress["next_book_id"])
            self.total = self.checked + len(book_ids)
            centre_covers = pdf_cover.available()
            running = {}
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                for book_id in book_ids:
                    self._unsubmitted = book_id
                    while len(running) >= self.workers:
                        self._finish(cdb, running)
                    if self._stopped():
                        break
                    running[pool.submit(_look_up, fetch_and_apply_metadata, book_id, centre_covers)] = book_id
                else:
                    self._unsubmitted = None
                # A stop lets the books under way finish
                while running:
                    self._finish(cdb, running)
            self._save_progress(running)
            if self._stopped():
                self.message = N_('Stopped: %(checked)s of %(total)s books checked, %(updated)s updated%(unanswered)s',
                                  **self._counts())
                return
        finally:
            with library_lock:
                cdb.session.close()
            if self._store:
                self._store.close()
            self.finished = True
            if self.updated:
                try:
                    from cps.duplicate_index import mark_duplicate_index_pending
                    mark_duplicate_index_pending("metadata rebuild")
                except Exception as ex:
                    log.debug("Could not mark the duplicate index pending: %s", ex)
        if self.covers:
            self.message = N_('Done: %(total)s books checked, %(updated)s updated, %(covers)s covers centred%(unanswered)s',
                              **self._counts())
        else:
            self.message = N_('Done: %(total)s books checked, %(updated)s updated%(unanswered)s', **self._counts())
        log.info("Metadata rebuild finished: %s books checked, %s updated, %s covers centred, no answer: %s",
                 self.total, self.updated, self.covers, dict(self.unanswered) or "none")
        self._handleSuccess()

    def _finish(self, cdb, running):
        """Wait for a lookup to end and count it; record a centred cover."""
        from cps.metadata_helper import library_lock
        done, __ = wait(running, return_when=FIRST_COMPLETED)
        for future in done:
            book_id = running.pop(future)
            try:
                updated, centred, unanswered = future.result()
                if updated:
                    self.updated += 1
                self.unanswered.update(unanswered)
                if centred:
                    with library_lock:
                        self._cover_changed(cdb, book_id)
            except Exception as ex:
                # One book going wrong must not end a run of hours, or strand the books under way
                with library_lock:
                    cdb.session.rollback()
                log.error("Rebuild: book %s failed: %s", book_id, ex, exc_info=True)
            self._count()
        self._save_progress(running)

    def _count(self):
        self.checked += 1
        self.progress = self.checked / self.total
        self.message = N_('%(checked)s of %(total)s books checked, %(updated)s updated%(unanswered)s',
                          **self._counts())

    def _counts(self):
        """The numbers the status lines are written from."""
        return dict(checked=self.checked, total=self.total, updated=self.updated, covers=self.covers,
                    unanswered=self._unanswered_note())

    def _unanswered_note(self):
        """'. No answer from Google (120), Open Library (3)': the books each provider failed to
        answer for, which were looked up without it. Empty when all answered."""
        if not self.unanswered:
            return ''
        providers = ', '.join('%s (%s)' % item for item in sorted(self.unanswered.items()))
        return N_('. No answer from %(providers)s', providers=providers)

    def _save_progress(self, running):
        """Note the lowest book not yet checked, for a later run to carry on from; nothing is
        kept once every book is done."""
        if not self._store:
            return
        waiting = list(running.values()) + ([self._unsubmitted] if self._unsubmitted is not None else [])
        try:
            if waiting:
                self._store.save_rebuild_progress(min(waiting), self.checked, self.updated, self.covers, self.total)
            else:
                self._store.clear_rebuild_progress()
        except Exception as ex:
            log.warning("Rebuild: could not save the progress: %s", ex)

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
            return
        self.covers += 1
        helper.replace_cover_thumbnail_cache(book_id)


def _look_up(fetch, book_id, centre_covers):
    """One book's work in the pool: the lookup, then centring a PDF's cover on its print.
    Returns (updated, cover centred, providers that failed to answer).

    The cover comes second so a cover the provider just set is never cropped: it no longer
    looks like the page render, so recentre_cover leaves it. Its paths are read after the
    lookup, which moves the book's folder when the title or first author changes."""
    from cps.metadata_helper import library_lock
    unanswered = set()
    updated = fetch(book_id, force=True, unanswered=unanswered)
    if not centre_covers:
        return updated, False, unanswered
    with library_lock:
        cdb = db.CalibreDB(expire_on_commit=False, init=True)
        try:
            cover = _cover_paths(cdb, book_id)
        finally:
            cdb.session.close()
    return updated, pdf_cover.try_recentre_cover(cover, book_id), unanswered


def _cover_paths(cdb, book_id):
    book = cdb.session.get(db.Books, book_id)
    return pdf_cover.cover_paths(book, config.get_book_path()) if book else None
