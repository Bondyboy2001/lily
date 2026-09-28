# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""refresh_library() bounds the ingest subprocess and reports a timeout."""

import subprocess

import pytest
from flask import Flask


@pytest.mark.unit
def test_refresh_library_handles_timeout(monkeypatch):
    from cps.cwa_functions import ingest

    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(ingest.subprocess, "run", fake_run)
    monkeypatch.setattr(ingest, "get_ingest_dir", lambda: "/tmp/ingest")
    monkeypatch.setenv("CWA_LIBRARY_REFRESH_TIMEOUT", "12")
    from flask_babel import Babel
    app = Flask(__name__)
    Babel(app)
    ingest.refresh_library(app)
    assert seen["timeout"] == 12
    with app.test_request_context():
        assert "took too long" in str(app.config["library_refresh_messages"][-1])


@pytest.mark.unit
@pytest.mark.parametrize("value,expected", [("30", 30), ("0", 7200), ("x", 7200)])
def test_refresh_timeout_env(monkeypatch, value, expected):
    from cps.cwa_functions import ingest
    monkeypatch.setenv("CWA_LIBRARY_REFRESH_TIMEOUT", value)
    assert ingest._library_refresh_timeout() == expected
