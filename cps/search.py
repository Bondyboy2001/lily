# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Simple and advanced search over the library."""

import json
from datetime import datetime

from flask import Blueprint, request, redirect, url_for, flash
from flask import session as flask_session
from .cw_login import current_user
from flask_babel import format_date
from flask_babel import gettext as _
from sqlalchemy.sql.expression import func, not_, and_, or_, text, true
from sqlalchemy.sql.functions import coalesce

from . import logger, db, calibre_db, config, ub
from .string_helper import strip_whitespaces
from .usermanagement import login_required_if_no_ano
from .render_template import render_title_template
from .pagination import Pagination


search = Blueprint('search', __name__)

log = logger.create()


@search.route("/search", methods=["GET"])
@login_required_if_no_ano
def simple_search():
    term = request.args.get("query")
    if term:
        # A new search starts best match first; another sort can be picked on the results
        return redirect(url_for('web.books_list', data="search", sort_param='relevance', query=term.strip()))
    else:
        return render_title_template('search.html',
                                     searchterm="",
                                     result_count=0,
                                     title=_("Search"),
                                     page="search")


@search.route("/advsearch", methods=['POST'])
@login_required_if_no_ano
def advanced_search():
    values = dict(request.form)
    params = ['include_tag', 'exclude_tag', 'include_shelf', 'exclude_shelf',
              'include_extension', 'exclude_extension']
    for param in params:
        values[param] = list(request.form.getlist(param))
    flask_session['query'] = json.dumps(values)
    return redirect(url_for('web.books_list', data="advsearch", sort_param='stored', query=""))


@search.route("/advsearch", methods=['GET'])
@login_required_if_no_ano
def advanced_search_form():
    # Build custom columns names
    cc = calibre_db.get_cc_columns(config, filter_config_custom_read=True)
    return render_prepare_search_form(cc)


def adv_search_custom_columns(cc, term, q):
    for c in cc:
        if c.datatype == "datetime":
            custom_start = term.get('custom_column_' + str(c.id) + '_start')
            custom_end = term.get('custom_column_' + str(c.id) + '_end')
            if custom_start:
                q = q.filter(getattr(db.Books, 'custom_column_' + str(c.id)).any(
                    func.datetime(db.cc_classes[c.id].value) >= func.datetime(custom_start)))
            if custom_end:
                q = q.filter(getattr(db.Books, 'custom_column_' + str(c.id)).any(
                    func.datetime(db.cc_classes[c.id].value) <= func.datetime(custom_end)))
        elif c.datatype in ["int", "float"]:
            custom_low = term.get('custom_column_' + str(c.id) + '_low')
            custom_high = term.get('custom_column_' + str(c.id) + '_high')
            if custom_low:
                q = q.filter(getattr(db.Books, 'custom_column_' + str(c.id)).any(
                    db.cc_classes[c.id].value >= custom_low))
            if custom_high:
                q = q.filter(getattr(db.Books, 'custom_column_' + str(c.id)).any(
                    db.cc_classes[c.id].value <= custom_high))
        else:
            custom_query = term.get('custom_column_' + str(c.id))
            if c.datatype == 'bool':
                if custom_query != "Any":
                    if custom_query == "":
                        q = q.filter(~getattr(db.Books, 'custom_column_' + str(c.id)).
                                     any(db.cc_classes[c.id].value >= 0))
                    else:
                        q = q.filter(getattr(db.Books, 'custom_column_' + str(c.id)).any(
                            db.cc_classes[c.id].value == bool(custom_query == "True")))
            elif custom_query != '' and custom_query is not None:
                if c.datatype == 'rating':
                    q = q.filter(getattr(db.Books, 'custom_column_' + str(c.id)).any(
                        db.cc_classes[c.id].value == int(float(custom_query) * 2)))
                else:
                    q = q.filter(getattr(db.Books, 'custom_column_' + str(c.id)).any(
                        func.lower(db.cc_classes[c.id].value).ilike("%" + custom_query + "%")))
    return q


def adv_search_read_status(read_status):
    if not config.config_read_column:
        if read_status == "True":
            db_filter = and_(ub.ReadBook.user_id == int(current_user.id),
                             ub.ReadBook.read_status == ub.ReadBook.STATUS_FINISHED)
        else:
            db_filter = coalesce(ub.ReadBook.read_status, 0) != ub.ReadBook.STATUS_FINISHED
    else:
        try:
            if read_status == "":
                db_filter = coalesce(db.cc_classes[config.config_read_column].value, 2) == 2
            else:
                db_filter = db.cc_classes[config.config_read_column].value == bool(read_status == "True")
        except (KeyError, AttributeError, IndexError):
            log.error("Custom Column No.{} does not exist in calibre database".format(config.config_read_column))
            flash(_("Custom column %(column)d is missing from your library, so read status can't be filtered.",
                    column=config.config_read_column),
                  category="error")
            return true()
    return db_filter


