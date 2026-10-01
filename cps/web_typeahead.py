# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""JSON lookups that feed the typeahead fields (authors, publishers, tags, series, languages, titles).

Routes are attached to the web blueprint; web.py imports this module at its end."""

import json
import importlib

from flask import request, url_for
from flask_babel import get_locale
from sqlalchemy.sql.expression import func, not_, or_

from . import isoLanguages
from . import db, config, app
from . import calibre_db
from .helper import tags_filters
from .usermanagement import login_required_if_no_ano
from .string_helper import strip_whitespaces

# CWA Imports
import time

import sys
sys.path.insert(1, '/app/calibre-web-automated/scripts/')


try:
    from natsort import natsorted as sort
except ImportError:
    sort = sorted  # Just use regular sort then, may cause issues with badly named pages in cbz/cbr files


sql_version = importlib.metadata.version("sqlalchemy")
sqlalchemy_version2 = ([int(x) for x in sql_version.split('.')] >= [2, 0, 0])

_start_time = time.time()

# Pages whose scripts build functions from strings (underscore templates in the metadata
# search, the djvu and unrar reader engines). Everything else runs without 'unsafe-eval'.
_EVAL_ENDPOINTS = frozenset({"web.read_book", "edit-book.show_edit_book"})


@app.after_request
def add_security_headers(resp):
    default_src = ([host.strip() for host in config.config_trustedhosts.split(',') if host] +
                   ["'self'", "'unsafe-inline'"])
    if request.endpoint in _EVAL_ENDPOINTS:
        default_src.append("'unsafe-eval'")
    csp = "default-src " + ' '.join(default_src)
    if request.endpoint == "web.read_book" and config.config_use_google_drive:
        csp +=" blob: "
    csp += "; font-src 'self' data:"
    if request.endpoint == "web.read_book":
        csp += " blob: "
    csp += "; img-src 'self'"
    if request.endpoint == "admin.hardcover_review_matches":
        csp += " https:"
    csp += " data:"
    if request.endpoint == "edit-book.show_edit_book" or config.config_use_google_drive:
        csp += " *"
    if request.endpoint == "web.read_book":
        csp += " blob: ; style-src-elem 'self' blob: 'unsafe-inline'"
    csp += "; object-src 'none';"
    resp.headers['Content-Security-Policy'] = csp
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    resp.headers['Referrer-Policy'] = 'same-origin'
    resp.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return resp


from .web import web


# ################################### Typeahead ##################################################################


@web.route("/get_authors_json", methods=['GET'])
@login_required_if_no_ano
def get_authors_json():
    return calibre_db.get_typeahead(db.Authors, request.args.get('q'), ('|', ','))


@web.route("/get_publishers_json", methods=['GET'])
@login_required_if_no_ano
def get_publishers_json():
    return calibre_db.get_typeahead(db.Publishers, request.args.get('q'), ('|', ','))


@web.route("/get_tags_json", methods=['GET'])
@login_required_if_no_ano
def get_tags_json():
    return calibre_db.get_typeahead(db.Tags, request.args.get('q'), tag_filter=tags_filters())


@web.route("/get_series_json", methods=['GET'])
@login_required_if_no_ano
def get_series_json():
    return calibre_db.get_typeahead(db.Series, request.args.get('q'))


@web.route("/get_languages_json", methods=['GET'])
@login_required_if_no_ano
def get_languages_json():
    query = (request.args.get('q') or '').lower()
    language_names = isoLanguages.get_language_names(get_locale())
    entries_start = [s for key, s in language_names.items() if s.lower().startswith(query.lower())]
    if len(entries_start) < 5:
        entries = [s for key, s in language_names.items() if query in s.lower()]
        entries_start.extend(entries[0:(5 - len(entries_start))])
        entries_start = list(set(entries_start))
    json_dumps = json.dumps([dict(name=r) for r in entries_start[0:5]])
    return json_dumps


SUGGEST_MIN_LENGTH = 2
SUGGEST_BOOKS = 8
SUGGEST_AUTHORS = 4


def _like_pattern(query):
    """%query% for LIKE, with the user's own % and _ matched literally."""
    return "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@web.route("/get_book_titles_json", methods=['GET'])
@login_required_if_no_ano
def get_book_titles_json():
    """Suggestions for the top bar search box: up to 8 books whose title or author matches, then
    up to 4 authors. Each item has a `type` ("book" or "author") and the `url` picking it opens.

    common_filters() keeps hidden/archived books out, exactly as the lists do, and an author is
    only offered when one of their books is visible. Both are small LIMITed queries of their own,
    so typing never runs the full search."""
    query = strip_whitespaces(request.args.get('q') or '')
    if len(query) < SUGGEST_MIN_LENGTH:
        return json.dumps([])
    pattern = _like_pattern(query)
    books = calibre_db.session.query(db.Books) \
        .filter(calibre_db.common_filters()) \
        .filter(or_(db.Books.title.ilike(pattern, escape="\\"),
                    db.Books.authors.any(db.Authors.name.ilike(pattern, escape="\\")))) \
        .order_by(db.Books.sort.collate('NOCASE')).limit(SUGGEST_BOOKS).all()
    # Each book carries its small cover thumbnail, cache-busted like the library grid.
    items = [dict(type="book", id=book.id, name=book.title,
                  url=url_for('web.show_book', book_id=book.id),
                  author=" & ".join(a.name.replace("|", ",") for a in book.authors),
                  cover=url_for('web.get_cover', book_id=book.id, resolution='sm',
                                c=str(int(book.last_modified.timestamp()))))
             for book in books]
    starts = db.Authors.name.ilike(pattern[1:], escape="\\")
    authors = calibre_db.session.query(db.Authors.id, db.Authors.name) \
        .filter(or_(db.Authors.name.ilike(pattern, escape="\\"), db.Authors.sort.ilike(pattern, escape="\\"))) \
        .filter(db.Authors.books.any(calibre_db.common_filters())) \
        .order_by(starts.desc(), db.Authors.sort.collate('NOCASE')).limit(SUGGEST_AUTHORS).all()
    items += [dict(type="author", id=author_id, name=name.replace("|", ","),
                   url=url_for('web.books_list', data='author', sort_param='stored', book_id=author_id))
              for author_id, name in authors]
    return json.dumps(items)


@web.route("/get_matching_tags", methods=['GET'])
@login_required_if_no_ano
def get_matching_tags():
    tag_dict = {'tags': []}
    q = calibre_db.session.query(db.Books).filter(calibre_db.common_filters(True))
    calibre_db.create_functions()
    author_input = request.args.get('authors') or ''
    title_input = request.args.get('title') or ''
    include_tag_inputs = request.args.getlist('include_tag') or ''
    exclude_tag_inputs = request.args.getlist('exclude_tag') or ''
    q = q.filter(db.Books.authors.any(func.lower(db.Authors.name).ilike("%" + author_input + "%")),
                 func.lower(db.Books.title).ilike("%" + title_input + "%"))
    if len(include_tag_inputs) > 0:
        for tag in include_tag_inputs:
            q = q.filter(db.Books.tags.any(db.Tags.id == tag))
    if len(exclude_tag_inputs) > 0:
        for tag in exclude_tag_inputs:
            q = q.filter(not_(db.Books.tags.any(db.Tags.id == tag)))
    for book in q:
        for tag in book.tags:
            if tag.id not in tag_dict['tags']:
                tag_dict['tags'].append(tag.id)
    json_dumps = json.dumps(tag_dict)
    return json_dumps
