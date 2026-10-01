# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Next book in a series, for the reader's end-of-book card and the book page.

``pick_next`` is pure (no Flask, no database); ``next_in_series`` runs the query with the
current user's visibility filters, so a hidden or archived book is never offered."""

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence, Tuple


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pick_next(current_id: int, current_index: Any,
              candidates: Iterable[Tuple[int, Any]]) -> Optional[int]:
    """The id of the book that follows ``current_index``, or None.

    ``candidates`` are ``(book_id, series_index)`` pairs of the books in the same series.
    The next book has the smallest index strictly above the current one; ties go to the
    lower id. Books with an unreadable index, or the current book itself, are skipped."""
    here = _as_float(current_index)
    if here is None:
        return None
    best: Optional[Tuple[float, int]] = None
    for book_id, index in candidates:
        value = _as_float(index)
        if book_id == current_id or value is None or value <= here:
            continue
        if best is None or (value, book_id) < best:
            best = (value, book_id)
    return best[1] if best else None


def next_in_series(calibre_db: Any, book: Any) -> Optional[Any]:
    """The next visible book in ``book``'s first series (a ``db.Books``), or None."""
    from . import db

    series: Sequence[Any] = getattr(book, "series", None) or []
    if not series:
        return None
    calibre_db.ensure_session()
    rows = (calibre_db.session.query(db.Books.id, db.Books.series_index)
            .join(db.books_series_link, db.books_series_link.c.book == db.Books.id)
            .filter(db.books_series_link.c.series == series[0].id)
            .filter(calibre_db.common_filters())
            .all())
    next_id = pick_next(book.id, book.series_index, rows)
    if next_id is None:
        return None
    return calibre_db.get_filtered_book(next_id)
