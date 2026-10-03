# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Library browsing: the home page, book grids, details, the reader entry points and the blueprint the web_* modules attach to."""

import os
import json
import math
import re
import importlib
from datetime import datetime, timezone

from flask import Blueprint, jsonify
from flask import request, redirect, flash, abort, url_for
from flask import session as flask_session
from flask_babel import gettext as _
from .cw_login import current_user
from sqlalchemy.exc import IntegrityError, InvalidRequestError, OperationalError
from sqlalchemy.sql.expression import and_

from . import constants, logger, helper
from . import db, ub, config, app
from . import calibre_db
from .recent_imports import added_summary, books_added_after, newest_book_id
from .search import render_search_results, render_adv_search_results
from .helper import check_read_formats, edit_book_read_status
from .usermanagement import login_required_if_no_ano
from .render_template import render_title_template
from . import list_filters
from . import pdf_fast
from .setup_checklist import setup_checklist
from .services.worker import WorkerThread
from .tasks_status import render_task_status
from .usermanagement import user_login_required

# CWA Imports
import shutil
import sqlite3
import subprocess
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
# search, on the book page and in the editor; the in-browser readers). Everything else runs
# without 'unsafe-eval'.
_EVAL_ENDPOINTS = frozenset({"web.read_book", "web.show_book", "edit-book.show_edit_book"})
# Pages showing metadata search results, whose covers come from the providers' own sites
_META_SEARCH_ENDPOINTS = frozenset({"web.show_book", "edit-book.show_edit_book"})


@app.after_request
def add_security_headers(resp):
    default_src = ([host.strip() for host in config.config_trustedhosts.split(',') if host] +
                   ["'self'", "'unsafe-inline'"])
    if request.endpoint in _EVAL_ENDPOINTS:
        default_src.append("'unsafe-eval'")
    csp = "default-src " + ' '.join(default_src)
    csp += "; font-src 'self' data:"
    if request.endpoint == "web.read_book":
        csp += " blob: "
    csp += "; img-src 'self'"
    csp += " data:"
    if request.endpoint in _META_SEARCH_ENDPOINTS:
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


WEB_PROGRESS_CFI_MAX_LEN = 4096
WEB_PROGRESS_FINISHED_AT = 0.99
# A finished book read again from (near) the start counts as being read again.
WEB_PROGRESS_REREAD_BELOW = 0.05
# Reaching the end only finishes a book after this much active reading (progress-sync.js
# counts it): 15 seconds a page, at least five minutes, or half an audiobook's length.
# Opening a book and scrolling through it to the end leaves it in progress.
READ_SECONDS_PER_PAGE = 15
READ_MIN_SECONDS = 5 * 60
READ_AUDIO_SHARE = 0.5
# The most reading time one post can claim (the reader posts far more often)
READ_SECONDS_MAX_POST = 3600

# Reader bookmarks: any number per user, book and format. Keys are positions in the
# progress sync's form: an epub CFI, or "page:N" for pdf and djvu.
BOOKMARK_FORMATS = ("epub", "pdf", "djvu", "djv")
BOOKMARK_LABEL_MAX_LEN = 200
BOOKMARK_EXCERPT_MAX_LEN = 300
BOOKMARKS_PER_BOOK_MAX = 500


def _bookmark_json(bookmark):
    return {"id": bookmark.id,
            "key": bookmark.bookmark_key,
            "label": bookmark.label or "",
            "excerpt": bookmark.excerpt or ""}


def _bookmark_text(value, limit):
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value[:limit] or None


def _bookmark_query(book_id, fmt):
    return ub.session.query(ub.Bookmark).filter(ub.Bookmark.user_id == int(current_user.id),
                                                ub.Bookmark.book_id == book_id,
                                                ub.Bookmark.format == fmt)


def _bookmark_format(book_id, book_format):
    """The upper-case format of a book the user can see, or None (answered with 404)."""
    fmt = (book_format or "").lower()
    if fmt not in BOOKMARK_FORMATS:
        return None
    book = calibre_db.get_filtered_book(book_id)
    if not book or fmt not in _progress_formats(book):
        return None
    return fmt.upper()


