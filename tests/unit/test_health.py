# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""/health reports the library's and the s6 services' real state and never creates metadata.db."""

import os
import re
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from gevent.pywsgi import WSGIHandler


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


def _library(tmp_path):
    conn = sqlite3.connect(tmp_path / "metadata.db")
    conn.execute("CREATE TABLE books (id INTEGER)")
    conn.close()
    return tmp_path


def _fake_s6_rc(tmp_path, monkeypatch, active, returncode=0):
    """Put an s6-rc on PATH that prints the `active` services for `s6-rc -a list`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "s6-rc"
    lines = "".join(f"echo {name}\n" for name in active)
    script.write_text(f'#!/bin/sh\n[ "$1 $2" = "-a list" ] || exit 9\n{lines}exit {returncode}\n')
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")


@pytest.mark.unit
def test_services_unknown_without_s6_and_health_stays_ok(tmp_path, monkeypatch):
    from cps import web
    monkeypatch.setattr(web, "_find_s6_rc", lambda: None)
    response, status = _call_health(monkeypatch, _library(tmp_path))
    assert status == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert set(body["services"]) == set(web._CRITICAL_LONGRUNS)
    assert set(body["services"].values()) == {"unknown"}


@pytest.mark.unit
def test_health_ok_when_all_services_up(tmp_path, monkeypatch):
    from cps import web
    _fake_s6_rc(tmp_path, monkeypatch, ["svc-calibre-web-automated", *web._CRITICAL_LONGRUNS])
    response, status = _call_health(monkeypatch, _library(tmp_path))
    assert status == 200
    assert set(response.get_json()["services"].values()) == {"up"}


@pytest.mark.unit
def test_health_degraded_when_a_service_is_down(tmp_path, monkeypatch):
    _fake_s6_rc(tmp_path, monkeypatch, ["svc-calibre-web-automated", "cwa-ingest-service"])
    response, status = _call_health(monkeypatch, _library(tmp_path))
    assert status == 503
    body = response.get_json()
    assert body["status"] == "degraded"
    assert body["database"] == "ok"
    assert body["services"] == {"cwa-ingest-service": "up", "metadata-change-detector": "down"}
    assert body["version"].startswith("Lily/")


@pytest.mark.unit
def test_failing_s6_rc_reports_unknown_not_down(tmp_path, monkeypatch):
    _fake_s6_rc(tmp_path, monkeypatch, [], returncode=1)
    response, status = _call_health(monkeypatch, _library(tmp_path))
    assert status == 200
    assert set(response.get_json()["services"].values()) == {"unknown"}


@pytest.mark.unit
def test_s6_rc_timeout_reports_unknown(monkeypatch):
    from cps import web

    def _hang(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="s6-rc", timeout=2)

    monkeypatch.setattr(web, "_find_s6_rc", lambda: "/command/s6-rc")
    monkeypatch.setattr(web.subprocess, "run", _hang)
    assert set(web._check_s6_service_status().values()) == {"unknown"}


@pytest.mark.unit
def test_dockerfile_healthcheck_bounds_each_curl():
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text()
    match = re.search(r"^HEALTHCHECK[^\n]*\n  CMD ([^\n]+)", dockerfile, re.MULTILINE)
    assert match, "Dockerfile must declare a HEALTHCHECK"
    cmd = match.group(1)
    assert "/health" in cmd
    curls = re.findall(r"curl [^|]+", cmd)
    assert curls
    for curl in curls:
        assert "--max-time" in curl and "--connect-timeout" in curl, curl


@pytest.mark.unit
def test_gevent_handler_closes_every_connection():
    from cps.gevent_wsgi import MyWSGIHandler
    handler = MyWSGIHandler.__new__(MyWSGIHandler)
    handler.close_connection = False
    with patch.object(WSGIHandler, "read_request", return_value=True):
        assert handler.read_request("GET / HTTP/1.1\r\n") is True
    assert handler.close_connection is True
