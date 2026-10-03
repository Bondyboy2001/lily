# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Loads the metadata providers and serves the metadata search used when editing a book."""

import concurrent.futures
import json
import sys
from dataclasses import asdict

from flask import Blueprint, request, url_for, make_response, jsonify, copy_current_request_context
from flask_babel import get_locale

from cps.metadata_provider.google import Google
from cps.metadata_provider.hardcover import Hardcover
from cps.metadata_provider.openlibrary import OpenLibrary
from cps.metadata_provider.scholar import google_scholar
from cps.services.Metadata import ProviderBusy
from cps.services.identifiers import identifier_pages, normalise_identifiers, parse_identifier
from . import logger
from .usermanagement import user_login_required


meta = Blueprint("metadata", __name__)

log = logger.create()

# Every provider, in the order an import's lookups try them: books, Hardcover first, then papers
# (Google Books is asked last of all: metadata_helper._lookup_order)
cl = [Google(), Hardcover(), OpenLibrary(), google_scholar()]


def _providers_to_ask(typed, providers=cl):
    """The providers to search: for a typed identifier, those that can look up its
    type, otherwise all of them. One that can't be asked (Hardcover without a token) is left out."""
    providers = [c for c in providers if c.available()]
    if typed:
        return [c for c in providers if c.identifier_types & typed.keys()]
    return providers


@meta.route("/metadata/provider")
@user_login_required
def metadata_provider():
    """The providers to search for `query`, as {id, name}."""
    ask = _providers_to_ask(parse_identifier(request.args.get("query")))
    return make_response(jsonify([{"id": c.__id__, "name": c.__name__} for c in ask]))


# A provider that hasn't answered by then is reported as timed out
PROVIDER_TIMEOUT = 20
try:
    # The server runs gevent without monkey-patching: waiting on plain threads
    # would stall every other request, so the lookups use gevent's thread pool
    import gevent
    from gevent.threadpool import ThreadPoolExecutor
except ImportError:
    gevent = None
    from concurrent.futures import ThreadPoolExecutor
# Shared by all searches; each asks one provider for at most two lookups
_executor = ThreadPoolExecutor(max_workers=10)


def _wait(futures, timeout):
    """The futures that finished within the timeout, without blocking the server."""
    if gevent:
        return set(gevent.wait(futures, timeout=timeout))
    return concurrent.futures.wait(futures, timeout=timeout).done


def _form_identifiers(raw_json):
    """The edit form's identifiers, lower-case type -> value, for exact lookups."""
    try:
        raw = json.loads(raw_json) if raw_json else {}
    except (TypeError, ValueError):
        raw = {}
    return normalise_identifiers(raw) if isinstance(raw, dict) else {}


def _file_identifiers(book_id, title):
    """The arXiv id or DOI the book's title or its PDF's first page names, and that
    page's text, so Fetch metadata finds a paper whose title is still its file name."""
    from cps import calibre_db
    from cps.metadata_helper import find_paper_identifiers, pdf_first_page_text
    try:
        book = calibre_db.get_filtered_book(int(book_id))
    except (TypeError, ValueError):
        return {}, ""
    if not book:
        return {}, ""
    page_text = pdf_first_page_text(book)
    return find_paper_identifiers(title or book.title, page_text), page_text


def _pinned(record, file_ids, form_ids, page_text, authors=()):
    """Whether a record an identifier lookup found stays pinned first as an exact
    match. One by another author is not: the book's ISBN may be one an earlier
    lookup read off a series list of other books. A DOI read off the PDF's first
    page may be a citation, so its record needs its title on that page, unless the
    book's own identifiers name it too."""
    from cps.metadata_helper import other_author, title_on_page
    if other_author(authors, record):
        return False
    doi = file_ids.get("doi", "").lower()
    if not doi or doi == form_ids.get("doi", "").lower():
        return True
    if (getattr(record, "identifiers", None) or {}).get("doi", "").lower() != doi:
        return True
    return title_on_page(record.title, page_text)


