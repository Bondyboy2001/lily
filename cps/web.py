# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import os
import json
import math
import importlib
from datetime import datetime, timezone

from flask import Blueprint, jsonify
from flask import request, redirect, make_response, flash, abort, url_for
from flask import session as flask_session
from flask_babel import gettext as _
from flask_babel import get_locale
from .cw_login import current_user
from sqlalchemy.exc import IntegrityError, InvalidRequestError, OperationalError
from sqlalchemy.sql.expression import text, func, false, and_
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.sql.functions import coalesce

from . import constants, logger, isoLanguages, helper
from . import db, ub, config, app
from . import calibre_db
from .search import render_search_results, render_adv_search_results
from .helper import check_read_formats, edit_book_read_status
from .pagination import Pagination
from .usermanagement import login_required_if_no_ano
from .render_template import render_title_template
from . import list_filters
from .setup_checklist import setup_checklist
from .helper import change_archived_books
from .services.worker import WorkerThread
from .tasks_status import render_task_status
from .usermanagement import user_login_required

# CWA Imports
import sqlite3
import time

import sys
sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from cwa_db import CWA_DB

from functools import wraps

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


web = Blueprint('web', __name__)

log = logger.create()


# ################################### Login logic and rights management ###############################################


def download_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        if current_user.role_download():
            return f(*args, **kwargs)
        abort(403)

    return inner


def viewer_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        if current_user.role_viewer():
            return f(*args, **kwargs)
        abort(403)

    return inner


# ################################### data provider functions #########################################################


@web.route("/ajax/emailstat")
@user_login_required
def get_email_status_json():
    tasks = WorkerThread.get_instance().tasks
    return jsonify(render_task_status(tasks))


@web.route("/ajax/bookmark/<int:book_id>/<book_format>", methods=['POST'])
@user_login_required
def set_bookmark(book_id, book_format):
    bookmark_key = request.form["bookmark"]
    ub.session.query(ub.Bookmark).filter(and_(ub.Bookmark.user_id == int(current_user.id),
                                              ub.Bookmark.book_id == book_id,
                                              ub.Bookmark.format == book_format)).delete()
    if not bookmark_key:
        ub.session_commit()
        return "", 204

    l_bookmark = ub.Bookmark(user_id=current_user.id,
                             book_id=book_id,
                             format=book_format,
                             bookmark_key=bookmark_key)
    ub.session.merge(l_bookmark)
    ub.session_commit("Bookmark for user {} in book {} created".format(current_user.id, book_id))
    return "", 201


WEB_PROGRESS_CFI_MAX_LEN = 4096
WEB_PROGRESS_FINISHED_AT = 0.99


def _web_progress_json(progress):
    if not progress:
        return {"cfi": None, "percent": None, "updated": None}
    updated = progress.last_modified
    if updated is not None and updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return {"cfi": progress.cfi,
            "percent": progress.percent,
            "updated": updated.isoformat() if updated else None}


def _update_read_status_from_web_progress(user_id, book_id, percent):
    """Unread -> in progress; anything not finished -> finished once the reader hits the end."""
    read_book = ub.session.query(ub.ReadBook).filter(ub.ReadBook.user_id == user_id,
                                                     ub.ReadBook.book_id == book_id).first()
    if not read_book:
        read_book = ub.ReadBook(user_id=user_id, book_id=book_id, read_status=ub.ReadBook.STATUS_UNREAD,
                                times_started_reading=0)
        ub.session.add(read_book)
    if percent >= WEB_PROGRESS_FINISHED_AT:
        if read_book.read_status != ub.ReadBook.STATUS_FINISHED:
            read_book.read_status = ub.ReadBook.STATUS_FINISHED
    elif read_book.read_status in (None, ub.ReadBook.STATUS_UNREAD):
        read_book.read_status = ub.ReadBook.STATUS_IN_PROGRESS
        read_book.times_started_reading = (read_book.times_started_reading or 0) + 1
        read_book.last_time_started_reading = datetime.now(timezone.utc)
    read_book.last_modified = datetime.now(timezone.utc)


@web.route("/ajax/progress/<int:book_id>", methods=['GET', 'POST'])
@user_login_required
def web_reader_progress(book_id):
    """Reading position of the built-in web reader, per user and book.

    GET  -> {"cfi": str|null, "percent": float|null, "updated": iso8601|null}
    POST <- {"cfi": str, "percent": float 0..1} (CSRF token in the X-CSRFToken header)
    """
    if not calibre_db.get_filtered_book(book_id, allow_show_archived=True):
        return jsonify({"error": "Book not found"}), 404
    user_id = int(current_user.id)
    progress = ub.session.query(ub.WebReaderProgress).filter(ub.WebReaderProgress.user_id == user_id,
                                                             ub.WebReaderProgress.book_id == book_id).first()
    if request.method == 'GET':
        return jsonify(_web_progress_json(progress))

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Expected a JSON object"}), 400
    cfi = data.get("cfi")
    percent = data.get("percent")
    if not isinstance(cfi, str) or not cfi or len(cfi) > WEB_PROGRESS_CFI_MAX_LEN:
        return jsonify({"error": "Invalid cfi"}), 400
    if isinstance(percent, bool) or not isinstance(percent, (int, float)) \
            or not math.isfinite(percent) or not 0 <= percent <= 1:
        return jsonify({"error": "percent must be a number between 0 and 1"}), 400
    percent = float(percent)

    try:
        if not progress:
            progress = ub.WebReaderProgress(user_id=user_id, book_id=book_id)
            ub.session.add(progress)
        progress.cfi = cfi
        progress.percent = percent
        progress.last_modified = datetime.now(timezone.utc)
        _update_read_status_from_web_progress(user_id, book_id, percent)
        ub.session.commit()
    except (OperationalError, InvalidRequestError, IntegrityError) as ex:
        ub.session.rollback()
        log.error("Could not save web reader progress for book %s: %s", book_id, ex)
        return jsonify({"error": "Could not save progress"}), 500
    return jsonify(_web_progress_json(progress))