@web.route("/ajax/bookmarks/<int:book_id>/<book_format>", methods=['GET', 'POST'])
@user_login_required
def reader_bookmarks(book_id, book_format):
    """The signed-in user's bookmarks in one format of a book.

    GET  -> {"bookmarks": [{"id", "key", "label", "excerpt"}, ...]}, oldest first
    POST <- {"key": str, "label": str?, "excerpt": str?} (CSRF token in the X-CSRFToken header)
         -> 201 and the new bookmark, or 200 and the saved one when the key is already there
    Rows from when a book had a single bookmark have no label or excerpt.
    """
    fmt = _bookmark_format(book_id, book_format)
    if not fmt:
        return jsonify({"error": "Book or format not found"}), 404
    if request.method == 'GET':
        rows = _bookmark_query(book_id, fmt).order_by(ub.Bookmark.id).all()
        return jsonify({"bookmarks": [_bookmark_json(row) for row in rows if row.bookmark_key]})

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Expected a JSON object"}), 400
    key = data.get("key")
    if not isinstance(key, str) or not key or len(key) > WEB_PROGRESS_CFI_MAX_LEN \
            or not _progress_cfi_ok(fmt.lower(), key):
        return jsonify({"error": "Invalid bookmark position"}), 400
    existing = _bookmark_query(book_id, fmt).filter(ub.Bookmark.bookmark_key == key).first()
    if existing:
        return jsonify(_bookmark_json(existing)), 200
    if _bookmark_query(book_id, fmt).count() >= BOOKMARKS_PER_BOOK_MAX:
        return jsonify({"error": "Too many bookmarks in this book"}), 400
    bookmark = ub.Bookmark(user_id=int(current_user.id), book_id=book_id, format=fmt, bookmark_key=key,
                           label=_bookmark_text(data.get("label"), BOOKMARK_LABEL_MAX_LEN),
                           excerpt=_bookmark_text(data.get("excerpt"), BOOKMARK_EXCERPT_MAX_LEN))
    ub.session.add(bookmark)
    try:
        ub.session.commit()
    except OperationalError as e:
        ub.session.rollback()
        log.error("Could not save a bookmark in book %s: %s", book_id, e)
        return jsonify({"error": "Could not save the bookmark"}), 500
    return jsonify(_bookmark_json(bookmark)), 201


@web.route("/ajax/bookmarks/<int:book_id>/<book_format>/<int:bookmark_id>", methods=['DELETE'])
@web.route("/ajax/bookmarks/<int:book_id>/<book_format>/<int:bookmark_id>/remove", methods=['POST'])
@user_login_required
def remove_reader_bookmark(book_id, book_format, bookmark_id):
    """Removes one of the user's bookmarks: 204, also when it was already gone."""
    fmt = _bookmark_format(book_id, book_format)
    if not fmt:
        return jsonify({"error": "Book or format not found"}), 404
    _bookmark_query(book_id, fmt).filter(ub.Bookmark.id == bookmark_id).delete()
    try:
        ub.session.commit()
    except OperationalError as e:
        ub.session.rollback()
        log.error("Could not remove bookmark %s: %s", bookmark_id, e)
        return jsonify({"error": "Could not remove the bookmark"}), 500
    return "", 204


def _library_uuid():
    try:
        row = calibre_db.session.query(db.Library_Id).first()
        return row.uuid if row else ""
    except Exception:
        return ""


def _web_progress_json(progress, fmt=None):
    if not progress:
        return {"cfi": None, "percent": None, "updated": None, "format": fmt}
    updated = progress.last_modified
    if updated is not None and updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return {"cfi": progress.cfi,
            "percent": progress.percent,
            "updated": updated.isoformat() if updated else None,
            "format": fmt}


# Readers that save their position as "page:N" (1-based).
PAGED_PROGRESS_FORMATS = ("pdf", "djvu", "djv")


def _progress_formats(book):
    try:
        stored = {str(d.format).lower() for d in book.data}
    except Exception:
        stored = set()
    return stored


