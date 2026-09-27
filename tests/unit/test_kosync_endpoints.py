# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""KOReader sync server endpoints (cps/progress_syncing/protocols/kosync.py) exercised through
a Flask test client against a real app.db + metadata.db (see lily_env.py).

Note: this server authenticates with HTTP Basic auth (the Lily KOReader plugin), not the
upstream koreader-sync-server x-auth-user / x-auth-key headers.
"""

import base64
import sys

import pytest

# Imported at collection time (like test_kosync_helpers.py): importing it lazily inside a
# fixture trips a pyOpenSSL/cryptography import quirk in the Google API client chain.
import cps.progress_syncing.protocols.kosync  # noqa: F401
from cps.progress_syncing.models import KOSyncProgress
from cps.progress_syncing.protocols.kosync import get_book_by_checksum
from tests.unit.lily_env import lily_env

# The protocols package re-exports the blueprint under the same name, so fetch the module itself.
kosync = sys.modules["cps.progress_syncing.protocols.kosync"]

pytestmark = pytest.mark.unit

DOC = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(kosync, "is_koreader_sync_enabled", lambda: True)
    with lily_env(tmp_path) as e:
        e.reader = e.add_user("koreader", password="s3cret:with:colons")
        yield e


def _basic(name, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{name}:{password}".encode()).decode()}


def _reader(env):
    return _basic("koreader", "s3cret:with:colons")


def _put(env, headers, **body):
    payload = {"document": DOC, "progress": "/body/DocFragment[3]/p[7]", "percentage": 0.4567,
               "device": "Kobo Libra", "device_id": "dev-1"}
    payload.update(body)
    payload = {k: v for k, v in payload.items() if v is not None}
    return env.app.test_client().put("/kosync/syncs/progress", json=payload, headers=headers)


def _get(env, headers, document=DOC):
    return env.app.test_client().get(f"/kosync/syncs/progress/{document}", headers=headers)


class TestAuth:
    def test_valid_credentials(self, env):
        resp = env.app.test_client().get("/kosync/users/auth", headers=_reader(env))
        assert resp.status_code == 200
        assert resp.get_json() == {"authorized": "OK"}

    def test_username_is_case_insensitive(self, env):
        resp = env.app.test_client().get("/kosync/users/auth", headers=_basic("KOReader", "s3cret:with:colons"))
        assert resp.status_code == 200

    @pytest.mark.parametrize("headers", [
        {},
        {"Authorization": "Bearer abc"},
        {"Authorization": "Basic !!!not-base64!!!"},
        {"Authorization": "Basic " + base64.b64encode(b"no-colon").decode()},
        _basic("koreader", "wrong"),
        _basic("nobody", "s3cret:with:colons"),
        _basic("bad:name", "x"),
    ], ids=["missing", "not-basic", "bad-base64", "no-colon", "wrong-password", "unknown-user",
            "colon-in-user"])
    def test_bad_credentials_are_401(self, env, headers):
        resp = env.app.test_client().get("/kosync/users/auth", headers=headers)
        assert resp.status_code == 401
        assert resp.get_json()["error"] == 2001

    def test_disabled_feature_returns_503(self, env, monkeypatch):
        monkeypatch.setattr(kosync, "is_koreader_sync_enabled", lambda: False)
        resp = env.app.test_client().get("/kosync/users/auth", headers=_reader(env))
        assert resp.status_code == 503
        assert _put(env, _reader(env)).status_code == 503

    def test_progress_endpoints_reject_bad_credentials(self, env):
        for resp in (_get(env, _basic("koreader", "wrong")), _put(env, _basic("koreader", "wrong"))):
            assert resp.status_code == 401
            assert resp.get_json()["error"] == 2001
        assert env.ub.session.query(KOSyncProgress).count() == 0


class TestProgressRoundTrip:
    def test_put_then_get_unmatched_document(self, env):
        put = _put(env, _reader(env))
        assert put.status_code == 200
        body = put.get_json()
        assert body["document"] == DOC
        assert isinstance(body["timestamp"], int)
        assert "calibre_book_id" not in body

        got = _get(env, _reader(env)).get_json()
        assert got["document"] == DOC
        assert got["progress"] == "/body/DocFragment[3]/p[7]"
        assert got["percentage"] == pytest.approx(0.4567)
        assert got["device"] == "Kobo Libra"
        assert got["device_id"] == "dev-1"
        assert isinstance(got["timestamp"], int)

    def test_get_timestamp_matches_put_on_non_utc_host(self, env):
        import os
        import time
        old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        try:
            put = _put(env, _reader(env)).get_json()
            got = _get(env, _reader(env)).get_json()
        finally:
            if old_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_tz
            time.tzset()
        assert got["timestamp"] == put["timestamp"]

    def test_second_put_updates_existing_record(self, env):
        _put(env, _reader(env), percentage=0.1)
        _put(env, _reader(env), percentage=0.2, progress="later", device="Phone")
        env.ub.session.expire_all()
        assert env.ub.session.query(KOSyncProgress).count() == 1
        got = _get(env, _reader(env)).get_json()
        assert got["percentage"] == pytest.approx(0.2)
        assert (got["progress"], got["device"]) == ("later", "Phone")

    def test_unknown_document_returns_empty_object(self, env):
        resp = _get(env, _reader(env), "ffffffffffffffffffffffffffffffff")
        assert resp.status_code == 200
        assert resp.get_json() == {}

    def test_progress_is_per_user(self, env):
        other = env.add_user("other", password="pw")
        _put(env, _reader(env))
        assert _get(env, _basic(other.name, "pw")).get_json() == {}

    @pytest.mark.parametrize("body,code", [
        ({"document": None}, 2004),
        ({"document": "bad:doc"}, 2004),
        ({"progress": None}, 2003),
        ({"device": None}, 2003),
        ({"percentage": None}, 2003),
        ({"percentage": "abc"}, 2003),
        ({"percentage": 150}, 2003),
        ({"percentage": -0.5}, 2003),
        ({"device_id": "x" * 101}, 2003),
    ])
    def test_invalid_payloads_are_rejected(self, env, body, code):
        resp = _put(env, _reader(env), **body)
        assert resp.status_code == 400
        assert resp.get_json()["error"] == code

    def test_whole_number_percentage_is_treated_as_percent(self, env):
        _put(env, _reader(env), percentage=45)
        assert _get(env, _reader(env)).get_json()["percentage"] == pytest.approx(0.45)


class TestBookMatching:
    def test_matched_document_is_enriched_and_updates_read_status(self, env):
        book_id = env.add_book("Matched Book")
        env.add_checksum(book_id, DOC)
        resp = _put(env, _reader(env), percentage=0.5)
        body = resp.get_json()
        assert body["calibre_book_id"] == book_id
        assert body["calibre_book_title"] == "Matched Book"
        assert body["calibre_book_format"] == "EPUB"

        ub = env.ub
        ub.session.expire_all()
        read = ub.session.query(ub.ReadBook).filter_by(user_id=env.reader.id, book_id=book_id).one()
        assert read.read_status == ub.ReadBook.STATUS_IN_PROGRESS
        assert read.kobo_reading_state.current_bookmark.progress_percent == pytest.approx(50.0)

        got = _get(env, _reader(env)).get_json()
        assert got["calibre_book_id"] == book_id
        assert got["percentage"] == pytest.approx(0.5)

    def test_finishing_marks_book_read(self, env):
        book_id = env.add_book("Done Book")
        env.add_checksum(book_id, DOC)
        _put(env, _reader(env), percentage=0.2)
        _put(env, _reader(env), percentage=1.0)
        ub = env.ub
        ub.session.expire_all()
        read = ub.session.query(ub.ReadBook).filter_by(user_id=env.reader.id, book_id=book_id).one()
        assert read.read_status == ub.ReadBook.STATUS_FINISHED
        assert read.times_started_reading == 1

    def test_different_checksums_of_same_book_share_progress(self, env):
        """Progress is stored against the Calibre book id, so another format/checksum of the
        same book sees it."""
        book_id = env.add_book("Two Formats")
        other_doc = "fedcba9876543210fedcba9876543210"
        env.add_checksum(book_id, DOC, fmt="EPUB")
        env.add_checksum(book_id, other_doc, fmt="KEPUB")
        _put(env, _reader(env), percentage=0.3, progress="epub-pos")
        got = _get(env, _reader(env), other_doc).get_json()
        assert got["progress"] == "epub-pos"
        assert got["percentage"] == pytest.approx(0.3)

    def test_get_book_by_checksum(self, env):
        book_id = env.add_book("Lookup")
        env.add_checksum(book_id, DOC, version="koreader")
        with env.app.app_context():
            assert get_book_by_checksum(DOC) == (book_id, "EPUB", "Lookup", "Test Author/Lookup", "koreader")
            assert get_book_by_checksum(DOC, version="other") == (None,) * 5
            assert get_book_by_checksum("nope") == (None,) * 5
