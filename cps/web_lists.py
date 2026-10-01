# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The author, series, publisher, ratings, formats, language, category and downloads list pages.

Routes are attached to the web blueprint; web.py imports this module at its end."""

import importlib

from flask import request, abort
from flask_babel import gettext as _
from .cw_login import current_user
from sqlalchemy.sql.expression import text, func, or_

from . import constants
from . import db, ub, config, app
from . import calibre_db
from .usermanagement import login_required_if_no_ano
from .render_template import render_title_template

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


from .web import web, generate_char_list, query_char_list


@web.route("/author")
@login_required_if_no_ano
def author_list():
    if current_user.check_visibility(constants.SIDEBAR_AUTHOR):
        # By first name, as displayed ("Jane Austen"), not Calibre's "Austen, Jane" sort key
        if current_user.get_view_property('author', 'dir') == 'desc':
            order = func.lower(db.Authors.name).desc()
            order_no = 0
        else:
            order = func.lower(db.Authors.name).asc()
            order_no = 1
        entries = calibre_db.session.query(db.Authors, func.count('books_authors_link.book').label('count')) \
            .join(db.books_authors_link).join(db.Books).filter(calibre_db.common_filters()) \
            .group_by(text('books_authors_link.author')).order_by(order).all()
        char_list = query_char_list(db.Authors.name, db.books_authors_link)
        return render_title_template('list.html', entries=entries, folder='web.books_list', charlist=char_list,
                                     title="Authors", page="authorlist", data='author', order=order_no)
    else:
        abort(404)


@web.route("/downloadlist")
@login_required_if_no_ano
def download_list():
    if current_user.get_view_property('download', 'dir') == 'desc':
        order = ub.User.name.desc()
        order_no = 0
    else:
        order = ub.User.name.asc()
        order_no = 1
    if current_user.check_visibility(constants.SIDEBAR_DOWNLOAD) and current_user.role_admin():
        entries = ub.session.query(ub.User, func.count(ub.Downloads.book_id).label('count')) \
            .join(ub.Downloads).group_by(ub.Downloads.user_id).order_by(order).all()
        char_list = ub.session.query(func.upper(func.substr(ub.User.name, 1, 1)).label('char')) \
            .filter(ub.User.role.op('&')(constants.ROLE_ANONYMOUS) != constants.ROLE_ANONYMOUS) \
            .group_by(func.upper(func.substr(ub.User.name, 1, 1))).all()
        return render_title_template('list.html', entries=entries, folder='web.books_list', charlist=char_list,
                                     title=_("Downloads"), page="downloadlist", data="download", order=order_no)
    else:
        abort(404)


@web.route("/publisher")
@login_required_if_no_ano
def publisher_list():
    if current_user.check_visibility(constants.SIDEBAR_PUBLISHER):
        order_dir = current_user.get_view_property('publisher', 'dir')
        order_no = 1 if order_dir != 'desc' else 0
        order = db.Publishers.name.desc() if order_dir == 'desc' else db.Publishers.name.asc()

        entries_query = (calibre_db.session.query(db.Publishers, func.count(db.books_publishers_link.c.book).label('count'))
                         .join(db.books_publishers_link, db.Publishers.id == db.books_publishers_link.c.publisher)
                         .join(db.Books, db.books_publishers_link.c.book == db.Books.id)
                         .filter(calibre_db.common_filters())
                         .group_by(db.Publishers.id)
                         .order_by(order))

        entries = entries_query.all()

        no_publisher_count = (calibre_db.session.query(func.count(db.Books.id))
                              .outerjoin(db.books_publishers_link)
                              .filter(db.books_publishers_link.c.book == None)
                              .filter(calibre_db.common_filters())
                              .scalar())

        if no_publisher_count:
            # Manually create an "Unknown" category entry
            none_publisher_entry = (db.Category(_("Unknown"), "-1"), no_publisher_count)
            # Decide where to insert it based on sort order
            if order_no == 1: # ascending
                entries.insert(0, none_publisher_entry)
            else: # descending
                entries.append(none_publisher_entry)

        char_list = [entry[0].name[0].upper() for entry in entries if entry[0].name]
        char_list = sorted(list(set(char_list)))

        return render_title_template('list.html', entries=entries, folder='web.books_list', charlist=char_list,
                                     title=_("Publishers"), page="publisherlist", data="publisher", order=order_no)
    else:
        abort(404)


@web.route("/series")
@login_required_if_no_ano
def series_list():
    if current_user.check_visibility(constants.SIDEBAR_SERIES):
        if current_user.get_view_property('series', 'dir') == 'desc':
            order = db.Series.sort.desc()
            order_no = 0
        else:
            order = db.Series.sort.asc()
            order_no = 1
        char_list = query_char_list(db.Series.sort, db.books_series_link)
        if current_user.get_view_property('series', 'series_view') == 'list':
            entries = calibre_db.session.query(db.Series, func.count('books_series_link.book').label('count')) \
                .join(db.books_series_link).join(db.Books).filter(calibre_db.common_filters()) \
                .group_by(text('books_series_link.series')).order_by(order).all()
            no_series_count = (calibre_db.session.query(db.Books)
                            .outerjoin(db.books_series_link).outerjoin(db.Series)
                            .filter(db.Series.name == None)
                            .filter(calibre_db.common_filters())
                            .count())
            if no_series_count:
                entries.append([db.Category(_("Unknown"), "-1"), no_series_count])
            entries = sorted(entries, key=lambda x: x[0].name.lower(), reverse=not order_no)
            return render_title_template('list.html',
                                         entries=entries,
                                         folder='web.books_list',
                                         charlist=char_list,
                                         title=_("Series"),
                                         page="serieslist",
                                         data="series", order=order_no)
        else:
            entries = (calibre_db.session.query(db.Books, func.count('books_series_link').label('count'),
                                                func.max(db.Books.series_index), db.Books.id)
                       .join(db.books_series_link).join(db.Series).filter(calibre_db.common_filters())
                       .group_by(text('books_series_link.series'))
                       .having(or_(func.max(db.Books.series_index), db.Books.series_index==""))
                       .order_by(order)
                       .all())
            return render_title_template('grid.html', entries=entries, folder='web.books_list', charlist=char_list,
                                         title=_("Series"), page="serieslist", data="series", bodyClass="grid-view",
                                         order=order_no)
    else:
        abort(404)


@web.route("/ratings")
@login_required_if_no_ano
def ratings_list():
    if current_user.check_visibility(constants.SIDEBAR_RATING):
        order_dir = current_user.get_view_property('ratings', 'dir')
        order_no = 1 if order_dir != 'desc' else 0
        order = db.Ratings.rating.desc() if order_dir == 'desc' else db.Ratings.rating.asc()

        entries_query = (calibre_db.session.query(db.Ratings, func.count(db.books_ratings_link.c.book).label('count'),
                                           (db.Ratings.rating / 2).label('name'))
                   .join(db.books_ratings_link, db.Ratings.id == db.books_ratings_link.c.rating)
                   .join(db.Books, db.books_ratings_link.c.book == db.Books.id)
                   .filter(calibre_db.common_filters())
                   .filter(db.Ratings.rating > 0)
                   .group_by(db.Ratings.id)
                   .order_by(order))

        entries = entries_query.all()

        no_rating_count = (calibre_db.session.query(func.count(db.Books.id))
                           .outerjoin(db.books_ratings_link, db.Books.id == db.books_ratings_link.c.book)
                           .outerjoin(db.Ratings, db.books_ratings_link.c.rating == db.Ratings.id)
                           .filter(calibre_db.common_filters())
                           .filter(or_(db.books_ratings_link.c.rating == None, db.Ratings.rating == 0))
                           .scalar())

        if no_rating_count:
            none_rating_entry = (db.Category(_("Unknown"), "-1"), no_rating_count, 0)
            if order_no == 1: # ascending
                entries.insert(0, none_rating_entry)
            else: # descending
                entries.append(none_rating_entry)

        return render_title_template('list.html', entries=entries, folder='web.books_list',
                                     title=_("Ratings"), page="ratingslist", data="ratings", order=order_no)
    else:
        abort(404)


@web.route("/formats")
@login_required_if_no_ano
def formats_list():
    if current_user.check_visibility(constants.SIDEBAR_FORMAT):
        if current_user.get_view_property('formats', 'dir') == 'desc':
            order = db.Data.format.desc()
            order_no = 0
        else:
            order = db.Data.format.asc()
            order_no = 1
        entries = calibre_db.session.query(db.Data,
                                           func.count('data.book').label('count'),
                                           db.Data.format.label('format')) \
            .join(db.Books).filter(calibre_db.common_filters()) \
            .group_by(db.Data.format).order_by(order).all()
        no_format_count = (calibre_db.session.query(db.Books).outerjoin(db.Data)
                           .filter(db.Data.format == None)
                           .filter(calibre_db.common_filters())
                           .count())
        if no_format_count:
            entries.append([db.Category(_("Unknown"), "-1"), no_format_count])
        return render_title_template('list.html', entries=entries, folder='web.books_list', charlist=list(),
                                     title=_("File formats list"), page="formatslist", data="formats", order=order_no)
    else:
        abort(404)


@web.route("/language")
@login_required_if_no_ano
def language_overview():
    if current_user.check_visibility(constants.SIDEBAR_LANGUAGE) and current_user.filter_language() == "all":
        order_no = 0 if current_user.get_view_property('language', 'dir') == 'desc' else 1
        languages = calibre_db.speaking_language(reverse_order=not order_no, with_count=True)
        char_list = generate_char_list(languages)
        return render_title_template('list.html', entries=languages, folder='web.books_list', charlist=char_list,
                                     title=_("Languages"), page="langlist", data="language", order=order_no)
    else:
        abort(404)


@web.route("/category")
@login_required_if_no_ano
def category_list():
    if current_user.check_visibility(constants.SIDEBAR_CATEGORY):
        if current_user.get_view_property('category', 'dir') == 'desc':
            order = db.Tags.name.desc()
            order_no = 0
        else:
            order = db.Tags.name.asc()
            order_no = 1
        entries = calibre_db.session.query(db.Tags, func.count('books_tags_link.book').label('count')) \
            .join(db.books_tags_link).join(db.Books).order_by(order).filter(calibre_db.common_filters()) \
            .group_by(db.Tags.id).all()
        no_tag_count = (calibre_db.session.query(db.Books)
                         .outerjoin(db.books_tags_link).outerjoin(db.Tags)
                        .filter(db.Tags.name == None)
                         .filter(calibre_db.common_filters())
                         .count())
        if no_tag_count:
            entries.append([db.Category(_("Unknown"), "-1"), no_tag_count])
        entries = sorted(entries, key=lambda x: x[0].name.lower(), reverse=not order_no)
        char_list = generate_char_list(entries)
        return render_title_template('list.html', entries=entries, folder='web.books_list', charlist=char_list,
                                     title=_("Categories"), page="catlist", data="category", order=order_no)
    else:
        abort(404)