@web.route("/ajax/toggleread/<int:book_id>", methods=['POST'])
@user_login_required
def toggle_read(book_id):
    message = edit_book_read_status(book_id)
    if message:
        return message, 400
    else:
        return message


@web.route("/ajax/togglearchived/<int:book_id>", methods=['POST'])
@user_login_required
def toggle_archived(book_id):
    change_archived_books(book_id, message="Book {} archive bit toggled".format(book_id))
    return ""


@web.route("/ajax/view", methods=["POST"])
@login_required_if_no_ano
def update_view():
    to_save = request.get_json()
    try:
        for element in to_save:
            for param in to_save[element]:
                current_user.set_view_property(element, param, to_save[element][param])
    except Exception as ex:
        log.error("Could not save view_settings: %r %r: %e", request, to_save, ex)
        return "Invalid request", 400
    return "1", 200


def generate_char_list(entries): # data_colum, db_link):
    char_list = list()
    for entry in entries:
        upper_char = entry[0].name[0].upper()
        if upper_char not in char_list:
            char_list.append(upper_char)
    return char_list


def query_char_list(data_colum, db_link):
    results = (calibre_db.session.query(func.upper(func.substr(data_colum, 1, 1)).label('char'))
            .join(db_link).join(db.Books).filter(calibre_db.common_filters())
            .group_by(func.upper(func.substr(data_colum, 1, 1))).all())
    return results


def get_sort_function(sort_param, data):
    order = [db.Books.timestamp.desc()]
    if sort_param == 'stored':
        sort_param = current_user.get_view_property(data, 'stored')
    else:
        current_user.set_view_property(data, 'stored', sort_param)
    if sort_param == 'pubnew':
        order = [db.Books.pubdate.desc()]
    if sort_param == 'pubold':
        order = [db.Books.pubdate]
    if sort_param == 'abc':
        order = [db.Books.sort]
    if sort_param == 'zyx':
        order = [db.Books.sort.desc()]
    if sort_param == 'new':
        order = [db.Books.timestamp.desc()]
    if sort_param == 'old':
        order = [db.Books.timestamp]
    if sort_param == 'authaz':
        order = [db.Books.author_sort.asc(), db.Series.name, db.Books.series_index]
    if sort_param == 'authza':
        order = [db.Books.author_sort.desc(), db.Series.name.desc(), db.Books.series_index.desc()]
    if sort_param == 'seriesasc':
        order = [db.Books.series_index.asc()]
    if sort_param == 'seriesdesc':
        order = [db.Books.series_index.desc()]
    if sort_param == 'hotdesc':
        order = [func.count(ub.Downloads.book_id).desc()]
    if sort_param == 'hotasc':
        order = [func.count(ub.Downloads.book_id).asc()]
    if sort_param is None:
        sort_param = "new"
    return order, sort_param


def cwa_get_library_location() -> str:
    dirs = {}
    with open('/app/calibre-web-automated/dirs.json', 'r') as f:
        dirs: dict[str, str] = json.load(f)
    library_dir = dirs['calibre_library_dir']
    return library_dir

def cwa_get_num_books_in_library() -> int:
    try:
        # Path to user's Calibre library's metadata.db
        db_path = os.path.join(cwa_get_library_location(), "metadata.db")
        # Connect to the SQLite database with simple retry for transient locks
        retries, count = 3, 0
        while retries:
            try:
                conn = sqlite3.connect(db_path, timeout=30)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM books")
                count = cursor.fetchone()[0]
                conn.close()
                break
            except sqlite3.OperationalError as e:
                if 'locked' in str(e).lower() and retries > 1:
                    time.sleep(0.1)
                    retries -= 1
                    continue
                raise
        # Return the result
        return count
    except Exception:
        return 0