def _scorer(form, query=""):
    """How well a record matches the book being edited (title and authors from the
    edit form) or the typed text as its title, scored by
    scripts/metadata_suggestions.py. The book's title is often a file name, so the
    typed text counts as much; how closely the record's title matches it breaks
    ties, since an author-less score is capped."""
    if '/app/calibre-web-automated/scripts/' not in sys.path:
        sys.path.insert(1, '/app/calibre-web-automated/scripts/')
    from metadata_suggestions import match_score, title_forms, title_similarity
    title = form.get("title") or ""
    authors = [a.strip() for a in (form.get("authors") or "").split("&") if a.strip()]

    def score(record):
        rec_authors = record.authors or []
        # With or without its subtitle, whichever is closer: an import accepts either
        best, close = 0.0, 0.0
        for rec_title in title_forms(record):
            best = max(best, match_score(title, authors, rec_title, rec_authors))
            if query:
                best = max(best, match_score(query, authors, rec_title, rec_authors))
            close = max(close, title_similarity(query or title, rec_title))
        return 0.99 * best + 0.01 * close
    return score


def _run_search(provider, query, identifiers):
    """The provider's identifier lookup and text search, run at once. Returns
    (record, exact) pairs, exact ones first, and the status: ok, busy (it answered "too many
    requests"), error or timeout."""
    static_cover = url_for("static", filename="generic_cover.svg")
    locale = get_locale()
    jobs = []
    if identifiers:
        jobs.append((_executor.submit(copy_current_request_context(provider.search_identifiers),
                                      identifiers, static_cover, locale), True))
    if query:
        jobs.append((_executor.submit(copy_current_request_context(provider.search),
                                      query, static_cover, locale), False))
    done = _wait([f for f, _ in jobs], PROVIDER_TIMEOUT)
    records, status = [], "ok"
    for future, exact in jobs:
        if future not in done:
            # Drops it if still queued; a running one ends at its own request timeout
            future.cancel()
            log.warning("Metadata provider %s timed out", provider.__class__.__name__)
            status = "timeout"
            continue
        try:
            records += [(r, exact) for r in future.result() or [] if r]
        except Exception as exc:
            log.warning("Metadata provider %s failed: %s", provider.__class__.__name__, exc)
            busy = isinstance(exc, ProviderBusy) or "429" in str(exc)
            if status != "timeout":
                status = "busy" if busy else "error"
    return records, status


@meta.route("/metadata/search", methods=["POST"])
@user_login_required
def metadata_search():
    """Searches one provider (the edit page asks each separately so results show as
    they arrive). An ISBN, DOI, arXiv id or hardcover-id:N typed as the query is
    looked up exactly instead of searched as text; otherwise the book's own
    identifiers, and an arXiv id or DOI on its PDF's first page, are looked up
    alongside the text search."""
    form = request.form.to_dict()
    query = (form.get("query") or "").strip()
    typed = parse_identifier(query)
    provider = next((c for c in _providers_to_ask(typed)
                     if c.__id__ == form.get("provider")), None)
    if provider is None or not query:
        # Not one to search for this query (the page's provider list is out of date)
        return make_response(jsonify({"results": [], "status": "skipped"}))
    form_ids = _form_identifiers(form.get("identifiers"))
    file_ids, page_text = ({}, "") if typed else _file_identifiers(form.get("book_id"), form.get("title"))
    records, status = _run_search(provider, "" if typed else query,
                                  typed or {**file_ids, **form_ids})
    score = _scorer(form, "" if typed else query)
    # An identifier typed in is the one wanted, whoever the form says wrote the book
    authors = [] if typed else [a.strip() for a in (form.get("authors") or "").split("&") if a.strip()]
    data, seen = [], set()
    for record, exact in records:
        key = (record.source.description, str(record.id))
        if key in seen:
            continue
        seen.add(key)
        item = asdict(record)
        item["exact_match"] = exact and _pinned(record, file_ids, form_ids, page_text, authors)
        item["score"] = score(record)
        # So the card can link each identifier to the page it is about
        item["identifier_urls"] = identifier_pages(record.identifiers)
        data.append(item)
    # The dialog names a provider that didn't answer, and says when a key would help
    return make_response(jsonify({"results": data, "status": status, "name": provider.__name__,
                                  "missing_key": provider.missing_key()}))
