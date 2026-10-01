# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The stats pages are built for one reader: no demo data, no user picker, no Top Users or
Active users, and no Kobo or e-mail categories."""

import pytest

from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


@pytest.fixture
def stats_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path / "cwa"))
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(env.app)
        from cwa_db import CWA_DB
        db = CWA_DB()
        db.log_activity(1, "harry", "LOGIN")
        db.log_activity(1, "harry", "DOWNLOAD", item_id=1, item_title="Book", extra_data="EPUB")
        db.log_activity(1, "harry", "OPDS_ACCESS", extra_data='{"endpoint": "/opds"}')
        client = env.app.test_client()
        client.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
        yield client


@pytest.mark.parametrize("tab", ["activity", "library", "api"])
def test_stats_tabs_render_without_multi_user_or_demo_parts(stats_client, tab):
    resp = stats_client.get(f"/cwa-stats-show?tab={tab}")
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    for gone in ("Show demo data", "DemoMode", "user-filter", "Top Users", "Active users",
                 "All users", "'EMAIL'", "Kobo"):
        assert gone not in page, gone
    assert "Most Active Days" in page
    assert "Logins" in page


def test_activity_csv_lists_most_active_days(stats_client):
    csv_text = stats_client.get("/cwa-stats-export-csv/activity?days=30").get_data(as_text=True)
    assert "=== MOST ACTIVE DAYS ===" in csv_text
    assert "TOP USERS" not in csv_text


def test_api_csv_has_only_real_categories(stats_client):
    csv_text = stats_client.get("/cwa-stats-export-csv/api?days=30").get_data(as_text=True)
    assert "OPDS Feed,1" in csv_text
    assert "Web UI," in csv_text  # the seeded events plus the test client's own logins
    assert "Kobo" not in csv_text and "Email" not in csv_text