def _valid_progress_format(book, fmt):
    if not isinstance(fmt, str) or not fmt.strip():
        return None
    fmt = fmt.lower()
    if fmt not in _progress_formats(book):
        return None
    if fmt in PAGED_PROGRESS_FORMATS or fmt == "epub":
        return fmt
    if fmt in constants.EXTENSIONS_AUDIO:
        return fmt
    return None


def _progress_cfi_ok(fmt, cfi):
    if fmt == "epub":
        return cfi.startswith("epubcfi(")
    if fmt in PAGED_PROGRESS_FORMATS:
        m = re.fullmatch(r"page:(\d+)", cfi)
        return bool(m) and int(m.group(1)) >= 1
    m = re.fullmatch(r"time:(\d+(?:\.\d+)?)", cfi)
    return bool(m) and math.isfinite(float(m.group(1)))


def _legacy_positions_belong_here(library_uuid):
    """True when legacy web_reader_progress rows may seed this library.

    The rows carry no library scope, so the library that first reads them claims them
    in reader_legacy_library; after a library switch the claim stays with the original.
    """
    if not library_uuid:
        return False
    row = ub.session.query(ub.ReaderLegacyLibrary).filter_by(id=1).first()
    if row:
        return row.library_uuid == library_uuid
    try:
        ub.session.add(ub.ReaderLegacyLibrary(id=1, library_uuid=library_uuid))
        ub.session.commit()
        return True
    except IntegrityError:
        ub.session.rollback()
        row = ub.session.query(ub.ReaderLegacyLibrary).filter_by(id=1).first()
        return bool(row and row.library_uuid == library_uuid)


def _seeded_legacy_progress(legacy, book, fmt):
    if legacy is None or not legacy.cfi or not _progress_cfi_ok(fmt, legacy.cfi):
        return None
    cfi = legacy.cfi
    formats = _progress_formats(book)
    if cfi.startswith("epubcfi("):
        if fmt == "epub":
            return legacy
        return None
    if cfi.startswith("page:"):
        return legacy if fmt == "pdf" else None
    if cfi.startswith("time:"):
        audio = [f for f in formats if f in constants.EXTENSIONS_AUDIO]
        return legacy if audio == [fmt] else None
    return None


