# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Unit tests for the home page "Continue reading" query helper."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.web import get_continue_reading_progress


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    sess = sessionmaker(bind=engine)()
    yield sess
    sess.close()


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _read(session, user_id, book_id, status, minutes, percent=None, bookmark_minutes=None):
    rb = ub.ReadBook(user_id=user_id, book_id=book_id, read_status=status)
    session.add(rb)
    session.flush()
    rb.last_modified = BASE + timedelta(minutes=minutes)
    if percent is not None or bookmark_minutes is not None:
        state = ub.KoboReadingState(user_id=user_id, book_id=book_id)
        state.current_bookmark = ub.KoboBookmark(progress_percent=percent)
        session.add(state)
        session.flush()
        state.current_bookmark.last_modified = BASE + timedelta(minutes=bookmark_minutes or minutes)
    session.commit()
    # onupdate hooks may have bumped timestamps; pin them explicitly
    session.query(ub.ReadBook).filter_by(id=rb.id).update(
        {ub.ReadBook.last_modified: BASE + timedelta(minutes=minutes)}, synchronize_session=False)
    session.commit()


@pytest.mark.unit
class TestContinueReadingProgress:
    def test_only_in_progress_books_for_user(self, session):
        _read(session, 1, 10, ub.ReadBook.STATUS_IN_PROGRESS, 1)
        _read(session, 1, 11, ub.ReadBook.STATUS_FINISHED, 2)
        _read(session, 1, 12, ub.ReadBook.STATUS_UNREAD, 3)
        _read(session, 2, 13, ub.ReadBook.STATUS_IN_PROGRESS, 4)
        assert get_continue_reading_progress(session, 1) == [(10, None)]

    def test_orders_by_most_recent_activity(self, session):
        _read(session, 1, 10, ub.ReadBook.STATUS_IN_PROGRESS, 1)
        _read(session, 1, 11, ub.ReadBook.STATUS_IN_PROGRESS, 5)
        # older ReadBook row, but its Kobo bookmark was updated most recently
        _read(session, 1, 12, ub.ReadBook.STATUS_IN_PROGRESS, 0, percent=42.5, bookmark_minutes=10)
        ids = [book_id for book_id, __ in get_continue_reading_progress(session, 1)]
        assert ids == [12, 11, 10]

    def test_progress_percent_is_clamped(self, session):
        _read(session, 1, 10, ub.ReadBook.STATUS_IN_PROGRESS, 1, percent=150.0)
        _read(session, 1, 11, ub.ReadBook.STATUS_IN_PROGRESS, 2, percent=33.3)
        result = dict(get_continue_reading_progress(session, 1))
        assert result[10] == 100.0
        assert result[11] == pytest.approx(33.3)

    def test_limit_and_empty(self, session):
        assert get_continue_reading_progress(session, 1) == []
        for i in range(10):
            _read(session, 1, 100 + i, ub.ReadBook.STATUS_IN_PROGRESS, i)
        # helper over-fetches (3x) so hidden books can be skipped by the caller
        assert len(get_continue_reading_progress(session, 1, limit=2)) == 6