def adv_search_extension(q, include_extension_inputs, exclude_extension_inputs):
    for extension in include_extension_inputs:
        q = q.filter(db.Books.data.any(db.Data.format == extension))
    for extension in exclude_extension_inputs:
        q = q.filter(not_(db.Books.data.any(db.Data.format == extension)))
    return q


def adv_search_tag(q, include_tag_inputs, exclude_tag_inputs):
    for tag in include_tag_inputs:
        q = q.filter(db.Books.tags.any(db.Tags.id == tag))
    for tag in exclude_tag_inputs:
        q = q.filter(not_(db.Books.tags.any(db.Tags.id == tag)))
    return q


def adv_search_shelf(q, include_shelf_inputs, exclude_shelf_inputs):
    q = q.outerjoin(ub.BookShelf, db.Books.id == ub.BookShelf.book_id)\
        .filter(or_(ub.BookShelf.shelf == None, ub.BookShelf.shelf.notin_(exclude_shelf_inputs)))
    if len(include_shelf_inputs) > 0:
        q = q.filter(ub.BookShelf.shelf.in_(include_shelf_inputs))
    return q

def extend_search_term(searchterm, author_name, book_title, pub_start, pub_end, tags, read_status):
    searchterm.extend((author_name.replace('|', ','), book_title))
    if pub_start:
        try:
            searchterm.extend([_("Published after ") +
                               format_date(datetime.strptime(pub_start, "%Y-%m-%d"), format='medium')])
        except ValueError:
            pub_start = ""
    if pub_end:
        try:
            searchterm.extend([_("Published before ") +
                               format_date(datetime.strptime(pub_end, "%Y-%m-%d"), format='medium')])
        except ValueError:
            pub_end = ""
    elements = {'tag': db.Tags, 'shelf': ub.Shelf}
    for key, db_element in elements.items():
        tag_names = calibre_db.session.query(db_element).filter(db_element.id.in_(tags['include_' + key])).all()
        searchterm.extend(tag.name for tag in tag_names)
        tag_names = calibre_db.session.query(db_element).filter(db_element.id.in_(tags['exclude_' + key])).all()
        searchterm.extend(tag.name for tag in tag_names)
    if read_status != "Any":
        searchterm.extend([_("Finished") if read_status == "True" else _("Not finished")])
    searchterm.extend(ext for ext in tags['include_extension'])
    searchterm.extend(ext for ext in tags['exclude_extension'])
    # handle custom columns
    return " + ".join(filter(None, searchterm)), pub_start, pub_end


