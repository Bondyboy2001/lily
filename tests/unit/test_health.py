# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""/health reports the library's real state and never creates metadata.db."""

import sqlite3

import pytest


def _call_health(monkeypatch, library_dir):
    import cps
    from cps import web
    monkeypatch.setattr(web, "cwa_get_library_location", lambda: str(library_dir))
    with cps.app.test_request_context("/health"):
        return web.health_check()


@pytest.mark.unit
def test_health_ok_with_readable_library(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / "metadata.db")
    conn.execute("CREATE TABLE books (id INTEGER)")
    conn.close()
    response, status = _call_health(monkeypatch, tmp_path)
    assert status == 200
    assert response.get_json()["status"] == "ok"
    assert response.get_json()["version"].startswith("Lily/")


@pytest.mark.unit
def test_health_degraded_when_library_missing_and_does_not_create_it(tmp_path, monkeypatch):
    response, status = _call_health(monkeypatch, tmp_path)
    assert status == 503
    assert response.get_json()["status"] == "degraded"
    assert not (tmp_path / "metadata.db").exists()


@pytest.mark.unit
def test_health_degraded_when_file_is_not_a_database(tmp_path, monkeypatch):
    (tmp_path / "metadata.db").write_bytes(b"not a database" * 100)
    _, status = _call_health(monkeypatch, tmp_path)
    assert status == 503
