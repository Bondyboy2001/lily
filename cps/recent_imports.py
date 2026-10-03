# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Books that reached the library since a point: what Refresh library and the ingest
folder added, for the toast that says so (lily.js). The point is the newest book id then,
as ids only grow (timestamps were written in local time by older imports)."""

import logging
import os
import zipfile

from sqlalchemy import func
from flask_babel import gettext as _, ngettext

from . import calibre_db, config, db, logger

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


def earlier_copy(book_id):
    """The id of the oldest other book with the same title and authors as `book_id`, or None:
    an upload of a book the library already had."""
    try:
        book = calibre_db.get_book(book_id)
        if not book:
            return None
        authors = sorted(db.lcase(a.name) for a in book.authors)
        others = (calibre_db.session.query(db.Books)
                  .filter(func.lower(db.Books.title) == db.lcase(book.title))
                  .filter(db.Books.id != book.id)
                  .filter(calibre_db.common_filters())
                  .order_by(db.Books.id)
                  .all())
        for other in others:
            if sorted(db.lcase(a.name) for a in other.authors) == authors:
                return other.id
    except Exception as e:
        log.debug("Could not look for an earlier copy of book %s: %s", book_id, e)
    return None


def _opens(path, book_format):
    """Whether a reader could open the file: a PDF pypdf can read, an EPUB that is a zip with
    its container file, a DjVu with its header. Other formats aren't checked."""
    if book_format == "pdf":
        pypdf_log = logging.getLogger("pypdf")
        level = pypdf_log.level
        pypdf_log.setLevel(logging.CRITICAL)
        try:
            from pypdf import PdfReader
            with open(path, "rb") as pdf:
                return len(PdfReader(pdf).pages) > 0
        except Exception:
            return False
        finally:
            pypdf_log.setLevel(level)
    if book_format == "epub":
        try:
            with zipfile.ZipFile(path) as epub:
                return "META-INF/container.xml" in epub.namelist()
        except (zipfile.BadZipFile, OSError):
            return False
    if book_format in ("djvu", "djv"):
        try:
            with open(path, "rb") as djvu:
                return djvu.read(8) == b"AT&TFORM"
        except OSError:
            return False
    return True


def unreadable_formats(book_id):
    """The formats of `book_id` whose file is missing or can't be opened, e.g. ["PDF"]."""
    try:
        book = calibre_db.get_book(book_id)
        if not book:
            return []
        broken = []
        for data in book.data:
            book_format = data.format.lower()
            path = os.path.join(config.get_book_path(), book.path, data.name + "." + book_format)
            if not os.path.isfile(path) or not _opens(path, book_format):
                broken.append(data.format.upper())
        return broken
    except Exception as e:
        log.debug("Could not check the files of book %s: %s", book_id, e)
        return []
