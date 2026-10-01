# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Task that looks up books without a description and queues metadata suggestions for review."""

import json
import sys
import time
from datetime import datetime, timezone

from flask_babel import lazy_gettext as N_

from cps import db, logger, ub
from cps.services.worker import CalibreTask, STAT_CANCELLED, STAT_ENDED

if '/app/calibre-web-automated/scripts/' not in sys.path:
    sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from metadata_suggestions import MIN_SCORE, fill_fields, match_score

# Hardcover has its own matching queue; Scholar searches papers, not books
EXCLUDED_PROVIDERS = frozenset({"hardcover", "scholar"})
DEFAULT_BATCH_SIZE = 25
REQUEST_DELAY_SECONDS = 1.0
NO_MATCH = "no_match"  # status of a book that was looked up and gave nothing useful


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def usable_providers():
    """Enabled providers that look books up by title and author."""
    from cps.search_metadata import cl, _get_global_provider_enabled_map
    enabled = _get_global_provider_enabled_map()
    return [p for p in cl if p.__id__ not in EXCLUDED_PROVIDERS and p.is_globally_enabled(enabled)]


class TaskSuggestMetadata(CalibreTask):
    """Looks up books that have no description, and queues provider records that would fill
    their gaps for an admin to review. Works through `batch_size` books per run."""

    def __init__(self, batch_size=DEFAULT_BATCH_SIZE, task_message=N_('Looking for metadata suggestions')):
        super(TaskSuggestMetadata, self).__init__(task_message)
        self.log = logger.create()
        self.batch_size = batch_size
        self.suggested = 0

    def _cancelled(self):
        return self.stat in (STAT_CANCELLED, STAT_ENDED)

    def _candidates(self, calibre_session):
        done = {row[0] for row in ub.session.query(ub.MetadataSuggestion.book_id).distinct()}
        query = calibre_session.query(db.Books).filter(
            ~db.Books.comments.any(db.Comments.text != "")).order_by(db.Books.id)
        books = []
        for book in query:
            if book.id not in done:
                books.append(book)
                if len(books) >= self.batch_size:
                    break
        return books

    def run(self, worker_thread):
        providers = usable_providers()
        if not providers:
            self._handleError("No metadata provider is enabled")
            return
        calibre = db.CalibreDB(expire_on_commit=False, init=True)
        try:
            books = self._candidates(calibre.session)
            if not books:
                self.log.info("Metadata suggestions: every book without a description has been looked up")
                self._handleSuccess()
                return
            for i, book in enumerate(books, 1):
                if self._cancelled():
                    return
                self._process(book, providers)
                self.progress = i / len(books)
            ub.session_commit()
        except Exception as e:
            ub.session.rollback()
            self.log.error("Metadata suggestion run failed: %s", e)
            self._handleError("Metadata suggestion run failed; see the log")
            return
        finally:
            calibre.session.close()
        self.message = N_('%(n)d suggestion(s) to review', n=self.suggested)
        self._handleSuccess()

    def _process(self, book, providers):
        authors = [a.name.replace('|', ',') for a in book.authors]
        query = " ".join([book.title] + authors[:1])
        have = {"description": "", "identifiers": {i.type: i.val for i in book.identifiers}}
        found = False
        for provider in providers:
            if self._cancelled():
                return
            try:
                records = provider.search(query) or []
            except Exception as e:
                self.log.warning("%s lookup failed for '%s': %s", provider.__id__, book.title, e)
                records = []
            time.sleep(REQUEST_DELAY_SECONDS)
            scored = [(match_score(book.title, authors, r.title, list(r.authors or [])), r) for r in records]
            scored = [(s, r) for s, r in scored if s >= MIN_SCORE]
            if not scored:
                continue
            score, record = max(scored, key=lambda sr: sr[0])
            fill = fill_fields(have, {"description": record.description, "identifiers": record.identifiers})
            if not fill:
                continue
            found = True
            self.suggested += 1
            ub.session.add(ub.MetadataSuggestion(
                book_id=book.id, book_title=book.title, book_authors=", ".join(authors),
                provider=provider.__id__, record_title=record.title,
                record_authors=", ".join(record.authors or []), record_url=record.url or "",
                score=score, fill=json.dumps(fill), created_at=_now()))
        if not found:
            ub.session.add(ub.MetadataSuggestion(
                book_id=book.id, book_title=book.title, book_authors=", ".join(authors), provider="-",
                record_title="", record_authors="", score=0.0, fill="{}", status=NO_MATCH, created_at=_now()))

    @property
    def name(self):
        return "Metadata Suggestions"

    @property
    def is_cancellable(self):
        return True
