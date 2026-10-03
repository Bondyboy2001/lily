# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Task that looks every book up again with the metadata providers (Import & Metadata → Rebuild).

It first makes each author the people it names (cps/author_cleanup.py) and clears tags that are
not subjects (ISBNs, publisher lines, shop listing scraps), and after each lookup gives a PDF
with no cover its first page, or centres a page-render cover on what is printed
(cps/pdf_cover.py).

A book that gets new details is kept in step like an edit is (metadata_helper does it): its folder
follows a new title or first author, and with "Write edits into book files" on, the change is
queued for the files.

How far a run has got is saved after every book (cwa.db), so one that was stopped, or cut short by
a restart, can be carried on from there instead of starting again.

A rebuild skips a book that is up to date: its last lookup matched it or found nothing, and it
hasn't changed since. A full rebuild first forgets what earlier lookups found (cwa.db) and looks
every book up again."""

import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from flask_babel import lazy_gettext as N_
from sqlalchemy import func

from cps import config, db, helper, logger, pdf_cover
from cps.services.worker import (CalibreTask, STAT_FAIL, STAT_FINISH_SUCCESS, STAT_STARTED, STAT_STOPPING,
                                 STAT_WAITING)
from cps.author_cleanup import tidy_authors
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
    def __init__(self, workers=WORKERS, resume=False, book_ids=None, selection=False, full=False):
        super(TaskRebuildMetadata, self).__init__(N_('Rebuilding metadata'))
        self.workers = workers
        # Carry on where an unfinished run got to, rather than from the first book
        self.resume = resume
        # Only these books (Retry failed, or a selection in the book table): no tidy first, and a full rebuild's progress is left alone
        self.book_ids = sorted(book_ids) if book_ids is not None else None
        # The books were picked by hand in the book table, not the failed ones: only the name differs
        self.selection = selection
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
        # Books whose authors the tidy before the lookups changed
        self.authors_tidied = 0
        # Forget earlier lookups and look every book up again, rather than skip those up to date
        self.full = full
        # Books skipped as up to date
        self.skipped = 0

    @property
    def name(self):
        if self.selection:
            return str(N_('Look up selected books'))
        return str(N_('Retry failed lookups') if self.book_ids is not None else N_('Rebuild metadata'))

    @property
    def is_cancellable(self):
        return True

    @property
    def state(self):
        """running, stopping (told to stop, finishing the books under way), stopped, done or
        failed: the task's status in the settings page's words."""
        if self.stat in (STAT_WAITING, STAT_STARTED):
            return "running"
        if self.stat == STAT_STOPPING:
            return "stopping"
        if self.stat == STAT_FAIL:
            return "failed"
        if self.stat == STAT_FINISH_SUCCESS:
            return "done"
        return "stopped"

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
            if self.full and not progress and self._store:
                self._store.clear_lookup_records()
            with library_lock:
                if not progress and self.book_ids is None:
                    self._tidy_authors(cdb)
                    self._tidy_tags(cdb)
                    self._clear_none_descriptions(cdb)
                books = cdb.session.query(db.Books.id, db.Books.last_modified).order_by(db.Books.id).all()
            book_ids = [row[0] for row in books]
            if self.book_ids is None and not self.full:
                book_ids = self._still_to_look_up(books)
            if self.book_ids is not None:
                # Those still in the library; picked here, not in SQL, as there can be thousands
                wanted = set(self.book_ids)
                book_ids = [book_id for book_id in book_ids if book_id in wanted]
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
                    if self.stop_requested:
                        break
                    running[pool.submit(_look_up, fetch_and_apply_metadata, book_id, centre_covers)] = book_id
                else:
                    self._unsubmitted = None
                # A stop lets the books under way finish
                while running:
                    self._finish(cdb, running)
            self._save_progress(running)
            if self.stop_requested:
                self.message = N_('Stopped: %(checked)s of %(total)s books checked, %(updated)s updated%(unanswered)s',
                                  **self._counts())
                return
        finally:
            with library_lock:
                cdb.session.close()
            if self._store:
                self._store.close()
            if self.updated or self.authors_tidied:
                try:
                    from cps.duplicate_index import mark_duplicate_index_pending
                    mark_duplicate_index_pending("metadata rebuild")
                except Exception as ex:
                    log.debug("Could not mark the duplicate index pending: %s", ex)
        if self.covers:
            self.message = N_('Done: %(total)s books checked, %(updated)s updated, %(covers)s covers made or centred%(unanswered)s',
                              **self._counts())
        else:
            self.message = N_('Done: %(total)s books checked, %(updated)s updated%(unanswered)s', **self._counts())
        log.info("Metadata rebuild finished: %s books checked, %s updated, %s covers made or centred, no answer: %s",
                 self.total, self.updated, self.covers, dict(self.unanswered) or "none")
        self._handleSuccess()

    def _finish(self, cdb, running):
        """Wait for a lookup to end and count it; record a made or centred cover."""
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

    def _still_to_look_up(self, books):
        """The books not up to date: never looked up, failed, or changed since their lookup
        matched them or found nothing. The others are counted as skipped."""
        try:
            lookups = self._store.metadata_lookups_by_book() if self._store else {}
        except Exception as ex:
            log.warning("Rebuild: could not read the earlier lookups, so every book is looked up: %s", ex)
            return [book_id for book_id, __ in books]
        wanted = []
        for book_id, last_modified in books:
            status, checked = lookups.get(book_id, (None, None))
            if status in ("matched", "nomatch") and not _changed_since(last_modified, checked):
                self.skipped += 1
            else:
                wanted.append(book_id)
        if self.skipped:
            log.info("Rebuild: %s books are up to date and skipped", self.skipped)
        return wanted

    def _count(self):
        self.checked += 1
        self.progress = self.checked / self.total
        self.message = N_('%(checked)s of %(total)s books checked, %(updated)s updated%(unanswered)s',
                          **self._counts())

    def _counts(self):
        """The numbers the status lines are written from."""
        return dict(checked=self.checked, total=self.total, updated=self.updated, covers=self.covers,
                    unanswered=self._skipped_note() + self._unanswered_note())

    def _skipped_note(self):
        """', 120 up to date skipped', or empty."""
        return str(N_(', %(skipped)s up to date skipped', skipped=self.skipped)) if self.skipped else ''

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
        if not self._store or self.book_ids is not None:
            return
        waiting = list(running.values()) + ([self._unsubmitted] if self._unsubmitted is not None else [])
        try:
            if waiting:
                self._store.save_rebuild_progress(min(waiting), self.checked, self.updated, self.covers, self.total)
            else:
                self._store.clear_rebuild_progress()
        except Exception as ex:
            log.warning("Rebuild: could not save the progress: %s", ex)

    def _tidy_authors(self, cdb):
        """Make each author the people it names first, so a lookup searches for them and a
        book whose author was junk is looked up by its title."""
        self.message = N_('Tidying authors')
        try:
            changed, removed = tidy_authors(cdb.session, calibre_path=config.get_book_path())
            self.authors_tidied = changed
            log.info("Rebuild: tidied the authors of %s books, removed %s unused authors", changed, removed)
        except Exception as ex:
            cdb.session.rollback()
            log.error("Rebuild: could not tidy authors: %s", ex)

    def _tidy_tags(self, cdb):
        """Clear out tags that are not subjects first: it takes seconds, the lookups take hours."""
        self.message = N_('Tidying tags')
        try:
            changed, removed = tidy_library_tags(cdb.session)
            log.info("Rebuild: tidied the tags of %s books, removed %s unused tags", changed, removed)
        except Exception as ex:
            cdb.session.rollback()
            log.error("Rebuild: could not tidy tags: %s", ex)

    def _clear_none_descriptions(self, cdb):
        """Clear the description "None" that an old Fetch Metadata save wrote, so the lookups
        fill it."""
        try:
            cleared = (cdb.session.query(db.Comments).filter(func.trim(db.Comments.text) == 'None')
                       .delete(synchronize_session=False))
            cdb.session.commit()
            if cleared:
                log.info("Rebuild: cleared the description \"None\" from %s books", cleared)
        except Exception as ex:
            cdb.session.rollback()
            log.error("Rebuild: could not clear \"None\" descriptions: %s", ex)

    def _cover_changed(self, cdb, book_id):
        """Record a made or centred cover so its URL and thumbnails change with it."""
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


def _changed_since(last_modified, checked_at) -> bool:
    """Whether the book changed after its lookup (checked_at, ISO 8601 UTC). Any doubt: changed."""
    try:
        checked = datetime.fromisoformat(checked_at)
    except (TypeError, ValueError):
        return True
    if last_modified is None:
        return False
    if last_modified.tzinfo is None:
        last_modified = last_modified.replace(tzinfo=timezone.utc)
    # A lookup notes itself just after the change it made
    return last_modified > checked + timedelta(seconds=5)


def _look_up(fetch, book_id, centre_covers):
    """One book's work in the pool: the lookup, then making or centring a PDF's cover.
    Returns (updated, cover changed, providers that failed to answer).

    The cover comes second so a cover the provider just set is neither replaced nor cropped:
    it no longer looks like the page render, so recentre_cover leaves it. Its paths are read
    after the lookup, which moves the book's folder when the title or first author changes."""
    from cps.metadata_helper import library_lock
    unanswered = set()
    updated = fetch(book_id, force=True, unanswered=unanswered)
    if not centre_covers:
        return updated, False, unanswered
    with library_lock:
        cdb = db.CalibreDB(expire_on_commit=False, init=True)
        try:
            cover = _cover_job(cdb, book_id)
        finally:
            cdb.session.close()
    return updated, pdf_cover.try_fix_cover(cover, book_id), unanswered


def _cover_job(cdb, book_id):
    book = cdb.session.get(db.Books, book_id)
    return pdf_cover.cover_job(book, config.get_book_path()) if book else None
