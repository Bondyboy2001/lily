# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The book page's reading helpers: which format "Read" opens, the saved reading position
and the next book in the series (series_nav).

web.py imports this module at its end so the template filter is registered on its blueprint."""

from . import calibre_db, helper, series_nav, ub
from .cw_login import current_user
from . import web as web_module
from .web import web


@web.app_template_filter('reader_formats')
def reader_formats(book):
    """Formats of `book` a built-in reader opens, best first (helper.check_read_formats)."""
    return helper.check_read_formats(book)


def reader_progress_percent(user_id, book_id):
    """The web reader's saved position as 0-100, or None when the book was never opened in it.

    The newest position saved for this library (any format) wins; an old unscoped
    web_reader_progress row is the fallback. A position without a percentage reads as 0."""
    pos = web_module._latest_reader_positions(ub.session, user_id, web_module._library_uuid(),
                                              book_ids={book_id}).get(book_id)
    if pos is not None:
        raw = pos[0]
    else:
        row = (ub.session.query(ub.WebReaderProgress.percent)
               .filter(ub.WebReaderProgress.user_id == user_id, ub.WebReaderProgress.book_id == book_id)
               .first())
        if row is None:
            return None
        raw = row[0]
    try:
        return max(0, min(100, int(round(float(raw or 0) * 100))))
    except (TypeError, ValueError):
        return 0


def book_page_context(entry):
    """Extra template values for detail.html."""
    read_progress = None
    if current_user.is_authenticated and not current_user.is_anonymous:
        read_progress = reader_progress_percent(int(current_user.id), entry.id)
    return {
        'read_progress': read_progress,
        'next_in_series': series_nav.next_in_series(calibre_db, entry),
    }
