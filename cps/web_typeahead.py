# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""JSON lookups that feed the typeahead fields (authors, tags, titles).

Routes are attached to the web blueprint; web.py imports this module at its end."""

import json

from flask import request, url_for
from sqlalchemy.orm import lazyload, selectinload
from sqlalchemy.sql.expression import and_, func, not_, or_

from . import db
from . import calibre_db
from .helper import tags_filters
from .usermanagement import login_required_if_no_ano
from .string_helper import strip_whitespaces

from .web import web


# ################################### Typeahead ##################################################################


@web.route("/get_authors_json", methods=['GET'])
@login_required_if_no_ano
def get_authors_json():
    return calibre_db.get_typeahead(db.Authors, request.args.get('q'), ('|', ','))


@web.route("/get_tags_json", methods=['GET'])
@login_required_if_no_ano
def get_tags_json():
    return calibre_db.get_typeahead(db.Tags, request.args.get('q'), tag_filter=tags_filters())


@web.route("/get_book_titles_json", methods=['GET'])
@login_required_if_no_ano
def get_book_titles_json():
    # Suggestions for the top bar search box: books whose title or authors hold every word of
    # the query (split like the full search, db.search_words), folding case and accents.
    # common_filters() keeps hidden books out of the suggestions, exactly as the lists do.
    query = strip_whitespaces(request.args.get('q') or '')
    if len(query) < 2:
        return json.dumps([])

    # lower() is the accent-folding lcase every connection registers (db._connection_setup)
    def word_match(word):
        pattern = db.like_pattern(db.lcase(word))
        return or_(func.lower(db.Books.title).like(pattern, escape="\\"),
                   db.Books.authors.any(func.lower(db.Authors.name).like(pattern, escape="\\")))

    # A suggestion shows the title, authors and cover; leave the other relationships
    # (tags, comments, identifiers, ...) unloaded instead of one query each per keystroke.
    books = calibre_db.session.query(db.Books) \
        .options(lazyload('*'), selectinload(db.Books.authors)) \
        .filter(calibre_db.common_filters()) \
        .filter(and_(*[word_match(word) for word in db.search_words(query)])) \
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
    q = calibre_db.session.query(db.Books).filter(calibre_db.common_filters())
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
