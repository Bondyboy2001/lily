# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The in-progress books query (web._in_progress_rows), which orders the Reading list's
progress."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub

from .lily_env import in_progress_rows


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
        # the web reader stores its position as a 0..1 fraction
        session.add(ub.WebReaderProgress(user_id=user_id, book_id=book_id, cfi="x",
                                         percent=None if percent is None else percent / 100.0))
    session.commit()
    # onupdate hooks may have bumped timestamps; pin them explicitly
    session.query(ub.ReadBook).filter_by(id=rb.id).update(
        {ub.ReadBook.last_modified: BASE + timedelta(minutes=minutes)}, synchronize_session=False)
    if percent is not None or bookmark_minutes is not None:
        session.query(ub.WebReaderProgress).filter_by(user_id=user_id, book_id=book_id).update(
            {ub.WebReaderProgress.last_modified: BASE + timedelta(minutes=bookmark_minutes or minutes)},
            synchronize_session=False)
    session.commit()


@pytest.mark.unit
class TestInProgressRows:
    def test_only_in_progress_books_for_user(self, session):
        _read(session, 1, 10, ub.ReadBook.STATUS_IN_PROGRESS, 1)
        _read(session, 1, 11, ub.ReadBook.STATUS_FINISHED, 2)
        _read(session, 1, 12, ub.ReadBook.STATUS_UNREAD, 3)
        _read(session, 2, 13, ub.ReadBook.STATUS_IN_PROGRESS, 4)
        assert in_progress_rows(session, 1) == [(10, None)]

    def test_orders_by_most_recent_activity(self, session):
        _read(session, 1, 10, ub.ReadBook.STATUS_IN_PROGRESS, 1)
        _read(session, 1, 11, ub.ReadBook.STATUS_IN_PROGRESS, 5)
        # older ReadBook row, but its web reader position was updated most recently
        _read(session, 1, 12, ub.ReadBook.STATUS_IN_PROGRESS, 0, percent=42.5, bookmark_minutes=10)
        ids = [book_id for book_id, __ in in_progress_rows(session, 1)]
        assert ids == [12, 11, 10]

    def test_progress_percent_is_clamped(self, session):
        _read(session, 1, 10, ub.ReadBook.STATUS_IN_PROGRESS, 1, percent=150.0)
        _read(session, 1, 11, ub.ReadBook.STATUS_IN_PROGRESS, 2, percent=33.3)
        result = dict(in_progress_rows(session, 1))
        assert result[10] == 100.0
        assert result[11] == pytest.approx(33.3)

    def test_limit_and_empty(self, session):
        assert in_progress_rows(session, 1) == []
        for i in range(10):
            _read(session, 1, 100 + i, ub.ReadBook.STATUS_IN_PROGRESS, i)
        # helper over-fetches (3x) so hidden books can be skipped by the caller
        assert len(in_progress_rows(session, 1, limit=2)) == 6