def render_adv_search_results(term, offset=None, order=None, limit=None):
    sort = order[0] if order else [db.Books.sort]
    pagination = None

    cc = calibre_db.get_cc_columns(config, filter_config_custom_read=True)
    calibre_db.create_functions()
    query = calibre_db.generate_linked_query(config.config_read_column, db.Books)
    q = query.filter(calibre_db.common_filters())

    # parse multi selects to a complete dict
    tags = dict()
    elements = ['tag', 'shelf', 'extension']
    for element in elements:
        tags['include_' + element] = term.get('include_' + element)
        tags['exclude_' + element] = term.get('exclude_' + element)

    author_name = term.get("authors")
    book_title = term.get("title")
    pub_start = term.get("publishstart")
    pub_end = term.get("publishend")
    read_status = term.get("read_status")
    if author_name:
        author_name = strip_whitespaces(author_name).lower().replace(',', '|')
    if book_title:
        book_title = strip_whitespaces(book_title).lower()

    search_term = []
    cc_present = False
    for c in cc:
        if c.datatype == "datetime":
            column_start = term.get('custom_column_' + str(c.id) + '_start')
            column_end = term.get('custom_column_' + str(c.id) + '_end')
            if column_start:
                search_term.extend(["{} >= {}".format(c.name,
                                                       format_date(datetime.strptime(column_start, "%Y-%m-%d").date(),
                                                                   format='medium')
                                                       )])
                cc_present = True
            if column_end:
                search_term.extend(["{} <= {}".format(c.name,
                                                      format_date(datetime.strptime(column_end, "%Y-%m-%d").date(),
                                                                   format='medium')
                                                       )])
                cc_present = True
        if c.datatype in ["int", "float"]:
            column_low = term.get('custom_column_' + str(c.id) + '_low')
            column_high = term.get('custom_column_' + str(c.id) + '_high')
            if column_low:
                search_term.extend(["{} >= {}".format(c.name, column_low)])
                cc_present = True
            if column_high:
                search_term.extend(["{} <= {}".format(c.name,column_high)])
                cc_present = True
        elif c.datatype == "bool":
            if term.get('custom_column_' + str(c.id)) != "Any":
                search_term.extend([("{}: {}".format(c.name, term.get('custom_column_' + str(c.id))))])
                cc_present = True
        elif term.get('custom_column_' + str(c.id)):
            search_term.extend([("{}: {}".format(c.name, term.get('custom_column_' + str(c.id))))])
            cc_present = True

    if any(tags.values()) or author_name or book_title or pub_start or pub_end or cc_present \
            or read_status != "Any":
        search_term, pub_start, pub_end = extend_search_term(search_term, author_name, book_title, pub_start,
                                                             pub_end, tags, read_status)
        if author_name:
            q = q.filter(db.Books.authors.any(func.lower(db.Authors.name).ilike("%" + author_name + "%")))
        if book_title:
            q = q.filter(func.lower(db.Books.title).ilike("%" + book_title + "%"))
        if pub_start:
            q = q.filter(func.datetime(db.Books.pubdate) > func.datetime(pub_start))
        if pub_end:
            q = q.filter(func.datetime(db.Books.pubdate) < func.datetime(pub_end))
        if read_status != "Any":
            q = q.filter(adv_search_read_status(read_status))
        q = adv_search_tag(q, tags['include_tag'], tags['exclude_tag'])
        q = adv_search_shelf(q, tags['include_shelf'], tags['exclude_shelf'])
        q = adv_search_extension(q, tags['include_extension'], tags['exclude_extension'])

        # search custom columns
        try:
            q = adv_search_custom_columns(cc, term, q)
        except AttributeError as ex:
            log.debug_or_exception(ex)
            flash(_("Couldn't search custom columns. Restart Lily and try again."), category="error")

    q = q.order_by(*sort)
    flask_session['query'] = json.dumps(term)

    # Perform a count query for pagination, which is much faster than fetching all results.
    result_count = q.count()

    if offset is not None and limit is not None:
        offset = int(offset)
        pagination = Pagination(page=(offset // limit + 1), per_page=limit, total_count=result_count)
        # Fetch only the required page of results from the database
        results = q.offset(offset).limit(limit).all()
    else:
        offset = 0
        limit = result_count if result_count > 0 else 1
        pagination = Pagination(page=1, per_page=limit, total_count=result_count)
        results = q.all()

    entries = calibre_db.order_authors(results, list_return=True, combined=True)
    return render_title_template('search.html',
                                 adv_searchterm=search_term,
                                 pagination=pagination,
                                 entries=entries,
                                 result_count=result_count,
                                 title=_("Advanced Search"), page="advsearch",
                                 order=order[1])


def render_prepare_search_form(cc):
    # prepare data for search-form
    tags = calibre_db.session.query(db.Tags)\
        .join(db.books_tags_link)\
        .join(db.Books)\
        .filter(calibre_db.common_filters()) \
        .group_by(text('books_tags_link.tag'))\
        .order_by(db.Tags.name).all()
    shelves = ub.session.query(ub.Shelf)\
        .filter(or_(ub.Shelf.is_public == 1, ub.Shelf.user_id == int(current_user.id)))\
        .order_by(ub.Shelf.name).all()
    extensions = calibre_db.session.query(db.Data)\
        .join(db.Books)\
        .filter(calibre_db.common_filters()) \
        .group_by(db.Data.format)\
        .order_by(db.Data.format).all()
    return render_title_template('search_form.html', tags=tags, extensions=extensions,
                                 shelves=shelves, title=_("Advanced Search"), cc=cc, page="advsearch")


def render_search_results(term, offset=None, order=None, limit=None):
    if term:
        entries, result_count, pagination = calibre_db.get_search_results(term, config, offset, order, limit,
                                                                          cards_only=True)
    else:
        entries = list()
        order = [None, None]
        pagination = result_count = None

    return render_title_template('search.html',
                                 searchterm=term,
                                 pagination=pagination,
                                 query=term,
                                 adv_searchterm=term,
                                 entries=entries,
                                 result_count=result_count,
                                 title=_("Search"),
                                 page="search",
                                 order=order[1])