def render_books_list(data, sort_param, book_id, page):
    order = get_sort_function(sort_param, data)
    if data == "rated":
        return render_rated_books(page, book_id, order=order)
    elif data == "discover":
        return render_discover_books(book_id)
    elif data == "unread":
        return render_read_books(page, False, order=order)
    elif data == "read":
        return render_read_books(page, True, order=order)
    elif data == "hot":
        return render_hot_books(page, order)
    elif data == "download":
        return render_downloaded_books(page, order, book_id)
    elif data == "author":
        return render_author_books(page, book_id, order)
    elif data == "publisher":
        return render_publisher_books(page, book_id, order)
    elif data == "series":
        return render_series_books(page, book_id, order)
    elif data == "ratings":
        return render_ratings_books(page, book_id, order)
    elif data == "formats":
        return render_formats_books(page, book_id, order)
    elif data == "category":
        return render_category_books(page, book_id, order)
    elif data == "language":
        return render_language_books(page, book_id, order)
    elif data == "archived":
        return render_archived_books(page, order)
    elif data == "search":
        term = request.args.get('query', None)
        offset = int(int(config.config_books_per_page) * (page - 1))
        return render_search_results(term, offset, order, config.config_books_per_page)
    elif data == "advsearch":
        term = json.loads(flask_session.get('query', '{}'))
        offset = int(int(config.config_books_per_page) * (page - 1))
        return render_adv_search_results(term, offset, order, config.config_books_per_page)
    else:
        website = data or "newest"
        entries, random, pagination = calibre_db.fill_indexpage(page, 0, db.Books,
                                                                list_filters.filter_expression(), order[0],
                                                                True, config.config_read_column,
                                                                db.books_series_link,
                                                                db.Books.id == db.books_series_link.c.book,
                                                                db.Series, cards_only=True)

        try:
            title = _('Books (%(count)s)', count=pagination.total_count)
        except (AttributeError, TypeError):
            title = _('Books (%(count)s)', count=cwa_get_num_books_in_library())

        continue_reading = []
        if website == "newest" and page == 1 and not list_filters.active_filters():
            continue_reading = get_continue_reading_entries()

        return render_title_template('index.html', random=random, entries=entries, pagination=pagination,
                                     title=title, page=website, order=order[1],
                                     continue_reading=continue_reading,
                                     list_filters=list_filters.filter_context(),
                                     setup_checklist=(setup_checklist() if website == "newest" and page == 1
                                                      else None))


CONTINUE_READING_LIMIT = 12


def get_continue_reading_progress(session, user_id, limit=CONTINUE_READING_LIMIT):
    """Return [(book_id, progress_percent or None), ...] for books the user is currently reading.

    ReadBook.read_status == STATUS_IN_PROGRESS is the source of truth; the percentage is the web
    reader's saved position. Most recently touched first.
    """
    last_touched = func.max(ub.ReadBook.last_modified,
                            coalesce(ub.WebReaderProgress.last_modified, ub.ReadBook.last_modified))
    rows = (session.query(ub.ReadBook.book_id, ub.WebReaderProgress.percent)
            .outerjoin(ub.WebReaderProgress,
                       and_(ub.WebReaderProgress.user_id == ub.ReadBook.user_id,
                            ub.WebReaderProgress.book_id == ub.ReadBook.book_id))
            .filter(ub.ReadBook.user_id == user_id,
                    ub.ReadBook.read_status == ub.ReadBook.STATUS_IN_PROGRESS)
            .order_by(last_touched.desc(), ub.ReadBook.id.desc())
            # headroom for duplicate rows and books hidden by the visibility filters
            .limit(limit * 3)
            .all())
    result = []
    seen = set()
    for book_id, web_percent in rows:
        if book_id in seen:
            continue
        seen.add(book_id)
        percent = None
        if web_percent is not None:
            try:
                percent = max(0.0, min(100.0, float(web_percent) * 100.0))
            except (TypeError, ValueError):
                percent = None
        result.append((book_id, percent))
    return result


def get_continue_reading_entries(limit=CONTINUE_READING_LIMIT):
    """Books the current user is reading, as index-style entries plus a 'progress' percentage."""
    if current_user.is_anonymous or not current_user.is_authenticated:
        return []
    try:
        progress = get_continue_reading_progress(ub.session, int(current_user.id), limit)
        if not progress:
            return []
        rows = (calibre_db.generate_linked_query(config.config_read_column, db.Books)
                .filter(calibre_db.common_filters())
                .filter(db.Books.id.in_([book_id for book_id, __ in progress]))
                .all())
        by_id = {row.Books.id: row for row in rows}
        entries = []
        for book_id, percent in progress:
            if book_id in by_id:
                entries.append({'entry': by_id[book_id], 'progress': percent})
                if len(entries) >= limit:
                    break
        return entries
    except Exception as ex:
        log.debug("Could not load continue reading row: %s", ex)
        return []


def render_rated_books(page, book_id, order):
    if current_user.check_visibility(constants.SIDEBAR_BEST_RATED):
        entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                db.Books,
                                                                and_(db.Books.ratings.any(db.Ratings.rating > 9),
                                                                     list_filters.filter_expression()),
                                                                order[0],
                                                                True, config.config_read_column,
                                                                db.books_series_link,
                                                                db.Books.id == db.books_series_link.c.book,
                                                                db.Series, cards_only=True)

        return render_title_template('index.html', random=random, entries=entries, pagination=pagination,
                                     id=book_id, title=_("Top Rated Books"), page="rated", order=order[1],
                                     list_filters=list_filters.filter_context())
    else:
        abort(404)


def render_discover_books(book_id):
    if current_user.check_visibility(constants.SIDEBAR_RANDOM):
        entries, __, ___ = calibre_db.fill_indexpage(1, 0, db.Books, list_filters.filter_expression(),
                                                     [func.randomblob(2)],
                                                            join_archive_read=True,
                                                            config_read_column=config.config_read_column, cards_only=True)
        pagination = Pagination(1, config.config_books_per_page, config.config_books_per_page)
        return render_title_template('index.html', random=false(), entries=entries, pagination=pagination, id=book_id,
                                     title=_("Discover (Random Books)"), page="discover",
                                     list_filters=list_filters.filter_context())
    else:
        abort(404)


