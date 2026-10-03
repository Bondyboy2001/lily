# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Books that reached the library since a point: what Refresh library and the ingest
folder added, for the toast that says so (lily.js). The point is the newest book id then,
as ids only grow (timestamps were written in local time by older imports)."""

from sqlalchemy import func
from flask_babel import gettext as _, ngettext

from . import calibre_db, db, logger

log = logger.create()


def newest_book_id():
    """The highest book id in the library now (0 when it is empty or can't be read)."""
    try:
        return calibre_db.session.query(func.max(db.Books.id)).scalar() or 0
    except Exception as e:
        log.debug("Could not read the newest book id: %s", e)
        return 0


def books_added_after(book_id):
    """[(id, title)] of books added after `book_id` that the user may see, newest first."""
    if book_id is None:
        return []
    try:
        return (calibre_db.session.query(db.Books.id, db.Books.title)
                .filter(db.Books.id > int(book_id))
                .filter(calibre_db.common_filters())
                .order_by(db.Books.id.desc())
                .limit(100)
                .all())
    except Exception as e:
        log.debug("Could not list books added after %s: %s", book_id, e)
        return []


def added_summary(books):
    """{count, book_id, message} for the toast: one book by its title, several by number."""
    count = len(books)
    summary = {"count": count, "book_id": books[0][0] if count == 1 else None}
    if count == 1:
        summary["message"] = _("Added %(name)s to the library", name=books[0][1])
    elif count:
        summary["message"] = ngettext("Added %(num)s book to the library",
                                      "Added %(num)s books to the library", count)
    else:
        summary["message"] = _("No new books found")
    return summary
