# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Loads the metadata providers and serves the metadata search used when editing a book."""

import concurrent.futures
import importlib
import inspect
import json
import os
import sys

from flask import Blueprint, request, url_for, make_response, jsonify, copy_current_request_context
from flask_babel import get_locale

from cps.services.Metadata import Metadata
from cps.services.identifiers import normalise_identifiers, parse_identifier
from . import constants, logger, web_server
from .usermanagement import user_login_required


meta = Blueprint("metadata", __name__)

log = logger.create()

try:
    from dataclasses import asdict
except ImportError:
    log.info('*** "dataclasses" is needed for calibre-web automated to run. Please install it using pip: "pip install dataclasses" ***')
    print('*** "dataclasses" is needed for calibre-web automated to run. Please install it using pip: "pip install dataclasses" ***')
    web_server.stop(True)
    sys.exit(6)

new_list = list()
meta_dir = os.path.join(constants.BASE_DIR, "cps", "metadata_provider")
modules = os.listdir(os.path.join(constants.BASE_DIR, "cps", "metadata_provider"))
for f in modules:
    if os.path.isfile(os.path.join(meta_dir, f)) and not f.endswith("__init__.py"):
        a = os.path.basename(f)[:-3]
        try:
            importlib.import_module("cps.metadata_provider." + a)
            new_list.append(a)
        except (IndentationError, SyntaxError) as e:
            log.error("Syntax error for metadata source: {} - {}".format(a, e))
        except ImportError as e:
            log.debug("Import error for metadata source: {} - {}".format(a, e))


def list_classes(provider_list):
    classes = list()
    for element in provider_list:
        for name, obj in inspect.getmembers(
            sys.modules["cps.metadata_provider." + element]
        ):
            if (
                inspect.isclass(obj)
                and name != "Metadata"
                and issubclass(obj, Metadata)
            ):
                classes.append(obj())
    return classes


cl = list_classes(new_list)
# Alphabetises the list of Metadata providers
cl.sort(key=lambda x: x.__class__.__name__)


# Helper to load global provider enablement map from CWA settings
def _get_global_provider_enabled_map() -> dict:
    try:
        # Import here to avoid circular import issues and keep startup fast
        scripts = '/app/calibre-web-automated/scripts/'
        if scripts not in sys.path:
            sys.path.insert(1, scripts)
        from cwa_db import CWA_DB  # type: ignore
        with CWA_DB() as cwa_db:
            settings = cwa_db.cwa_settings

        if not settings:
            log.warning("Could not get CWA settings for provider enabled map")
            return {}

        from cps.cwa_functions import parse_metadata_providers_enabled
        return parse_metadata_providers_enabled(
            settings.get('metadata_providers_enabled', '{}')
        )
    except Exception as e:
        # On any failure, treat as all enabled (empty dict = all default to enabled)
        log.warning(f"Error loading provider enabled map: {e}")
        return {}


def _enabled_providers():
    """The providers the admin hasn't switched off."""
    global_enabled = _get_global_provider_enabled_map()
    return [c for c in cl if c.is_globally_enabled(global_enabled)]


def _providers_to_ask(typed, enabled):
    """The providers to search: for a typed identifier, those that can look up its
    type, otherwise every enabled one."""
    if typed:
        return [c for c in enabled if c.identifier_types & typed.keys()]
    return list(enabled)


@meta.route("/metadata/provider")
@user_login_required
def metadata_provider():
    """The ids of the providers to search for `query`."""
    ask = _providers_to_ask(parse_identifier(request.args.get("query")), _enabled_providers())
    return make_response(jsonify([c.__id__ for c in ask]))


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


def _scorer(form, query=""):
    """How well a record matches the book being edited (title and authors from the
    edit form) or the typed text as its title, scored by
    scripts/metadata_suggestions.py. The book's title is often a file name, so the
    typed text counts as much; how closely the record's title matches it breaks
    ties, since an author-less score is capped."""
    import sys
    if '/app/calibre-web-automated/scripts/' not in sys.path:
        sys.path.insert(1, '/app/calibre-web-automated/scripts/')
    from metadata_suggestions import match_score, title_similarity
    title = form.get("title") or ""
    authors = [a.strip() for a in (form.get("authors") or "").split("&") if a.strip()]

    def score(record):
        rec_title, rec_authors = record.title or "", record.authors or []
        best = match_score(title, authors, rec_title, rec_authors)
        if query:
            best = max(best, match_score(query, authors, rec_title, rec_authors))
        return 0.99 * best + 0.01 * title_similarity(query or title, rec_title)
    return score


def _run_search(provider, query, identifiers):
    """The provider's identifier lookup and text search, run at once. Returns
    (record, exact) pairs, exact ones first, and the status: ok, error or timeout."""
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
            status = "error"
    return records, status


@meta.route("/metadata/search", methods=["POST"])
@user_login_required
def metadata_search():
    """Searches one provider (the edit page asks each separately so results show as
    they arrive). An ISBN, DOI, arXiv id or hardcover-id:N typed as the query is
    looked up exactly instead of searched as text; otherwise the book's own
    identifiers are looked up alongside the text search."""
    form = request.form.to_dict()
    query = (form.get("query") or "").strip()
    typed = parse_identifier(query)
    provider = next((c for c in _providers_to_ask(typed, _enabled_providers())
                     if c.__id__ == form.get("provider")), None)
    if provider is None or not query:
        # Not one to search for this query (the page's provider list is out of date)
        return make_response(jsonify({"results": [], "status": "skipped"}))
    records, status = _run_search(provider, "" if typed else query,
                                  typed or _form_identifiers(form.get("identifiers")))
    score = _scorer(form, "" if typed else query)
    data, seen = [], set()
    for record, exact in records:
        key = (record.source.description, str(record.id))
        if key in seen:
            continue
        seen.add(key)
        item = asdict(record)
        item["exact_match"] = exact
        item["score"] = score(record)
        data.append(item)
    return make_response(jsonify({"results": data, "status": status}))