def render_hot_books(page, order):
    if current_user.check_visibility(constants.SIDEBAR_HOT):
        if order[1] not in ['hotasc', 'hotdesc']:
            order = [func.count(ub.Downloads.book_id).desc()], 'hotdesc'

        random = false()

        off = int(config.config_books_per_page) * (page - 1)

        # Get total count for pagination
        total_hot_books = ub.session.query(func.count(ub.Downloads.book_id.distinct())).scalar()

        # Get the book_ids for the current page
        hot_book_ids_query = (ub.session.query(ub.Downloads.book_id)
                              .group_by(ub.Downloads.book_id)
                              .order_by(*order[0])
                              .offset(off)
                              .limit(config.config_books_per_page))

        hot_book_ids = [item[0] for item in hot_book_ids_query]

        entries = []
        if hot_book_ids:
            query = calibre_db.generate_linked_query(config.config_read_column, db.Books)
            # Fetch all book details in one query
            book_details = query.filter(calibre_db.common_filters()).filter(db.Books.id.in_(hot_book_ids)).all()

            # Create a dictionary for quick lookups
            book_map = {book.Books.id: book for book in book_details}

            # Reorder the entries to match the "hotness" order
            for book_id in hot_book_ids:
                if book_id in book_map:
                    entries.append(book_map[book_id])
                else:
                    # This book might have been deleted from calibre but still in downloads table
                    ub.delete_download(book_id)

        pagination = Pagination(page, config.config_books_per_page, total_hot_books)
        return render_title_template('index.html', random=random, entries=entries, pagination=pagination,
                                     title=_("Hot Books (Most Downloaded)"), page="hot", order=order[1])
    else:
        abort(404)


def render_downloaded_books(page, order, user_id):
    if current_user.role_admin():
        user_id = int(user_id)
    else:
        user_id = current_user.id
    user = ub.session.query(ub.User).filter(ub.User.id == user_id).first()
    if current_user.check_visibility(constants.SIDEBAR_DOWNLOAD) and user:
        entries, random, pagination = calibre_db.fill_indexpage(page,
                                                            0,
                                                            db.Books,
                                                            ub.Downloads.user_id == user_id,
                                                            order[0],
                                                            True, config.config_read_column,
                                                            db.books_series_link,
                                                            db.Books.id == db.books_series_link.c.book,
                                                            db.Series,
                                                            ub.Downloads, db.Books.id == ub.Downloads.book_id, cards_only=True)
        for book in entries:
            if not (calibre_db.session.query(db.Books).filter(calibre_db.common_filters())
                    .filter(db.Books.id == book.Books.id).first()):
                ub.delete_download(book.Books.id)
        return render_title_template('index.html',
                                     random=random,
                                     entries=entries,
                                     pagination=pagination,
                                     id=user_id,
                                     title=_("Downloaded books by %(user)s", user=user.name),
                                     page="download",
                                     order=order[1])
    else:
        abort(404)


def render_author_books(page, author_id, order):
    entries, __, pagination = calibre_db.fill_indexpage(page, 0,
                                                        db.Books,
                                                        and_(db.Books.authors.any(db.Authors.id == author_id),
                                                             list_filters.filter_expression()),
                                                        [order[0][0], db.Series.name, db.Books.series_index],
                                                        True, config.config_read_column,
                                                        db.books_series_link,
                                                        db.books_series_link.c.book == db.Books.id,
                                                        db.Series, cards_only=True)
    if entries is None or (not len(entries) and not list_filters.active_filters()):
        flash(_("Oops! Selected book is unavailable. File does not exist or is not accessible"),
              category="error")
        return redirect(url_for("web.index"))
    if sqlalchemy_version2:
        author = calibre_db.session.get(db.Authors, author_id)
    else:
        author = calibre_db.session.query(db.Authors).get(author_id)
    author_name = author.name.replace('|', ',')
    return render_title_template('author.html', entries=entries, pagination=pagination, id=author_id,
                                 title=_("Author: %(name)s", name=author_name), page="author", order=order[1],
                                 list_filters=list_filters.filter_context())


def render_publisher_books(page, book_id, order):
    if book_id == '-1':
        entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                db.Books,
                                                                db.Publishers.name == None,
                                                                [db.Series.name, order[0][0], db.Books.series_index],
                                                                True, config.config_read_column,
                                                                db.books_publishers_link,
                                                                db.Books.id == db.books_publishers_link.c.book,
                                                                db.Publishers,
                                                                db.books_series_link,
                                                                db.Books.id == db.books_series_link.c.book,
                                                                db.Series, cards_only=True)
        publisher = _("Unknown")
    else:
        publisher = calibre_db.session.query(db.Publishers).filter(db.Publishers.id == book_id).first()
        if publisher:
            entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                    db.Books,
                                                                    db.Books.publishers.any(
                                                                        db.Publishers.id == book_id),
                                                                    [db.Series.name, order[0][0],
                                                                     db.Books.series_index],
                                                                    True, config.config_read_column,
                                                                    db.books_series_link,
                                                                    db.Books.id == db.books_series_link.c.book,
                                                                    db.Series, cards_only=True)
            publisher = publisher.name
        else:
            abort(404)

    return render_title_template('index.html', random=random, entries=entries, pagination=pagination, id=book_id,
                                 title=_("Publisher: %(name)s", name=publisher),
                                 page="publisher",
                                 order=order[1])