def _reading_time(data):
    """(seconds read since the last post, the book's length in pages or None) from a progress
    post. Clients from before reading time was counted send neither: no time, no length."""
    seconds = data.get("seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
        seconds = 0
    pages = data.get("pages")
    if isinstance(pages, bool) or not isinstance(pages, (int, float)) or not math.isfinite(pages) or pages <= 0:
        pages = None
    return int(max(0, min(READ_SECONDS_MAX_POST, seconds))), pages


def _seconds_to_finish(cfi, percent, pages=None):
    """How much active reading finishing the book takes. A page position near the end gives
    the page count, a time position the audiobook's length; an epub reader sends its pages."""
    m = re.fullmatch(r"(page|time):(\d+(?:\.\d+)?)", cfi or "")
    if m and percent > 0:
        size = float(m.group(2)) / percent
        if m.group(1) == "time":
            return READ_AUDIO_SHARE * size
        pages = size
    return max(READ_MIN_SECONDS, READ_SECONDS_PER_PAGE * pages) if pages else READ_MIN_SECONDS


def _update_read_status_from_web_progress(user_id, book_id, percent, seconds=0, needed=READ_MIN_SECONDS):
    """Unread -> in progress; anything not finished -> finished once the reader hits the end
    after `needed` seconds of reading, counted across posts; finished -> in progress again when
    it is reopened from the start (a re-read, whose reading time starts over)."""
    read_book = ub.session.query(ub.ReadBook).filter(ub.ReadBook.user_id == user_id,
                                                     ub.ReadBook.book_id == book_id).first()
    if not read_book:
        read_book = ub.ReadBook(user_id=user_id, book_id=book_id, read_status=ub.ReadBook.STATUS_UNREAD)
        ub.session.add(read_book)
    if read_book.read_status == ub.ReadBook.STATUS_FINISHED and 0 < percent < WEB_PROGRESS_REREAD_BELOW:
        read_book.read_status = ub.ReadBook.STATUS_IN_PROGRESS
        read_book.reading_seconds = 0
    read_book.reading_seconds = (read_book.reading_seconds or 0) + seconds
    if read_book.read_status == ub.ReadBook.STATUS_FINISHED:
        pass
    elif percent >= WEB_PROGRESS_FINISHED_AT and read_book.reading_seconds >= needed:
        read_book.read_status = ub.ReadBook.STATUS_FINISHED
    elif read_book.read_status in (None, ub.ReadBook.STATUS_UNREAD):
        read_book.read_status = ub.ReadBook.STATUS_IN_PROGRESS
    read_book.last_modified = datetime.now(timezone.utc)


@web.route("/ajax/progress/<int:book_id>", methods=['GET', 'POST'])
@user_login_required
def web_reader_progress(book_id):
    """Reading position of the built-in web reader, per user and book.

    New clients pass ?format=<fmt> and positions are stored per user, library,
    book and format in reader_position. The formatless request is the legacy path
    backed by web_reader_progress.

    GET  -> {"cfi": str|null, "percent": float|null, "updated": iso8601|null, "format": fmt|null}
    POST <- {"cfi": str, "percent": float 0..1, "format": fmt} (CSRF token in the X-CSRFToken header)
    """
    book = calibre_db.get_filtered_book(book_id)
    if not book:
        return jsonify({"error": "Book not found"}), 404
    user_id = int(current_user.id)

    data = None
    if request.method == 'POST':
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Expected a JSON object"}), 400
    formats = request.args.getlist("format")
    if any(not f.strip() for f in formats):
        return jsonify({"error": "empty format parameter"}), 400
    if len(set(f.lower() for f in formats)) > 1:
        return jsonify({"error": "conflicting format parameters"}), 400
    fmt_arg = formats[0] if formats else None
    if fmt_arg is None and isinstance(data, dict) and "format" in data:
        fmt_arg = data.get("format")
    if request.method == 'POST' and formats \
            and data.get("format") is not None \
            and formats[0].lower() != str(data.get("format")).lower():
        return jsonify({"error": "format in query and body disagree"}), 400

    if fmt_arg is None:
        progress = ub.session.query(ub.WebReaderProgress).filter(
            ub.WebReaderProgress.user_id == user_id,
            ub.WebReaderProgress.book_id == book_id).first()
        if request.method == 'GET':
            return jsonify(_web_progress_json(progress))
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
            seconds, pages = _reading_time(data)
            _update_read_status_from_web_progress(user_id, book_id, percent, seconds,
                                                  _seconds_to_finish(cfi, percent, pages))
            ub.session.commit()
        except (OperationalError, InvalidRequestError, IntegrityError) as ex:
            ub.session.rollback()
            log.error("Could not save web reader progress for book %s: %s", book_id, ex)
            return jsonify({"error": "Could not save progress"}), 500
        return jsonify(_web_progress_json(progress))

    fmt = _valid_progress_format(book, fmt_arg)
    if fmt is None:
        return jsonify({"error": "Unknown or unreadable format"}), 400
    library_uuid = _library_uuid()
    if not library_uuid:
        return jsonify({"error": "Library identity unavailable; progress sync disabled"}), 503
    progress = ub.session.query(ub.ReaderPosition).filter(
        ub.ReaderPosition.user_id == user_id,
        ub.ReaderPosition.library_uuid == library_uuid,
        ub.ReaderPosition.book_id == book_id,
        ub.ReaderPosition.format == fmt).first()

    if request.method == 'GET':
        if progress is None and _legacy_positions_belong_here(library_uuid):
            legacy = ub.session.query(ub.WebReaderProgress).filter(
                ub.WebReaderProgress.user_id == user_id,
                ub.WebReaderProgress.book_id == book_id).first()
            progress = _seeded_legacy_progress(legacy, book, fmt)
        return jsonify(_web_progress_json(progress, fmt))

    cfi = data.get("cfi")
    percent = data.get("percent")
    if not isinstance(cfi, str) or not cfi or len(cfi) > WEB_PROGRESS_CFI_MAX_LEN:
        return jsonify({"error": "Invalid cfi"}), 400
    if not _progress_cfi_ok(fmt, cfi):
        return jsonify({"error": "cfi does not match the selected format"}), 400
    if isinstance(percent, bool) or not isinstance(percent, (int, float)) \
            or not math.isfinite(percent) or not 0 <= percent <= 1:
        return jsonify({"error": "percent must be a number between 0 and 1"}), 400
    percent = float(percent)
    try:
        if not progress:
            progress = ub.ReaderPosition(user_id=user_id, library_uuid=library_uuid,
                                         book_id=book_id, format=fmt)
            ub.session.add(progress)
        progress.cfi = cfi
        progress.percent = percent
        progress.last_modified = datetime.now(timezone.utc)
        seconds, pages = _reading_time(data)
        _update_read_status_from_web_progress(user_id, book_id, percent, seconds,
                                              _seconds_to_finish(cfi, percent, pages))
        ub.session.commit()
    except (OperationalError, InvalidRequestError, IntegrityError) as ex:
        ub.session.rollback()
        log.error("Could not save reader position for book %s: %s", book_id, ex)
        return jsonify({"error": "Could not save progress"}), 500
    return jsonify(_web_progress_json(progress, fmt))


@web.route("/ajax/recent-imports")
@user_login_required
def recent_imports():
    """Books added after `after` (the `last` of the previous answer), for the toast that says
    a book dropped in the ingest folder has arrived. The first call only gives `last`."""
    answer = {"last": newest_book_id()}
    after = request.args.get("after", type=int)
    if after is not None:
        books = books_added_after(after)
        if books:
            answer.update(added_summary(books))
    return jsonify(answer)


@web.route("/ajax/toggleread/<int:book_id>", methods=['POST'])
@user_login_required
def toggle_read(book_id):
    message = edit_book_read_status(book_id)
    if message:
        return message, 400
    else:
        return message


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
    if sort_param == 'fetchnew':
        order = [db.last_fetched_order(newest_first=True), db.Books.timestamp.desc()]
    if sort_param == 'fetchold':
        order = [db.last_fetched_order(newest_first=False), db.Books.timestamp.desc()]
    if sort_param == 'authaz':
        order = [db.Books.author_sort.asc(), db.Books.sort]
    if sort_param == 'authza':
        order = [db.Books.author_sort.desc(), db.Books.sort.desc()]
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
    if data == "read":
        return render_read_books(page, True, order=order)
    elif data == "author":
        return render_author_books(page, book_id, order)
    elif data == "search":
        term = request.args.get('query', None)
        offset = int(int(config.config_books_per_page) * (page - 1))
        return render_search_results(term, offset, order, config.config_books_per_page)
    elif data == "advsearch":
        term = json.loads(flask_session.get('query', '{}'))
        offset = int(int(config.config_books_per_page) * (page - 1))
        return render_adv_search_results(term, offset, order, config.config_books_per_page)
    elif data != "newest":
        abort(404)
    else:
        website = data
        entries, pagination = calibre_db.fill_indexpage(page, 0, db.Books,
                                                                list_filters.filter_expression(), order[0],
                                                                True, config.config_read_column, cards_only=True)

        try:
            title = _('Books (%(count)s)', count=pagination.total_count)
        except (AttributeError, TypeError):
            title = _('Books (%(count)s)', count=cwa_get_num_books_in_library())

        return render_title_template('index.html', entries=entries, pagination=pagination,
                                     title=title, page=website, order=order[1],
                                     list_filters=list_filters.filter_context(),
                                     setup_checklist=(setup_checklist() if website == "newest" and page == 1
                                                      else None))


def _latest_reader_positions(session, user_id, library_uuid, book_ids=None):
    """{book_id: (percent, format, last_modified)} of each book's newest scoped position."""
    positions = {}
    if not library_uuid:
        return positions
    query = (session.query(ub.ReaderPosition.book_id, ub.ReaderPosition.format,
                           ub.ReaderPosition.percent, ub.ReaderPosition.last_modified)
             .filter(ub.ReaderPosition.user_id == user_id,
                     ub.ReaderPosition.library_uuid == library_uuid))
    if book_ids is not None:
        if not book_ids:
            return positions
        query = query.filter(ub.ReaderPosition.book_id.in_(book_ids))
    rows = query.order_by(ub.ReaderPosition.last_modified.desc(),
                          ub.ReaderPosition.id.desc()).all()
    for book_id, fmt, percent, modified in rows:
        if book_id not in positions:
            positions[book_id] = (percent, fmt, modified)
    return positions


def _book_resume(user_id, book_id, reader_list):
    """{'format': fmt} for the book page's Continue button, or None for a book not started.

    The newest scoped position wins; the format is only kept
    when the browser can still read it.
    """
    try:
        pos = _latest_reader_positions(ub.session, user_id, _library_uuid(), {book_id}).get(book_id)
        raw, fmt = (pos[0], pos[1]) if pos else (None, None)
        if raw is None:
            legacy = (ub.session.query(ub.WebReaderProgress.percent)
                      .filter(ub.WebReaderProgress.user_id == user_id,
                              ub.WebReaderProgress.book_id == book_id).first())
            raw = legacy[0] if legacy else None
        if raw is None:
            return None
        float(raw)  # a position that is no number is no start
        return {'format': fmt if fmt in reader_list else None}
    except (TypeError, ValueError, OperationalError, InvalidRequestError):
        return None


def render_author_books(page, author_id, order):
    entries, pagination = calibre_db.fill_indexpage(page, 0,
                                                        db.Books,
                                                        and_(db.Books.authors.any(db.Authors.id == author_id),
                                                             list_filters.filter_expression()),
                                                        order[0],
                                                        True, config.config_read_column, cards_only=True)
    if entries is None or (not len(entries) and not list_filters.active_filters()):
        flash(_("That author has no books in your library any more."),
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


def render_read_books(page, are_read, as_xml=False, order=None):
    sort_param = order[0] if order else []
    if not are_read:
        # Neither finished nor in progress: a book being read is under Reading, not here too
        db_filter = list_filters.filter_expression({"status": "unread"})
    elif not config.config_read_column:
        db_filter = and_(ub.ReadBook.user_id == int(current_user.id),
                         ub.ReadBook.read_status == ub.ReadBook.STATUS_FINISHED)
    else:
        try:
            db_filter = db.cc_classes[config.config_read_column].value == True
        except (KeyError, AttributeError, IndexError):
            log.error("Custom Column No.{} does not exist in calibre database".format(config.config_read_column))
            if not as_xml:
                flash(_("Custom column %(column)d is missing from your library, so read status can't be shown.",
                        column=config.config_read_column),
                      category="error")
                return redirect(url_for("web.index"))
            return []  # ToDo: Handle error Case for opds

    entries, pagination = calibre_db.fill_indexpage(page, 0,
                                                            db.Books,
                                                            db_filter,
                                                            sort_param,
                                                            True, config.config_read_column, cards_only=not as_xml)

    if as_xml:
        return entries, pagination
    # The library shows only Finished; unread books are the OPDS feed's
    name = _('Finished') + ' (' + str(pagination.total_count) + ')'
    return render_title_template('index.html', entries=entries, pagination=pagination,
                                 title=name, page="read", order=order[1])


# ################################### Health Check ##################################################################

# Longruns the container needs beyond the web app itself: the ingest service moves new
# files from the ingest folder into the library, and the metadata change detector writes
# metadata edits back into the book files. Either one dying leaves the app serving pages
# with imports or write-back silently broken, so /health reports it.
_CRITICAL_LONGRUNS = ("cwa-ingest-service", "metadata-change-detector")

# s6-overlay v3 keeps its binaries in /command, which isn't always on the app's PATH
_S6_RC_FALLBACKS = ("/command/s6-rc", "/package/admin/s6-rc/command/s6-rc")


def _find_s6_rc():
    found = shutil.which("s6-rc")
    if found:
        return found
    return next((path for path in _S6_RC_FALLBACKS if os.access(path, os.X_OK)), None)


def _check_s6_service_status():
    """Return {service: "up" | "down" | "unknown"} for each critical longrun.

    Uses ``s6-rc -a list`` (the services s6-rc currently has up), which reads
    world-readable state and so works for the unprivileged abc user the app runs as;
    scripts/check-cwa-services.sh uses the same primitive. Outside the container (tests,
    local dev) there is no s6-rc, and a probe that fails or times out tells us nothing,
    so both report "unknown", which never marks the app unhealthy.
    """
    unknown = {service: "unknown" for service in _CRITICAL_LONGRUNS}
    s6_rc = _find_s6_rc()
    if not s6_rc:
        return unknown
    try:
        completed = subprocess.run([s6_rc, "-a", "list"], capture_output=True, text=True,
                                   timeout=2, check=False)
    except (subprocess.SubprocessError, OSError):
        return unknown
    if completed.returncode != 0:
        return unknown
    active = {line.strip() for line in completed.stdout.splitlines() if line.strip()}
    return {service: ("up" if service in active else "down") for service in _CRITICAL_LONGRUNS}


def _metadata_db_readable():
    try:
        db_path = os.path.join(cwa_get_library_location(), "metadata.db")
        # Read-only URI so a missing library reports unhealthy instead of creating
        # an empty metadata.db, and a real read so an unreadable file is caught.
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            conn.close()
        return True
    except Exception:
        return False


@web.route("/health")
def health_check():
    uptime = time.time() - _start_time
    db_up = _metadata_db_readable()
    services = _check_s6_service_status()
    healthy = db_up and "down" not in services.values()

    return jsonify({
        "status": "ok" if healthy else "degraded",
        "uptime": uptime,
        "version": f"Lily/{constants.INSTALLED_VERSION}",
        "database": "ok" if db_up else "unreadable",
        "services": services,
    }), 200 if healthy else 503

# ################################### View Books list ##################################################################

@web.route("/", defaults={'page': 1})
@web.route('/page/<int:page>')
@login_required_if_no_ano
def index(page):
    # Warn once per session: the architecture can't change, so repeating it on every visit is noise
    if current_user.is_authenticated and current_user.role_admin() and not flask_session.get('arch_warning_shown'):
        arch_warning = helper.check_architecture()
        if arch_warning:
            flash(arch_warning, category="cwa_arch_warning")
        flask_session['arch_warning_shown'] = True

    sort_param = (request.args.get('sort') or 'stored').lower()
    return render_books_list("newest", sort_param, 1, page)


@web.route('/<data>/<sort_param>', defaults={'page': 1, 'book_id': 1})
@web.route('/<data>/<sort_param>/', defaults={'page': 1, 'book_id': 1})
@web.route('/<data>/<sort_param>/<book_id>', defaults={'page': 1})
@web.route('/<data>/<sort_param>/<book_id>/<int:page>')
@login_required_if_no_ano
def books_list(data, sort_param, book_id, page):
    return render_books_list(data, sort_param, book_id, page)


# ###################################Show single book ##################################################################


@web.route("/read/<int:book_id>/<book_format>")
@login_required_if_no_ano
@viewer_required
def read_book(book_id, book_format):
    book = calibre_db.get_filtered_book(book_id)

    if not book:
        flash(_("That book isn't in your library any more, or its file can't be read."),
              category="error")
        log.debug("Selected book is unavailable. File does not exist or is not accessible")
        return redirect(url_for("web.index"))

    book.ordered_authors = calibre_db.order_authors([book], False)

    fmt_lower = book_format.lower()
    user_key = str(current_user.id) if current_user.is_authenticated else "anonymous"
    progress_args = {}
    progress_uuid = _library_uuid()
    if progress_uuid and (fmt_lower == "epub" or fmt_lower in PAGED_PROGRESS_FORMATS
                          or fmt_lower in constants.EXTENSIONS_AUDIO):
        progress_args = {
            "progress_key": "%s.%s.%s.%s" % (user_key, progress_uuid, book_id, fmt_lower),
            "progress_format": fmt_lower,
            "progress_url": url_for("web.web_reader_progress", book_id=book_id,
                                    format=fmt_lower),
        }

    if book_format.lower() == "epub":
        log.debug("Start epub reader for %d (%s)", book_id, book_format.lower())
        return render_title_template('read.html', bookid=book_id, title=book.title,
                                     book_format=book_format.lower(),
                                     **progress_args)
    elif book_format.lower() == "pdf":
        log.debug("Start pdf reader for %d", book_id)
        # The linearized copy opens on page 1 in a few ranges; until it's made, the file itself
        source = pdf_fast.source(book)
        fast = bool(source) and pdf_fast.ready_or_queue(book_id, source)
        return render_title_template('readpdf.html', pdffile=book_id, title=book.title,
                                     pdf_fast=fast, **progress_args)
    elif book_format.lower() in ["djvu", "djv"]:
        log.debug("Start djvu reader for %d", book_id)
        return render_title_template('readdjvu.html', djvufile=book_id, title=book.title,
                                     extension=book_format.lower(), **progress_args)
    else:
        for fileExt in constants.EXTENSIONS_AUDIO:
            if book_format.lower() == fileExt:
                entries = calibre_db.get_filtered_book(book_id)
                log.debug("Start mp3 listening for %d", book_id)
                return render_title_template('listenmp3.html', mp3file=book_id, audioformat=book_format.lower(),
                                             entry=entries, **progress_args)
        log.debug("Selected book is unavailable. File does not exist or is not accessible")
        flash(_("That book isn't in your library any more, or its file can't be read."),
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
    entries = calibre_db.get_book_read_status(book_id, config.config_read_column)
    if entries:
        entry, read_book = entries
        entry.read_status = read_book == ub.ReadBook.STATUS_FINISHED
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

        # Have the reader's fast copy of a big PDF ready by the time Read is pressed
        if "pdf" in entry.reader_list:
            pdf_fast.ready_or_queue(book_id, pdf_fast.source(entry))

        entry.audio_entries = []
        for media_format in entry.data:
            if media_format.format.lower() in constants.EXTENSIONS_AUDIO:
                entry.audio_entries.append(media_format.format.lower())

        resume = None
        if read_book == ub.ReadBook.STATUS_IN_PROGRESS and current_user.is_authenticated:
            resume = _book_resume(int(current_user.id), book_id, entry.reader_list)

        metadata_lookup = _metadata_lookup(CWA_DB(), book_id) if current_user.role_edit() else None

        from .editbooks import book_edition, book_volume

        # The Shelves menu: the shelves this user may change, each ticked when the book is on it
        shelf_menu = []
        if current_user.is_authenticated:
            from .editbooks import _editable_shelves, _book_shelf_ids
            on_ids = set(_book_shelf_ids(book_id))
            shelf_menu = [(shelf, shelf.id in on_ids) for shelf in _editable_shelves()]

        return render_title_template('detail.html',
                                     entry=entry,
                                     resume=resume,
                                     is_xhr=request.headers.get('X-Requested-With') == 'XMLHttpRequest',
                                     title=entry.title,
                                     metadata_lookup=metadata_lookup,
                                     edition=book_edition(book_id),
                                     volume=book_volume(book_id),
                                     shelf_menu=shelf_menu,
                                     page="book")
    else:
        log.debug("Selected book is unavailable. File does not exist or is not accessible")
        flash(_("That book isn't in your library any more, or its file can't be read."),
              category="error")
        return redirect(url_for("web.index"))

def _metadata_lookup(cwa_db, book_id):
    """What the book's last metadata lookup found, for its Metadata fact: {status, source,
    checked} with checked a datetime, or None when it has had none since they were recorded."""
    try:
        lookup = cwa_db.get_metadata_lookup(book_id)
        if lookup:
            lookup["checked"] = datetime.fromisoformat(lookup["checked_at"])
        return lookup
    except Exception as e:
        log.debug("No metadata lookup to show for book %s: %s", book_id, e)
        return None


from . import web_auth  # noqa: E402,F401  (attaches its routes to this blueprint)

from . import web_lists  # noqa: E402,F401  (attaches its routes to this blueprint)

from . import web_files  # noqa: E402,F401  (attaches its routes to this blueprint)

from . import web_typeahead  # noqa: E402,F401  (attaches its routes to this blueprint)
