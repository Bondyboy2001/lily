# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""End-to-end tests for the Kobo /v1/library/sync handler (cps/kobo.py HandleSyncRequest)
and for push_reading_state_to_hardcover.

HandleSyncRequest is driven through a real Flask test client against a real app.db and
Calibre metadata.db (see lily_env.py), authenticated with a real Kobo auth token.
"""

import base64
import json
import threading
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.unit.lily_env import lily_env

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path):
    with lily_env(tmp_path) as e:
        yield e


def _sync(env, token, sync_token=None):
    headers = {"x-kobo-synctoken": sync_token} if sync_token else {}
    resp = env.app.test_client().get(f"/kobo/{token}/v1/library/sync", headers=headers)
    return resp, json.loads(resp.data) if resp.status_code == 200 else None


def _decode_token(resp):
    raw = resp.headers["x-kobo-synctoken"]
    return json.loads(base64.b64decode(raw))["data"]


def _mark_synced(env, user, book_ids):
    ub = env.ub
    for book_id in book_ids:
        ub.session.add(ub.KoboSyncedBooks(user_id=user.id, book_id=book_id))
    ub.session.commit()


def _add_state(env, user, book_id, when, percent=25.0):
    """Create a ReadBook + KoboReadingState (with bookmark/statistics) last modified at ``when``."""
    ub = env.ub
    read = ub.ReadBook(user_id=user.id, book_id=book_id, read_status=ub.ReadBook.STATUS_IN_PROGRESS)
    state = ub.KoboReadingState(user_id=user.id, book_id=book_id)
    state.current_bookmark = ub.KoboBookmark(progress_percent=percent)
    state.statistics = ub.KoboStatistics()
    read.kobo_reading_state = state
    ub.session.add(read)
    ub.session.commit()
    # Pin the timestamp (defaults/onupdate hooks would otherwise use "now").
    ub.session.query(ub.KoboReadingState).filter_by(id=state.id).update(
        {ub.KoboReadingState.last_modified: when}, synchronize_session=False)
    ub.session.commit()
    return state.id


def _changed_states(results):
    return [r["ChangedReadingState"]["ReadingState"] for r in results if "ChangedReadingState" in r]


def _uuid(env, book_id):
    from cps import calibre_db
    return calibre_db.get_book(book_id).uuid


# ---------------------------------------------------------------------------
# HandleSyncRequest: entitlements + auth
# ---------------------------------------------------------------------------

class TestSyncEntitlements:
    def test_invalid_token_is_rejected(self, env):
        resp, _ = _sync(env, "not-a-real-token")
        assert resp.status_code == 401

    def test_user_without_download_role_gets_403(self, env):
        from cps import constants
        user = env.add_user("nodl", role=constants.ROLE_USER)
        resp, _ = _sync(env, env.kobo_token(user))
        assert resp.status_code == 403

    def test_first_sync_returns_new_entitlements_for_kobo_formats_only(self, env):
        epub = env.add_book("Epub Book", fmt="EPUB")
        kepub = env.add_book("Kepub Book", fmt="KEPUB")
        env.add_book("Pdf Book", fmt="PDF")
        user = env.admin()
        resp, results = _sync(env, env.kobo_token(user))
        assert resp.status_code == 200
        ids = {r["NewEntitlement"]["BookEntitlement"]["Id"] for r in results if "NewEntitlement" in r}
        assert ids == {_uuid(env, epub), _uuid(env, kepub)}
        assert "x-kobo-sync" not in resp.headers

        env.ub.session.expire_all()
        synced = {row.book_id for row in env.ub.session.query(env.ub.KoboSyncedBooks)
                  .filter_by(user_id=user.id)}
        assert synced == {epub, kepub}

    def test_second_sync_with_token_returns_nothing_new(self, env):
        env.add_book("Only Book")
        token = env.kobo_token(env.admin())
        first, results = _sync(env, token)
        assert len(results) == 1
        _, again = _sync(env, token, first.headers["x-kobo-synctoken"])
        assert again == []

    def test_book_limit_sets_continue_header(self, env, monkeypatch):
        import cps.kobo as kobo
        monkeypatch.setattr(kobo, "SYNC_ITEM_LIMIT", 2)
        for i in range(3):
            env.add_book(f"Book {i}")
        token = env.kobo_token(env.admin())
        first, results = _sync(env, token)
        assert len(results) == 2
        assert first.headers.get("x-kobo-sync") == "continue"
        second, rest = _sync(env, token, first.headers["x-kobo-synctoken"])
        assert len(rest) == 1
        assert "x-kobo-sync" not in second.headers


# ---------------------------------------------------------------------------
# HandleSyncRequest: changed reading states (books already on the device)
# ---------------------------------------------------------------------------

class TestChangedReadingStates:
    def test_states_for_existing_books_are_returned_in_order(self, env):
        user = env.admin()
        b1, b2 = env.add_book("One"), env.add_book("Two")
        _mark_synced(env, user, [b1, b2])
        _add_state(env, user, b2, datetime(2026, 3, 1, 10, 0, 0), percent=40.0)
        _add_state(env, user, b1, datetime(2026, 3, 1, 9, 0, 0), percent=10.0)

        resp, results = _sync(env, env.kobo_token(user))
        states = _changed_states(results)
        assert [s["EntitlementId"] for s in states] == [_uuid(env, b1), _uuid(env, b2)]
        assert states[1]["CurrentBookmark"]["ProgressPercent"] == 40.0
        assert states[0]["StatusInfo"]["Status"] == "Reading"
        assert "x-kobo-sync" not in resp.headers

    def test_states_for_missing_books_are_skipped(self, env):
        user = env.admin()
        b1 = env.add_book("Present")
        _mark_synced(env, user, [b1])
        _add_state(env, user, 99999, datetime(2026, 3, 1, 8, 0, 0))  # book deleted from Calibre
        _add_state(env, user, b1, datetime(2026, 3, 1, 9, 0, 0))

        _, results = _sync(env, env.kobo_token(user))
        states = _changed_states(results)
        assert [s["EntitlementId"] for s in states] == [_uuid(env, b1)]

    def test_other_users_states_are_not_returned(self, env):
        admin = env.admin()
        other = env.add_user("reader2")
        b1 = env.add_book("Shared")
        _mark_synced(env, admin, [b1])
        _add_state(env, other, b1, datetime(2026, 3, 1, 9, 0, 0))
        _, results = _sync(env, env.kobo_token(admin))
        assert _changed_states(results) == []

    def test_reading_state_last_modified_advances(self, env):
        user = env.admin()
        b1, b2 = env.add_book("One"), env.add_book("Two")
        _mark_synced(env, user, [b1, b2])
        latest = datetime(2026, 3, 2, 12, 30, 0)
        _add_state(env, user, b1, datetime(2026, 3, 1, 9, 0, 0))
        _add_state(env, user, b2, latest)

        resp, _ = _sync(env, env.kobo_token(user))
        token = _decode_token(resp)
        assert token["reading_state_last_modified"] == (latest - datetime(1970, 1, 1)).total_seconds()

        # Replaying the returned token yields no already-delivered states.
        _, again = _sync(env, env.kobo_token(user), resp.headers["x-kobo-synctoken"])
        assert _changed_states(again) == []

    def test_state_newer_than_token_is_returned_on_next_sync(self, env):
        user = env.admin()
        b1, b2 = env.add_book("One"), env.add_book("Two")
        _mark_synced(env, user, [b1, b2])
        token = env.kobo_token(user)
        _add_state(env, user, b1, datetime(2026, 3, 1, 9, 0, 0))
        first, _ = _sync(env, token)
        _add_state(env, user, b2, datetime(2026, 3, 1, 10, 0, 0))
        _, results = _sync(env, token, first.headers["x-kobo-synctoken"])
        assert [s["EntitlementId"] for s in _changed_states(results)] == [_uuid(env, b2)]

    def test_more_than_limit_sets_continue_and_pages_through_all(self, env, monkeypatch):
        import cps.kobo as kobo
        monkeypatch.setattr(kobo, "SYNC_ITEM_LIMIT", 3)
        user = env.admin()
        books = [env.add_book(f"Book {i}") for i in range(5)]
        _mark_synced(env, user, books)
        for i, book_id in enumerate(books):
            _add_state(env, user, book_id, datetime(2026, 3, 1, 9, i, 0))
        token = env.kobo_token(user)

        first, results = _sync(env, token)
        page1 = _changed_states(results)
        assert len(page1) == 3
        assert first.headers.get("x-kobo-sync") == "continue"

        second, results = _sync(env, token, first.headers["x-kobo-synctoken"])
        page2 = _changed_states(results)
        assert len(page2) == 2
        assert "x-kobo-sync" not in second.headers
        seen = [s["EntitlementId"] for s in page1 + page2]
        assert seen == [_uuid(env, b) for b in books]

    def test_exactly_limit_states_does_not_continue(self, env, monkeypatch):
        import cps.kobo as kobo
        monkeypatch.setattr(kobo, "SYNC_ITEM_LIMIT", 3)
        user = env.admin()
        books = [env.add_book(f"Book {i}") for i in range(3)]
        _mark_synced(env, user, books)
        for i, book_id in enumerate(books):
            _add_state(env, user, book_id, datetime(2026, 3, 1, 9, i, 0))
        resp, results = _sync(env, env.kobo_token(user))
        assert len(_changed_states(results)) == 3
        assert "x-kobo-sync" not in resp.headers

    def test_page_of_only_missing_books_still_makes_progress(self, env, monkeypatch):
        import cps.kobo as kobo
        monkeypatch.setattr(kobo, "SYNC_ITEM_LIMIT", 2)
        user = env.admin()
        b1 = env.add_book("Present")
        _mark_synced(env, user, [b1])
        _add_state(env, user, 90001, datetime(2026, 3, 1, 8, 0, 0))
        _add_state(env, user, 90002, datetime(2026, 3, 1, 8, 1, 0))
        _add_state(env, user, b1, datetime(2026, 3, 1, 9, 0, 0))
        token = env.kobo_token(user)
        first, _ = _sync(env, token)
        second, results = _sync(env, token, first.headers["x-kobo-synctoken"])
        assert [s["EntitlementId"] for s in _changed_states(results)] == [_uuid(env, b1)]


# ---------------------------------------------------------------------------
# push_reading_state_to_hardcover
# ---------------------------------------------------------------------------

class _SlowClient:
    """Stand-in for hardcover.HardcoverClient whose update blocks until released."""
    instances = []

    def __init__(self, token):
        self.token = token
        self.release = threading.Event()
        self.called = threading.Event()
        self.finished = threading.Event()
        self.args = None
        self.raise_error = None
        _SlowClient.instances.append(self)

    def parse_identifiers(self, identifiers):
        return {i.type: i.val for i in identifiers}

    def update_reading_progress(self, identifiers, progress):
        self.args = (identifiers, progress)
        self.called.set()
        try:
            if self.raise_error:
                raise self.raise_error
            self.release.wait(5)
        finally:
            self.finished.set()


@pytest.fixture
def hardcover_env(monkeypatch):
    """Patch kobo's module globals so push_reading_state_to_hardcover runs without a DB."""
    import cps.kobo as kobo
    from cps.services import hardcover as real_hardcover

    _SlowClient.instances = []
    fake_hardcover = SimpleNamespace(HardcoverClient=_SlowClient,
                                     MissingHardcoverToken=real_hardcover.MissingHardcoverToken)
    monkeypatch.setattr(kobo, "hardcover", fake_hardcover)
    monkeypatch.setattr(kobo, "config", SimpleNamespace(config_hardcover_sync=True))
    blacklist = {"row": None}
    query = MagicMock()
    query.filter.return_value.first.side_effect = lambda: blacklist["row"]
    monkeypatch.setattr(kobo.ub, "session", MagicMock(query=MagicMock(return_value=query)))
    logged = MagicMock()
    monkeypatch.setattr(kobo, "log", logged)
    return SimpleNamespace(kobo=kobo, blacklist=blacklist, log=logged)