def render_series_books(page, book_id, order):
    if book_id == '-1':
        entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                db.Books,
                                                                and_(db.Series.name == None,
                                                                     list_filters.filter_expression()),
                                                                [order[0][0]],
                                                                True, config.config_read_column,
                                                                db.books_series_link,
                                                                db.Books.id == db.books_series_link.c.book,
                                                                db.Series, cards_only=True)
        series_name = _("Unknown")
    else:
        series_name = calibre_db.session.query(db.Series).filter(db.Series.id == book_id).first()
        if series_name:
            entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                    db.Books,
                                                                    and_(db.Books.series.any(db.Series.id == book_id),
                                                                         list_filters.filter_expression()),
                                                                    [order[0][0]],
                                                                    True, config.config_read_column, cards_only=True)
            series_name = series_name.name
        else:
            abort(404)
    return render_title_template('index.html', random=random, pagination=pagination, entries=entries, id=book_id,
                                 title=_("Series: %(serie)s", serie=series_name), page="series", order=order[1],
                                 list_filters=list_filters.filter_context())


def render_ratings_books(page, book_id, order):
    if book_id == '-1':
        db_filter = coalesce(db.Ratings.rating, 0) < 1
        entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                db.Books,
                                                                db_filter,
                                                                [order[0][0]],
                                                                True, config.config_read_column,
                                                                db.books_ratings_link,
                                                                db.Books.id == db.books_ratings_link.c.book,
                                                                db.Ratings, cards_only=True)
        title = _("Rating: None")
    else:
        name = calibre_db.session.query(db.Ratings).filter(db.Ratings.id == book_id).first()
        if name:
            entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                    db.Books,
                                                                    db.Books.ratings.any(db.Ratings.id == book_id),
                                                                    [order[0][0]],
                                                                    True, config.config_read_column, cards_only=True)
            title = _("Rating: %(rating)s stars", rating=int(name.rating / 2))
        else:
            abort(404)
    return render_title_template('index.html', random=random, pagination=pagination, entries=entries, id=book_id,
                                 title=title, page="ratings", order=order[1])


def render_formats_books(page, book_id, order):
    if book_id == '-1':
        name = _("Unknown")
        entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                db.Books,
                                                                db.Data.format == None,
                                                                [order[0][0]],
                                                                True, config.config_read_column,
                                                                db.Data, cards_only=True)

    else:
        name = calibre_db.session.query(db.Data).filter(db.Data.format == book_id.upper()).first()
        if name:
            name = name.format
            entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                    db.Books,
                                                                    db.Books.data.any(
                                                                        db.Data.format == book_id.upper()),
                                                                    [order[0][0]],
                                                                    True, config.config_read_column, cards_only=True)
        else:
            abort(404)

    return render_title_template('index.html', random=random, pagination=pagination, entries=entries, id=book_id,
                                 title=_("File format: %(format)s", format=name),
                                 page="formats",
                                 order=order[1])


def render_category_books(page, book_id, order):
    if book_id == '-1':
        entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                db.Books,
                                                                db.Tags.name == None,
                                                                [order[0][0], db.Series.name, db.Books.series_index],
                                                                True, config.config_read_column,
                                                                db.books_tags_link,
                                                                db.Books.id == db.books_tags_link.c.book,
                                                                db.Tags,
                                                                db.books_series_link,
                                                                db.Books.id == db.books_series_link.c.book,
                                                                db.Series, cards_only=True)
        tagsname = _("Unknown")
    else:
        tagsname = calibre_db.session.query(db.Tags).filter(db.Tags.id == book_id).first()
        if tagsname:
            # Issue #906: Pass viewing_tag_id to allow this tag even if not in allowed tags
            entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                    db.Books,
                                                                    db.Books.tags.any(db.Tags.id == book_id),
                                                                    [order[0][0], db.Series.name,
                                                                     db.Books.series_index],
                                                                    True, config.config_read_column,
                                                                    db.books_series_link,
                                                                    db.Books.id == db.books_series_link.c.book,
                                                                    db.Series,
                                                                    viewing_tag_id=book_id, cards_only=True)
            tagsname = tagsname.name
        else:
            abort(404)
    return render_title_template('index.html', random=random, entries=entries, pagination=pagination, id=book_id,
                                 title=_("Category: %(name)s", name=tagsname), page="category", order=order[1])


def render_language_books(page, name, order):
    try:
        if name.lower() != "none":
            lang_name = isoLanguages.get_language_name(get_locale(), name)
            if lang_name == "Unknown":
                abort(404)
        else:
            lang_name = _("Unknown")
    except KeyError:
        abort(404)
    if name == "none":
        entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                db.Books,
                                                                db.Languages.lang_code == None,
                                                                [order[0][0]],
                                                                True, config.config_read_column,
                                                                db.books_languages_link,
                                                                db.Books.id == db.books_languages_link.c.book,
                                                                db.Languages, cards_only=True)
    else:
        entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                                db.Books,
                                                                db.Books.languages.any(db.Languages.lang_code == name),
                                                                [order[0][0]],
                                                                True, config.config_read_column, cards_only=True)
    return render_title_template('index.html', random=random, entries=entries, pagination=pagination, id=name,
                                 title=_("Language: %(name)s", name=lang_name), page="language", order=order[1])


