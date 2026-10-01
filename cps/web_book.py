# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The book page's reading helpers: which format "Read" opens, the saved reading position,
the next book in the series (series_nav), and "Convert to EPUB" for books no reader opens.

Routes are attached to the web blueprint; web.py imports this module at its end."""

from flask import abort, flash, redirect, url_for
from flask_babel import gettext as _

from . import calibre_db, config, helper, logger, series_nav, ub
from .cw_login import current_user
from .services.worker import STAT_STARTED, STAT_WAITING, WorkerThread
from .tasks.convert import TaskConvert
from .usermanagement import user_login_required
from .web import web

log = logger.create()

# What "Convert to EPUB" starts from, best first. PDF and comics are left out: they already
# open in a reader and convert poorly.
EPUB_SOURCE_ORDER = ('azw3', 'mobi', 'azw', 'fb2', 'fbz', 'docx', 'odt', 'rtf', 'lit', 'htmlz',
                     'html', 'htm', 'prc', 'pdb', 'lrf', 'chm', 'txtz')
# A book holding one of these and no EPUB is offered the conversion even if it opens otherwise.
EPUB_WORTHY_SOURCES = frozenset({'azw3', 'mobi', 'fb2', 'docx'})


@web.app_template_filter('reader_formats')
def reader_formats(book):
    """Formats of `book` a built-in reader opens, best first (helper.check_read_formats)."""
    return helper.check_read_formats(book)


def can_convert_books(user):
    return bool(user.is_authenticated and (user.role_admin() or user.role_edit()))


def epub_conversion_source(book):
    """The format to convert to EPUB from, or None when the book has an EPUB or nothing to
    convert. Offered when nothing opens in a reader, or the book has an e-book format (MOBI,
    AZW3, FB2, DOCX) that is better read as EPUB."""
    formats = {d.format.lower() for d in book.data}
    if 'epub' in formats:
        return None
    readable = helper.readable_formats(formats)
    if readable and not formats & EPUB_WORTHY_SOURCES:
        return None
    return next((fmt for fmt in EPUB_SOURCE_ORDER if fmt in formats), None)


def epub_conversion_queued(book_id):
    """True while a conversion of this book is waiting or running in the worker."""
    worker = WorkerThread._instance  # never start the worker just to look
    if worker is None:
        return False
    for queued in worker.tasks:
        task = queued.task
        if isinstance(task, TaskConvert) and task.book_id == book_id and task.stat in (STAT_WAITING, STAT_STARTED):
            return True
    return False


def reader_progress_percent(user_id, book_id):
    """The web reader's saved position as 0-100, or None when the book was never opened in it.

    A position without a percentage (an old row) reads as 0."""
    row = (ub.session.query(ub.WebReaderProgress.percent)
           .filter(ub.WebReaderProgress.user_id == user_id, ub.WebReaderProgress.book_id == book_id)
           .first())
    if row is None:
        return None
    try:
        return max(0, min(100, int(round(float(row[0] or 0) * 100))))
    except (TypeError, ValueError):
        return 0


def book_page_context(entry):
    """Extra template values for detail.html."""
    read_progress = None
    if current_user.is_authenticated and not current_user.is_anonymous:
        read_progress = reader_progress_percent(int(current_user.id), entry.id)
    convert_from = epub_conversion_source(entry) if can_convert_books(current_user) else None
    return {
        'read_progress': read_progress,
        'next_in_series': series_nav.next_in_series(calibre_db, entry),
        'convert_from': convert_from,
        'convert_queued': bool(convert_from) and epub_conversion_queued(entry.id),
    }


@web.route("/book/<int:book_id>/convert-epub", methods=["POST"])
@user_login_required
def convert_to_epub(book_id):
    if not can_convert_books(current_user):
        abort(403)
    book = calibre_db.get_filtered_book(book_id, allow_show_archived=True)
    if not book:
        abort(404)
    source = epub_conversion_source(book)
    if not source:
        flash(_("This book has nothing to convert to EPUB."), category="error")
    elif epub_conversion_queued(book_id):
        flash(_("This book is already being converted. Progress is on the Tasks page."), category="info")
    else:
        log.info("Converting book %s from %s to EPUB", book_id, source.upper())
        error = helper.convert_book_format(book_id, config.get_book_path(), source.upper(), 'EPUB',
                                           current_user.name)
        if error:
            flash(_("Could not convert this book: %(error)s", error=error), category="error")
        else:
            flash(_("Converting %(format)s to EPUB. Progress is on the Tasks page.", format=source.upper()),
                  category="success")
    return redirect(url_for('web.show_book', book_id=book_id))
