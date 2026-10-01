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
# search, the in-browser readers). Everything else runs without 'unsafe-eval'.
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


@web.route("/get_book_titles_json", methods=['GET'])
@login_required_if_no_ano
def get_book_titles_json():
    # Suggestions for the top bar search box: books whose title or author matches.
    # common_filters() keeps hidden/archived books out of the suggestions, exactly as the lists do.
    query = strip_whitespaces(request.args.get('q') or '')
    if len(query) < 2:
        return json.dumps([])
    pattern = "%" + query + "%"
    books = calibre_db.session.query(db.Books) \
        .filter(calibre_db.common_filters()) \
        .filter(or_(db.Books.title.ilike(pattern),
                    db.Books.authors.any(db.Authors.name.ilike(pattern)))) \
        .order_by(func.lower(db.Books.title)).limit(8).all()
    # Each suggestion carries its small cover thumbnail, cache-busted like the library grid.
    return json.dumps([dict(name=book.title,
                            id=book.id,
                            url=url_for('web.show_book', book_id=book.id),
                            author=" & ".join(a.name.replace("|", ",") for a in book.authors),
                            cover=url_for('web.get_cover', book_id=book.id, resolution='sm',
                                          c=str(int(book.last_modified.timestamp()))))
                       for book in books])


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