def render_read_books(page, are_read, as_xml=False, order=None):
    sort_param = order[0] if order else []
    if not config.config_read_column:
        if are_read:
            db_filter = and_(ub.ReadBook.user_id == int(current_user.id),
                             ub.ReadBook.read_status == ub.ReadBook.STATUS_FINISHED)
        else:
            db_filter = coalesce(ub.ReadBook.read_status, 0) != ub.ReadBook.STATUS_FINISHED
    else:
        try:
            if are_read:
                db_filter = db.cc_classes[config.config_read_column].value == True
            else:
                db_filter = coalesce(db.cc_classes[config.config_read_column].value, False) != True
        except (KeyError, AttributeError, IndexError):
            log.error("Custom Column No.{} does not exist in calibre database".format(config.config_read_column))
            if not as_xml:
                flash(_("Custom Column No.%(column)d does not exist in calibre database",
                        column=config.config_read_column),
                      category="error")
                return redirect(url_for("web.index"))
            return []  # ToDo: Handle error Case for opds

    entries, random, pagination = calibre_db.fill_indexpage(page, 0,
                                                            db.Books,
                                                            db_filter,
                                                            sort_param,
                                                            True, config.config_read_column,
                                                            db.books_series_link,
                                                            db.Books.id == db.books_series_link.c.book,
                                                            db.Series)

    if as_xml:
        return entries, pagination
    else:
        if are_read:
            name = _('Read Books') + ' (' + str(pagination.total_count) + ')'
            page_name = "read"
        else:
            name = _('Unread Books') + ' (' + str(pagination.total_count) + ')'
            page_name = "unread"
        return render_title_template('index.html', random=random, entries=entries, pagination=pagination,
                                     title=name, page=page_name, order=order[1])


def render_archived_books(page, sort_param):
    order = sort_param[0] or []
    archived_books = (ub.session.query(ub.ArchivedBook)
                      .filter(ub.ArchivedBook.user_id == int(current_user.id))
                      .filter(ub.ArchivedBook.is_archived == True)
                      .all())
    archived_book_ids = [archived_book.book_id for archived_book in archived_books]

    archived_filter = db.Books.id.in_(archived_book_ids)

    entries, random, pagination = calibre_db.fill_indexpage_with_archived_books(page, db.Books,
                                                                                0,
                                                                                archived_filter,
                                                                                order,
                                                                                True,
                                                                                True, config.config_read_column)

    name = _('Archived Books') + ' (' + str(len(archived_book_ids)) + ')'
    page_name = "archived"
    return render_title_template('index.html', random=random, entries=entries, pagination=pagination,
                                 title=name, page=page_name, order=sort_param[1])


# ################################### Health Check ##################################################################

@web.route("/health")
def health_check():
    uptime = time.time() - _start_time

    try:
        db_path = os.path.join(cwa_get_library_location(), "metadata.db")
        # Read-only URI so a missing library reports unhealthy instead of creating
        # an empty metadata.db, and a real read so an unreadable file is caught.
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            conn.close()
        db_up = True
    except Exception:
        db_up = False

    return jsonify({
        "status": "ok" if db_up else "degraded",
        "uptime": uptime,
        "version": f"Lily/{constants.INSTALLED_VERSION}",
    }), 200 if db_up else 503

# ################################### View Books list ##################################################################

@web.route("/", defaults={'page': 1})
@web.route('/page/<int:page>')
@login_required_if_no_ano
def index(page):
    if current_user.is_authenticated and current_user.role_admin():
        arch_warning = helper.check_architecture()
        if arch_warning:
            flash(arch_warning, category="cwa_arch_warning")

    sort_param = (request.args.get('sort') or 'stored').lower()
    return render_books_list("newest", sort_param, 1, page)


@web.route('/<data>/<sort_param>', defaults={'page': 1, 'book_id': 1})
@web.route('/<data>/<sort_param>/', defaults={'page': 1, 'book_id': 1})
@web.route('/<data>/<sort_param>/<book_id>', defaults={'page': 1})
@web.route('/<data>/<sort_param>/<book_id>/<int:page>')
@login_required_if_no_ano
def books_list(data, sort_param, book_id, page):
    return render_books_list(data, sort_param, book_id, page)


@web.route("/table")
@user_login_required
def books_table():
    visibility = current_user.view_settings.get('table', {})
    cc = calibre_db.get_cc_columns(config, filter_config_custom_read=True)
    return render_title_template('book_table.html', title=_("Books List"), cc=cc, page="book_table",
                                 visiblility=visibility)


