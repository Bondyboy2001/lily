# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Task that looks every book up again with the metadata providers (Import & Metadata → Rebuild).

It first clears tags that are not subjects (ISBNs, publisher lines, shop listing scraps).

A book that gets new details is kept in step like an edit is: its folder follows a new title or
first author, and with "Write edits into book files" on, the change is queued for the files."""

import json
import os
import time
from datetime import datetime

from flask_babel import lazy_gettext as N_

from cps import config, db, helper, logger
from cps.services.worker import CalibreTask, STAT_CANCELLED, STAT_ENDED
from cps.tag_cleanup import tidy_library_tags
from cps.ub import init_db_thread

log = logger.create()

# Pause between books so a whole library doesn't trip the providers' rate limits
PAUSE_SECONDS = 1.0
# The metadata-change-detector service hands each log here to cover_enforcer.py
CHANGE_LOGS_DIR = "/app/calibre-web-automated/metadata_change_logs"
# The formats cover_enforcer.py can write metadata into
ENFORCED_FORMATS = {"EPUB", "AZW3"}


class TaskRebuildMetadata(CalibreTask):
    def __init__(self, pause=PAUSE_SECONDS):
        super(TaskRebuildMetadata, self).__init__(N_('Rebuilding metadata'))
        self.pause = pause
        self.checked = 0
        self.updated = 0
        self.write_files = False

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
        # A session of this thread's own: the rebuild runs beside web requests
        cdb = db.CalibreDB(expire_on_commit=False, init=True)
        try:
            self.write_files = bool(_cwa_settings().get('auto_metadata_enforcement'))
            self._tidy_tags(cdb)
            book_ids = [row[0] for row in cdb.session.query(db.Books.id).order_by(db.Books.id).all()]
            total = len(book_ids)
            for book_id in book_ids:
                if self._stopped():
                    self.message = N_('Stopped: %(checked)s of %(total)s books checked, %(updated)s updated',
                                      checked=self.checked, total=total, updated=self.updated)
                    return
                before = _title_and_author(cdb, book_id)
                if before and fetch_and_apply_metadata(book_id, force=True):
                    self.updated += 1
                    self._follow_up(cdb, book_id, before)
                self.checked += 1
                self.progress = self.checked / total
                self.message = N_('%(checked)s of %(total)s books checked, %(updated)s updated',
                                  checked=self.checked, total=total, updated=self.updated)
                if self.checked < total and self.pause:
                    time.sleep(self.pause)
        finally:
            cdb.session.close()
            if self.updated:
                try:
                    from cps.duplicate_index import mark_duplicate_index_pending
                    mark_duplicate_index_pending("metadata rebuild")
                except Exception as ex:
                    log.debug("Could not mark the duplicate index pending: %s", ex)
        self.message = N_('Done: %(total)s books checked, %(updated)s updated', total=total, updated=self.updated)
        log.info("Metadata rebuild finished: %s books checked, %s updated", total, self.updated)
        self._handleSuccess()

    def _tidy_tags(self, cdb):
        """Clear out tags that are not subjects first: it takes seconds, the lookups take hours."""
        self.message = N_('Tidying tags')
        try:
            changed, removed = tidy_library_tags(cdb.session)
            log.info("Rebuild: tidied the tags of %s books, removed %s unused tags", changed, removed)
        except Exception as ex:
            cdb.session.rollback()
            log.error("Rebuild: could not tidy tags: %s", ex)

    def _follow_up(self, cdb, book_id, before):
        """Move the folder after a new title or first author, then queue the file write."""
        cdb.session.expire_all()
        book = cdb.session.get(db.Books, book_id)
        if book is None:
            return
        after = _title_and_author(cdb, book_id)
        if after != before:
            try:
                error = helper.update_dir_structure(book_id, config.get_book_path(), after[1], book=book)
                if error:
                    raise RuntimeError(error)
                cdb.session.commit()
            except Exception as ex:
                cdb.session.rollback()
                log.error("Rebuild: could not move the folder of book %s: %s", book_id, ex)
        if self.write_files and {d.format.upper() for d in book.data} & ENFORCED_FORMATS:
            _write_change_log(book)


def _cwa_settings():
    from cwa_db import CWA_DB
    return CWA_DB().get_cwa_settings()


def _title_and_author(cdb, book_id):
    """(title, first author) as calibre orders them, or None for a book that has gone."""
    book = cdb.session.get(db.Books, book_id)
    if book is None:
        return None
    authors = cdb.order_authors([book]) if book.authors else []
    return book.title, authors[0].name if authors else None


def _write_change_log(book):
    """The same log an edit writes; cover_enforcer.py reads the book's metadata back from the library."""
    payload = {
        'title': book.title,
        'authors': ' & '.join(author.name for author in book.authors),
        '_cwa_meta': {'source': 'metadata rebuild', 'timestamp': datetime.now().isoformat()},
    }
    path = os.path.join(CHANGE_LOGS_DIR, "%s-%s.json" % (datetime.now().strftime("%Y%m%d%H%M%S"), book.id))
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=4, ensure_ascii=False)
    except OSError as ex:
        log.error("Rebuild: could not queue the file write for book %s: %s", book.id, ex)