def _hc_book(book_id=7):
    return SimpleNamespace(id=book_id, identifiers=[SimpleNamespace(type="isbn", val="9780000000001")])


_HC_USER = SimpleNamespace(name="reader", hardcover_token="tok")


class TestPushReadingStateToHardcover:
    def test_returns_without_waiting_for_slow_client(self, hardcover_env):
        start = time.monotonic()
        hardcover_env.kobo.push_reading_state_to_hardcover(_HC_USER, _hc_book(), 42)
        elapsed = time.monotonic() - start
        client = _SlowClient.instances[0]
        try:
            assert client.called.wait(2), "update should run on a background thread"
            assert elapsed < 1.0
            assert not client.finished.is_set(), "request thread returned before the push finished"
            assert client.args == ({"isbn": "9780000000001"}, 42)
            worker = [t for t in threading.enumerate() if t.name == "hardcover-progress"]
            assert worker and all(t.daemon for t in worker)
        finally:
            client.release.set()
            client.finished.wait(2)

    def test_errors_in_background_thread_are_logged_not_raised(self, hardcover_env):
        original = _SlowClient.__init__

        def failing_init(self, token):
            original(self, token)
            self.raise_error = RuntimeError("hardcover down")

        _SlowClient.__init__ = failing_init
        try:
            hardcover_env.kobo.push_reading_state_to_hardcover(_HC_USER, _hc_book(11), 50)
        finally:
            _SlowClient.__init__ = original
        client = _SlowClient.instances[0]
        assert client.finished.wait(2)
        for _ in range(100):
            if hardcover_env.log.error.called:
                break
            time.sleep(0.01)
        message = hardcover_env.log.error.call_args.args[0]
        assert "book 11" in message and "hardcover down" in message

    def test_blacklisted_book_is_skipped(self, hardcover_env):
        hardcover_env.blacklist["row"] = SimpleNamespace(blacklist_reading_progress=True)
        hardcover_env.kobo.push_reading_state_to_hardcover(_HC_USER, _hc_book(), 10)
        assert _SlowClient.instances == []

    def test_blacklist_row_without_progress_flag_still_pushes(self, hardcover_env):
        hardcover_env.blacklist["row"] = SimpleNamespace(blacklist_reading_progress=False)
        hardcover_env.kobo.push_reading_state_to_hardcover(_HC_USER, _hc_book(), 10)
        client = _SlowClient.instances[0]
        client.release.set()
        assert client.called.wait(2)

    def test_missing_token_skips_push(self, hardcover_env, monkeypatch):
        from cps.services import hardcover as real_hardcover

        def no_token(token):
            raise real_hardcover.MissingHardcoverToken()

        monkeypatch.setattr(hardcover_env.kobo.hardcover, "HardcoverClient", no_token)
        hardcover_env.kobo.push_reading_state_to_hardcover(_HC_USER, _hc_book(), 10)
        assert hardcover_env.log.info.called
        assert not hardcover_env.log.error.called

    def test_disabled_sync_does_nothing(self, hardcover_env, monkeypatch):
        monkeypatch.setattr(hardcover_env.kobo, "config", SimpleNamespace(config_hardcover_sync=False))
        hardcover_env.kobo.push_reading_state_to_hardcover(_HC_USER, _hc_book(), 10)
        assert _SlowClient.instances == []