@web.route("/ajax/listbooks")
@user_login_required
def list_books():
    off = int(request.args.get("offset") or 0)
    limit = int(request.args.get("limit") or config.config_books_per_page)
    search_param = request.args.get("search")
    sort_param = request.args.get("sort", "id")
    order = request.args.get("order", "").lower()
    state = None
    join = tuple()

    if sort_param == "state":
        state = json.loads(request.args.get("state", "[]"))
    elif sort_param == "tags":
        order = [db.Tags.name.asc()] if order == "asc" else [db.Tags.name.desc()]
        join = db.books_tags_link, db.Books.id == db.books_tags_link.c.book, db.Tags
    elif sort_param == "series":
        order = [db.Series.name.asc()] if order == "asc" else [db.Series.name.desc()]
        join = db.books_series_link, db.Books.id == db.books_series_link.c.book, db.Series
    elif sort_param == "publishers":
        order = [db.Publishers.name.asc()] if order == "asc" else [db.Publishers.name.desc()]
        join = db.books_publishers_link, db.Books.id == db.books_publishers_link.c.book, db.Publishers
    elif sort_param == "authors":
        order = [db.Authors.name.asc(), db.Series.name, db.Books.series_index] if order == "asc" \
            else [db.Authors.name.desc(), db.Series.name.desc(), db.Books.series_index.desc()]
        join = db.books_authors_link, db.Books.id == db.books_authors_link.c.book, db.Authors, db.books_series_link, \
            db.Books.id == db.books_series_link.c.book, db.Series
    elif sort_param == "author_sort":
        order = [db.Books.author_sort.asc()] if order == "asc" else [db.Books.author_sort.desc()]
    elif sort_param == "languages":
        order = [db.Languages.lang_code.asc()] if order == "asc" else [db.Languages.lang_code.desc()]
        join = db.books_languages_link, db.Books.id == db.books_languages_link.c.book, db.Languages
    elif order and sort_param in ["sort", "title", "authors_sort", "series_index"]:
        order = [text(sort_param + " " + order)]
    elif not state:
        order = [db.Books.timestamp.desc()]

    total_count = filtered_count = calibre_db.session.query(db.Books).filter(
        calibre_db.common_filters(allow_show_archived=True)).count()
    if state is not None:
        if search_param:
            books = calibre_db.search_query(search_param, config).all()
            filtered_count = len(books)
        else:
            query = calibre_db.generate_linked_query(config.config_read_column, db.Books)
            books = query.filter(calibre_db.common_filters(allow_show_archived=True)).all()
        entries = calibre_db.get_checkbox_sorted(books, state, off, limit, order, True)
    elif search_param:
        entries, filtered_count, __ = calibre_db.get_search_results(search_param,
                                                                    config,
                                                                    off,
                                                                    [order, ''],
                                                                    limit,
                                                                    *join)
    else:
        entries, __, __ = calibre_db.fill_indexpage_with_archived_books((int(off) / (int(limit)) + 1),
                                                                        db.Books,
                                                                        limit,
                                                                        True,
                                                                        order,
                                                                        True,
                                                                        True,
                                                                        config.config_read_column,
                                                                        *join)

    result = list()
    for entry in entries:
        val = entry[0]
        val.is_archived = entry[1] is True
        val.read_status = entry[2] == ub.ReadBook.STATUS_FINISHED
        for lang_index in range(0, len(val.languages)):
            val.languages[lang_index].language_name = isoLanguages.get_language_name(get_locale(), val.languages[
                lang_index].lang_code)
        result.append(val)

    table_entries = {'totalNotFiltered': total_count, 'total': filtered_count, "rows": result}
    js_list = json.dumps(table_entries, cls=db.AlchemyEncoder)

    response = make_response(js_list)
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    return response


@web.route("/ajax/table_settings", methods=['POST'])
@user_login_required
def update_table_settings():
    current_user.view_settings['table'] = json.loads(request.data)
    try:
        try:
            flag_modified(current_user, "view_settings")
        except AttributeError:
            pass
        ub.session.commit()
    except (InvalidRequestError, OperationalError):
        log.error("Invalid request received: %r ", request, )
        return "Invalid request", 400
    return ""


# ###################################Show single book ##################################################################


