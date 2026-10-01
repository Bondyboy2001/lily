# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Browser uploads: file validation order and the /upload/status route lily.js polls."""

import io

import pytest

from .lily_env import lily_env, ADMIN_PASSWORD


@pytest.fixture
def env(tmp_path, temp_cwa_db, monkeypatch):
    monkeypatch.setenv("BOOK_RECOVERY_DIR", str(tmp_path / "recovery"))
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        yield e


def _login(env):
    client = env.app.test_client()
    client.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
    return client


@pytest.mark.unit
class TestUploadStatus:
    def test_unknown_file_is_queued(self, env):
        client = _login(env)
        uid = env.admin().id
        name = f"new_{uid}_20261001_120000_123456_book.epub"
        resp = client.get("/upload/status", query_string={"file": name})
        assert resp.status_code == 200
        assert resp.get_json() == {"files": [{"file": name, "state": "queued", "error": ""}]}

    def test_names_outside_the_upload_pattern_are_refused(self, env):
        client = _login(env)
        uid = env.admin().id
        for name in ("../app.db", "book.epub", f"new_{uid + 1}_20261001_120000_123456_book.epub"):
            assert client.get("/upload/status", query_string={"file": name}).status_code == 400, name


@pytest.mark.unit
class TestUploadValidation:
    def test_damaged_file_of_an_allowed_type_says_so(self, env):
        from flask import get_flashed_messages
        from werkzeug.datastructures import FileStorage
        from cps import config
        from cps.editbooks_upload import _validate_uploaded_file
        config.config_check_extensions = True
        upload = FileStorage(stream=io.BytesIO(b"not really an epub"), filename="broken.epub")
        with env.app.test_request_context():
            assert _validate_uploaded_file(upload) is False
            message = get_flashed_messages()[0]
        assert "couldn't be read as EPUB" in message and "isn't allowed" not in message

    def test_disallowed_extension_is_named(self, env):
        from flask import get_flashed_messages
        from werkzeug.datastructures import FileStorage
        from cps.editbooks_upload import _validate_uploaded_file
        upload = FileStorage(stream=io.BytesIO(b"x"), filename="notes.xyz")
        with env.app.test_request_context():
            assert _validate_uploaded_file(upload) is False
            assert "'xyz'" in get_flashed_messages()[0]
