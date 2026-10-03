# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Browser uploads: file validation order and the /upload/status route lily.js polls."""

import io

import pytest

from .lily_env import lily_env, ADMIN_PASSWORD


@pytest.fixture
def env(tmp_path, temp_cwa_db):
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

    def test_a_finished_import_names_its_book(self, env):
        import automation_jobs as aj
        client = _login(env)
        uid = env.admin().id
        name = f"new_{uid}_20261002_082501_450561_Salt.epub"
        aj.finish_job(aj.create_job("ingest", filename=name), "succeeded", book_id=7)
        assert client.get("/upload/status", query_string={"file": name}).get_json() == {
            "files": [{"file": name, "state": "succeeded", "error": "", "book_id": 7}]}

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


@pytest.mark.unit
class TestUploadSizeLimit:
    def test_limit_fits_inside_the_tornado_buffer(self):
        import re
        from pathlib import Path
        from cps import app, constants
        server = (Path(__file__).resolve().parents[2] / "cps" / "server.py").read_text(encoding="utf-8")
        buffer = int(re.search(r"max_buffer_size=(\d+)", server).group(1))
        assert app.config["MAX_CONTENT_LENGTH"] == constants.MAX_UPLOAD_BYTES < buffer

    def test_oversize_upload_gets_an_early_413_with_a_plain_message(self, env, monkeypatch):
        from cps import constants, editbooks_upload
        monkeypatch.setattr(constants, "MAX_UPLOAD_BYTES", 1000)
        saved = []
        monkeypatch.setattr(editbooks_upload, "_save_to_ingest_atomic_rename",
                            lambda *a, **k: saved.append(a) or ("", ""))
        client = _login(env)
        resp = client.post("/upload", data={"btn-upload": (io.BytesIO(b"x" * 5000), "big.epub")},
                           content_type="multipart/form-data")
        assert resp.status_code == 413
        assert resp.mimetype == "text/plain"
        assert "upload limit" in resp.get_data(as_text=True)
        assert saved == []

    def test_flask_413_on_the_upload_route_uses_the_same_message(self, env):
        # With CSRF on, the form is parsed (and Flask's 413 raised) before the view runs
        from werkzeug.exceptions import RequestEntityTooLarge
        from cps.error_handler import error_http
        with env.app.test_request_context("/upload", method="POST"):
            resp = error_http(RequestEntityTooLarge())
        assert resp.status_code == 413
        assert resp.mimetype == "text/plain"
        assert "200 MB upload limit" in resp.get_data(as_text=True)