@web.route("/read/<int:book_id>/<book_format>")
@login_required_if_no_ano
@viewer_required
def read_book(book_id, book_format):
    book = calibre_db.get_filtered_book(book_id)

    if not book:
        flash(_("Oops! Selected book is unavailable. File does not exist or is not accessible"),
              category="error")
        log.debug("Selected book is unavailable. File does not exist or is not accessible")
        return redirect(url_for("web.index"))

    book.ordered_authors = calibre_db.order_authors([book], False)

    # check if book has a bookmark
    bookmark = None
    if current_user.is_authenticated:
        bookmark = ub.session.query(ub.Bookmark).filter(and_(ub.Bookmark.user_id == int(current_user.id),
                                                             ub.Bookmark.book_id == book_id,
                                                             ub.Bookmark.format == book_format.upper())).first()

    # Track read activity
    if current_user.is_authenticated:
        try:
            from scripts.cwa_db import CWA_DB
            import json
            
            # Detect source of book discovery
            source = request.args.get('from', 'direct')
            referer = request.headers.get('Referer', '')
            if not source or source == 'direct':
                if '/search' in referer:
                    source = 'search'
                elif '/series' in referer:
                    source = 'series'
                elif '/author' in referer:
                    source = 'author'
                elif '/category' in referer:
                    source = 'category'
                elif '/shelf' in referer:
                    source = 'shelf'
            
            cwa_db = CWA_DB()
            cwa_db.log_activity(
                user_id=int(current_user.id),
                user_name=current_user.name,
                event_type='READ',
                item_id=book_id,
                item_title=book.title,
                extra_data=json.dumps({'format': book_format.upper(), 'source': source})
            )
        except Exception as e:
            log.debug(f"Failed to log read activity: {e}")
    
    if book_format.lower() in ("epub", "kepub"):
        log.debug("Start epub reader for %d (%s)", book_id, book_format.lower())
        return render_title_template('read.html', bookid=book_id, title=book.title,
                                     bookmark=bookmark,
                                     book_format=book_format.lower())
    elif book_format.lower() == "pdf":
        log.debug("Start pdf reader for %d", book_id)
        return render_title_template('readpdf.html', pdffile=book_id, title=book.title)
    elif book_format.lower() == "txt":
        log.debug("Start txt reader for %d", book_id)
        return render_title_template('readtxt.html', txtfile=book_id, title=book.title)
    elif book_format.lower() in ["djvu", "djv"]:
        log.debug("Start djvu reader for %d", book_id)
        return render_title_template('readdjvu.html', djvufile=book_id, title=book.title,
                                     extension=book_format.lower())
    else:
        for fileExt in constants.EXTENSIONS_AUDIO:
            if book_format.lower() == fileExt:
                entries = calibre_db.get_filtered_book(book_id)
                log.debug("Start mp3 listening for %d", book_id)
                return render_title_template('listenmp3.html', mp3file=book_id, audioformat=book_format.lower(),
                                             entry=entries, bookmark=bookmark)
        for fileExt in ["cbr", "cbt", "cbz"]:
            if book_format.lower() == fileExt:
                all_name = str(book_id)
                title = book.title
                if len(book.series):
                    title = title + " - " + book.series[0].name
                    if book.series_index:
                        title = title + " #" + '{0:.2f}'.format(book.series_index).rstrip('0').rstrip('.')
                log.debug("Start comic reader for %d", book_id)
                return render_title_template('readcbr.html', comicfile=all_name, title=title,
                                             extension=fileExt, bookmark=bookmark)
        log.debug("Selected book is unavailable. File does not exist or is not accessible")
        flash(_("Oops! Selected book is unavailable. File does not exist or is not accessible"),
              category="error")
        return redirect(url_for("web.index"))


@web.route("/book/<int:book_id>")
@login_required_if_no_ano
def show_book(book_id):
    # Ensure book_id is a plain int to avoid SQLite binding errors
    try:
        book_id = int(book_id)
    except (ValueError, TypeError):
        log.error(f"Invalid book_id passed to show_book: {book_id}")
        flash(_("Invalid book ID."), category="error")
        return redirect(url_for("web.index"))
    entries = calibre_db.get_book_read_archived(book_id, config.config_read_column, allow_show_archived=True)
    if entries:
        read_book = entries[1]
        archived_book = entries[2]
        entry = entries[0]
        entry.read_status = read_book == ub.ReadBook.STATUS_FINISHED
        entry.is_archived = archived_book
        for lang_index in range(0, len(entry.languages)):
            entry.languages[lang_index].language_name = isoLanguages.get_language_name(get_locale(), entry.languages[
                lang_index].lang_code)
        cc = calibre_db.get_cc_columns(config, filter_config_custom_read=True)
        book_in_shelves = []
        shelves = ub.session.query(ub.BookShelf).filter(ub.BookShelf.book_id == book_id).all()
        for sh in shelves:
            book_in_shelves.append(sh.shelf)

        entry.tags = sort(entry.tags, key=lambda tag: tag.name)
        
        # Filter tags based on user's allowed/denied tags (Issue #906)
        if current_user.is_authenticated:
            allowed_tags = current_user.list_allowed_tags()
            denied_tags = current_user.list_denied_tags()
            
            # If allowed tags are configured (not empty), filter to only show allowed tags
            if allowed_tags and allowed_tags != ['']:
                entry.tags = [tag for tag in entry.tags if tag.name in allowed_tags]
            
            # Remove denied tags
            if denied_tags and denied_tags != ['']:
                entry.tags = [tag for tag in entry.tags if tag.name not in denied_tags]

        entry.ordered_authors = calibre_db.order_authors([entry])

        entry.reader_list = check_read_formats(entry)

        entry.audio_entries = []
        for media_format in entry.data:
            if media_format.format.lower() in constants.EXTENSIONS_AUDIO:
                entry.audio_entries.append(media_format.format.lower())

        cwa_db = CWA_DB()
        cwa_settings = cwa_db.cwa_settings

        return render_title_template('detail.html',
                                     entry=entry,
                                     cc=cc,
                                     is_xhr=request.headers.get('X-Requested-With') == 'XMLHttpRequest',
                                     title=entry.title,
                                     books_shelfs=book_in_shelves,
                                     cwa_settings=cwa_settings,
                                     page="book")
    else:
        log.debug("Selected book is unavailable. File does not exist or is not accessible")
        flash(_("Oops! Selected book is unavailable. File does not exist or is not accessible"),
              category="error")
        return redirect(url_for("web.index"))

from . import web_auth  # noqa: E402,F401  (attaches its routes to this blueprint)

from . import web_lists  # noqa: E402,F401  (attaches its routes to this blueprint)

from . import web_files  # noqa: E402,F401  (attaches its routes to this blueprint)

from . import web_typeahead  # noqa: E402,F401  (attaches its routes to this blueprint)
