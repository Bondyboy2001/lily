# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Task that looks every book up again with the metadata providers (Settings → Metadata → Rebuild).

It first makes each author the people it names (cps/author_cleanup.py), and after each lookup gives a PDF
with no cover (or only the old "Cover not available" card) its first page (cps/pdf_cover.py). It
doesn't render a PDF that has a cover: a lookup never changes a PDF-only book's cover, and Redo PDF
covers is there to make every cover page 1 again.

A book that gets new details is kept in step like an edit is (metadata_helper does it): its folder
follows a new title or first author, and with "Write edits into book files" on, the change is
queued for the files.

How far a run has got is saved every few seconds (cwa.db), so one that was stopped, or cut short by
a restart, can be carried on from there instead of starting again: a full one stays full, and the
books it had already checked are not checked again.

A rebuild skips a book that is up to date: its last lookup matched it or found nothing, and it
hasn't changed since, and fills only what a matched book lacks. A full rebuild first copies the
library's metadata.db to /config/metadata.db.before-full-rebuild, forgets what earlier lookups
found (cwa.db) and looks every book up again, and a match replaces the book's details rather
than filling them: its date and identifiers, and its title and authors unless it
was edited by hand. A book with no match keeps what it has."""

import os
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, UTC
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from flask_babel import lazy_gettext as N_

from cps import config, db, helper, logger, pdf_cover
from cps.services.worker import (CalibreTask, STAT_FAIL, STAT_FINISH_SUCCESS, STAT_STARTED, STAT_STOPPING,
                                 STAT_WAITING)
from cps.author_cleanup import tidy_authors
from cps.ub import init_db_thread
sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from cwa_db import CWA_DB  # noqa: E402

log = logger.create()

# Books looked up at once: a lookup is mostly waiting on the providers, a few seconds a book
WORKERS = 4
# Seconds between saves of how far a run got: on the NAS a commit syncs the disk (~45 ms), and a
# restart repeats only the books since the last save
PROGRESS_EVERY = 5


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
        super().__init__(N_('Rebuilding metadata'))
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
        # Books checked above the lowest one still to do, so a carried-on run skips them
        self._done = set()
        # When the progress was last saved (time.monotonic)
        self._saved_at = 0.0

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
            if progress and progress["full"]:
                # Carrying on a full rebuild: the books still to do are replaced, not filled
                self.full = True
            if self.full and not progress and self._store:
                _backup_library()
                self._store.clear_lookup_records()
            with library_lock:
                if not progress and self.book_ids is None:
                    self._tidy_authors(cdb)
                books = cdb.session.query(db.Books.id, db.Books.last_modified).order_by(db.Books.id).all()
            book_ids = [row[0] for row in books]
            if self.book_ids is None and not self.full:
                book_ids = self._still_to_look_up(books)
            if self.book_ids is not None:
                # Those still in the library; picked here, not in SQL, as there can be thousands
                wanted = set(self.book_ids)
                book_ids = [book_id for book_id in book_ids if book_id in wanted]
            if progress:
                self._done = set(progress["done"])
                book_ids = [book_id for book_id in book_ids
                            if book_id >= progress["next_book_id"] and book_id not in self._done]
                self.checked, self.updated, self.covers = (progress[k] for k in ("checked", "updated", "covers"))
                log.info("Rebuild: carrying on after %s books, from book %s", self.checked, progress["next_book_id"])
            self.total = self.checked + len(book_ids)
            make_covers = pdf_cover.available()
            running = {}
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                for book_id in book_ids:
                    self._unsubmitted = book_id
                    while len(running) >= self.workers:
                        self._finish(cdb, running)
                    if self.stop_requested:
                        break
                    running[pool.submit(_look_up, fetch_and_apply_metadata, book_id, make_covers,
                                        self.full)] = book_id
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
            self.message = N_('Done: %(total)s books checked, %(updated)s updated, %(covers)s covers made%(unanswered)s',
                              **self._counts())
        else:
            self.message = N_('Done: %(total)s books checked, %(updated)s updated%(unanswered)s', **self._counts())
        log.info("Metadata rebuild finished: %s books checked, %s updated, %s covers made, no answer: %s",
                 self.total, self.updated, self.covers, dict(self.unanswered) or "none")
        self._handleSuccess()

    def _finish(self, cdb, running):
        """Wait for a lookup to end and count it; record a made cover."""
        from cps.metadata_helper import library_lock
        done, __ = wait(running, return_when=FIRST_COMPLETED)
        for future in done:
            book_id = running.pop(future)
            try:
                updated, cover_made, unanswered = future.result()
                if updated:
                    self.updated += 1
                self.unanswered.update(unanswered)
                if cover_made:
                    with library_lock:
                        self._cover_changed(cdb, book_id)
            except Exception as ex:
                # One book going wrong must not end a run of hours, or strand the books under way
                with library_lock:
                    cdb.session.rollback()
                log.error("Rebuild: book %s failed: %s", book_id, ex, exc_info=True)
            self._done.add(book_id)
            self._count()
        if time.monotonic() - self._saved_at >= PROGRESS_EVERY:
            self._save_progress(running)

    def _still_to_look_up(self, books):
        """The books not up to date: never looked up, failed, or changed since their lookup
        matched them, found nothing or left them as filled in by hand. The others are counted
        as skipped."""
        try:
            lookups = self._store.metadata_lookups_by_book() if self._store else {}
        except Exception as ex:
            log.warning("Rebuild: could not read the earlier lookups, so every book is looked up: %s", ex)
            return [book_id for book_id, __ in books]
        wanted = []
        for book_id, last_modified in books:
            status, checked = lookups.get(book_id, (None, None))
            if status in ("matched", "nomatch", "manual") and not _changed_since(last_modified, checked):
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
        return {'checked': self.checked, 'total': self.total, 'updated': self.updated, 'covers': self.covers,
                    'unanswered': self._skipped_note() + self._unanswered_note()}

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
        """Note the lowest book not yet checked, and the books above it already checked, for a
        later run to carry on from; nothing is kept once every book is done."""
        if not self._store or self.book_ids is not None:
            return
        self._saved_at = time.monotonic()
        waiting = list(running.values()) + ([self._unsubmitted] if self._unsubmitted is not None else [])
        try:
            if waiting:
                next_book_id = min(waiting)
                # Those below it are passed for good
                self._done = {book_id for book_id in self._done if book_id > next_book_id}
                self._store.save_rebuild_progress(next_book_id, self.checked, self.updated, self.covers, self.total,
                                                  full=self.full, done=self._done)
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

    def _cover_changed(self, cdb, book_id):
        """Record a made cover so its URL and thumbnails change with it.

        The cover bumps the book's last_modified, which a later rebuild reads as "changed
        since its lookup" and looks it up again; when it was up to date, the lookup is stamped
        again instead so it still counts as fresh."""
        cdb.session.expire_all()
        book = cdb.session.get(db.Books, book_id)
        if book is None:
            return
        before = book.last_modified
        record = None
        if self._store:
            try:
                record = self._store.get_metadata_lookup(book_id)
            except Exception as ex:
                log.debug("Rebuild: could not read the lookup of book %s: %s", book_id, ex)
        try:
            pdf_cover.mark_cover_changed(book)
            cdb.session.commit()
        except Exception as ex:
            cdb.session.rollback()
            log.error("Rebuild: could not record the new cover of book %s: %s", book_id, ex)
            return
        self.covers += 1
        helper.replace_cover_thumbnail_cache(book_id)
        if record and record["status"] in ("matched", "nomatch", "manual") and \
                not _changed_since(before, record["checked_at"]):
            try:
                self._store.save_metadata_lookup(book_id, record["status"], record["source"])
            except Exception as ex:
                log.debug("Rebuild: could not re-stamp the lookup of book %s: %s", book_id, ex)


def _changed_since(last_modified, checked_at) -> bool:
    """Whether the book changed after its lookup (checked_at, ISO 8601 UTC). Any doubt: changed."""
    try:
        checked = datetime.fromisoformat(checked_at)
    except (TypeError, ValueError):
        return True
    if last_modified is None:
        return False
    if last_modified.tzinfo is None:
        last_modified = last_modified.replace(tzinfo=UTC)
    # A lookup notes itself just after the change it made
    return last_modified > checked + timedelta(seconds=5)


def _backup_library():
    """Copy the library's metadata.db beside cwa.db before a full rebuild overwrites it; a
    failure is logged, and the rebuild goes on (the copy is there to restore the library from)."""
    target = os.path.join(os.environ.get("CWA_DB_PATH", "/config"), "metadata.db.before-full-rebuild")
    try:
        source = sqlite3.connect(os.path.join(config.config_calibre_dir, "metadata.db"), timeout=30)
        try:
            copy = sqlite3.connect(target)
            try:
                source.backup(copy)
            finally:
                copy.close()
        finally:
            source.close()
        log.info("Rebuild: copied the library to %s before the full rebuild", target)
    except Exception as ex:
        log.error("Rebuild: could not copy the library before the full rebuild: %s", ex)


def _look_up(fetch, book_id, make_covers, overwrite=False):
    """One book's work in the pool: the lookup, then making a PDF's page 1 cover when it has
    none. Returns (updated, cover changed, providers that failed to answer).

    A cover the PDF already has is kept without rendering page 1 to compare: the lookup takes no
    provider's cover for a PDF-only book, so it is still what Redo PDF covers or the import made.
    The cover's paths are read after the lookup, which moves the book's folder when the title or
    first author changes."""
    from cps.metadata_helper import library_lock
    unanswered = set()
    # Only a full rebuild overwrites; the keyword stays out otherwise (tests pass bare fetchers)
    extra = {"overwrite": True} if overwrite else {}
    updated = fetch(book_id, force=True, unanswered=unanswered, **extra)
    if not make_covers:
        return updated, False, unanswered
    with library_lock:
        cdb = db.CalibreDB(expire_on_commit=False, init=True)
        try:
            cover = _cover_job(cdb, book_id, replace=False)
        finally:
            cdb.session.close()
    return updated, pdf_cover.try_fix_cover(cover, book_id), unanswered


def _cover_job(cdb, book_id, store=None, replace=True):
    book = cdb.session.get(db.Books, book_id)
    return pdf_cover.cover_job(book, config.get_book_path(), store, replace) if book else None
