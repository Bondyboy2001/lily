# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""POST /upload hands files to the ingest folder (editbooks_upload.upload): new books as
plain files, extra formats with a .cwa.json manifest naming the book. No converter runs
here (subprocess is stubbed to fail if anything tries), and the worker queue is a list."""

import io
import json
import subprocess
from pathlib import Path

import pytest

from tests.unit.library_fixture import SAMPLE_BOOKS, library_env

pytestmark = pytest.mark.unit


@pytest.fixture
def upload_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path / "cwa"))
    ingest = tmp_path / "ingest"
    with library_env(tmp_path, config_uploading=1, config_upload_formats="epub,txt",
                     config_check_extensions=0) as lib:
        import cps.editbooks_upload as upload_module
        monkeypatch.setattr(upload_module, "get_ingest_dir", lambda: str(ingest))
        queued = []
        monkeypatch.setattr(upload_module.WorkerThread, "add",
                            staticmethod(lambda user, task, hidden=False: queued.append((user, task))))
        # chown to abc (1000:1000) is expected to fail outside the container; make it a no-op.
        monkeypatch.setattr(upload_module.os, "chown", lambda *a, **k: None)
        client = lib.admin_client()

        # The route only hands files to ingest: converters (calibre, kepubify) must not run.
        def _no_subprocess(*args, **kwargs):
            raise AssertionError(f"upload started a process: {args!r}")
        monkeypatch.setattr(subprocess, "Popen", _no_subprocess)
        monkeypatch.setattr(subprocess, "run", _no_subprocess)
        yield lib, client, ingest, queued


def _file(name="test_minimal_valid.epub", as_name=None):
    return (io.BytesIO((SAMPLE_BOOKS / name).read_bytes()), as_name or name)


def _ingest_files(ingest: Path):
    return sorted(p.name for p in ingest.iterdir()) if ingest.exists() else []


def test_new_book_upload_lands_in_ingest_folder(upload_env):
    lib, client, ingest, queued = upload_env
    resp = client.post("/upload", data={"btn-upload": _file()}, content_type="multipart/form-data")

    assert resp.status_code == 200
    assert json.loads(resp.data)["location"] == "/"  # back to the library; the book appears once ingested
    files = _ingest_files(ingest)
    assert len(files) == 1
    name = files[0]
    assert name.startswith(f"new_{lib.admin().id}_") and name.endswith("_test_minimal_valid.epub")
    assert (ingest / name).read_bytes() == (SAMPLE_BOOKS / "test_minimal_valid.epub").read_bytes()
    assert len(queued) == 1


def test_format_upload_writes_manifest_for_the_book(upload_env):
    lib, client, ingest, queued = upload_env
    book_id = lib.add_book_with_files("Has Epub", "Jane Doe", ["test_minimal_valid.epub"])

    resp = client.post("/upload", data={"btn-upload-format": _file("metamorphosis.txt"), "book_id": str(book_id)},
                       content_type="multipart/form-data")

    assert resp.status_code == 200
    assert json.loads(resp.data)["location"].endswith(f"/admin/book/{book_id}")
    files = _ingest_files(ingest)
    book_file = [f for f in files if f.endswith(".txt")]
    assert len(book_file) == 1 and book_file[0].startswith(f"format_{book_id}_")
    manifest = json.loads((ingest / (book_file[0] + ".cwa.json")).read_text())
    assert manifest == {"action": "add_format", "book_id": book_id, "original_filename": "metamorphosis.txt"}
    assert not [f for f in files if f.endswith(".uploading")]
    assert len(queued) == 1


def test_disallowed_extension_is_rejected(upload_env):
    lib, client, ingest, queued = upload_env
    resp = client.post("/upload", data={"btn-upload": _file("alice_in_wonderland.mobi")},
                       content_type="multipart/form-data")

    assert resp.status_code == 200
    assert _ingest_files(ingest) == []
    assert queued == []


def test_format_upload_for_missing_book_is_rejected(upload_env):
    lib, client, ingest, queued = upload_env
    resp = client.post("/upload", data={"btn-upload-format": _file(), "book_id": "9999"},
                       content_type="multipart/form-data")

    assert resp.status_code == 200
    assert json.loads(resp.data)["location"] == "/"
    assert [f for f in _ingest_files(ingest) if not f.startswith(".")] == []
    assert queued == []


def test_upload_needs_the_upload_role(upload_env):
    lib, _client, ingest, queued = upload_env
    from cps import constants
    lib.add_user("reader", password="pw", role=constants.ROLE_DOWNLOAD)
    client = lib.app.test_client()
    client.post("/login", data={"username": "reader", "password": "pw"})

    resp = client.post("/upload", data={"btn-upload": _file()}, content_type="multipart/form-data")

    assert resp.status_code == 403
    assert _ingest_files(ingest) == []
