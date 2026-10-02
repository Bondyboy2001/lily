# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline reading: the service worker route, the Offline page, and what the pages hand offline.js."""

import json
import re
from pathlib import Path

import pytest

from .lily_env import lily_env, ADMIN_PASSWORD

REPO_ROOT = Path(__file__).resolve().parents[2]
SW = (REPO_ROOT / "cps/templates/sw.js").read_text(encoding="utf-8")
JS = (REPO_ROOT / "cps/static/js/offline.js").read_text(encoding="utf-8")


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "")
        from cps.editbooks import editbook
        from cps.duplicates import duplicates
        from cps.cwa_functions import library_refresh, cwa_settings
        for bp in (editbook, duplicates, library_refresh, cwa_settings):
            if bp.name not in e.app.blueprints:
                e.app.register_blueprint(bp)
        yield e


def _login(env):
    client = env.app.test_client()
    client.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
    return client


@pytest.mark.unit
class TestRoutes:
    def test_worker_is_served_at_the_root_for_the_whole_app(self, env):
        resp = env.app.test_client().get("/sw.js")  # no login: the browser fetches it itself
        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith("application/javascript")
        assert resp.headers["Service-Worker-Allowed"] == "/"
        assert resp.headers["Cache-Control"] == "no-cache"
        body = resp.get_data(as_text=True)
        assert "{% raw %}" not in body and "{{" not in body
        version = re.search(r'const VERSION = "([0-9a-f]{12})";', body)
        assert version and 'const SCOPE = "/";' in body and 'const SHELL_URL = "/offline";' in body
        extras = json.loads(re.search(r"const EXTRAS = (\{.*?\});\n", body).group(1))
        # Files the readers load by themselves (cache-busted with ?q= in production).
        assert any("/static/locale/en-US/" in u for u in extras["pdf"])
        assert any("/static/standard_fonts/" in u for u in extras["pdf"])
        assert any(u.split("?")[0].endswith(".cache.js") for u in extras["djvu"])
        assert any("/static/fonts/literata/" in u for u in extras["all"])
        assert not any("/cmaps/" in u for urls in extras.values() for u in urls)

    def test_offline_page_needs_no_login_and_holds_no_server_data(self, env):
        html = env.app.test_client().get("/offline").get_data(as_text=True)
        assert "Saved on This Device" in html
        assert '<meta name="lily-sw" content="/sw.js" data-scope="/">' in html
        assert "js/offline.js" in html and 'class="offline-books"' in html
        assert env.admin().name not in html

    def test_csrf_tokens_last_the_session(self, env):
        # A reader page opened from the cache days later still saves positions.
        assert "app.config['WTF_CSRF_TIME_LIMIT'] = None" in (REPO_ROOT / "cps/__init__.py").read_text()

    def test_every_page_registers_the_worker(self, env):
        html = _login(env).get("/").get_data(as_text=True)
        assert '<meta name="lily-sw" content="/sw.js" data-scope="/">' in html
        assert re.search(r'<script src="/static/js/offline\.js(\?q=\w+)?" defer>', html)


@pytest.mark.unit
class TestBookSpecs:
    def test_the_book_page_has_no_keep_offline_button(self, env):
        # Books are kept offline from Continue Reading only
        book = env.add_book("Offline Epub", fmt="EPUB")
        html = _login(env).get(f"/book/{book}").get_data(as_text=True)
        assert 'keep-offline-btn' not in html

    def test_library_hands_over_continue_reading(self, env):
        from cps import ub
        reading = env.add_book("In Progress", fmt="EPUB")
        env.add_book("Not Started", fmt="EPUB")
        client = _login(env)
        client.post(f"/ajax/progress/{reading}?format=epub", json={"cfi": "epubcfi(/6/4)", "percent": 0.3})
        ub.session.expire_all()
        html = client.get("/").get_data(as_text=True)
        books = json.loads(re.search(r'<script type="application/json" id="lily-offline-auto">(.*?)</script>',
                                     html, flags=re.S).group(1))
        assert [(b["id"], b["format"]) for b in books] == [(reading, "epub")]
        # Other lists (here: a filtered one) don't speak for Continue Reading.
        assert 'id="lily-offline-auto"' not in client.get("/", query_string={"format": "epub"}).get_data(as_text=True)


@pytest.mark.unit
class TestWorkerRules:
    def test_links_resolve_against_an_absolute_base(self):
        # new URL(x, "/read/7/pdf") throws, which once left kept books without their files.
        assert "referencedUrls(html, abs(url), false)" in SW
        assert "referencedUrls(html, abs(SHELL_URL), false)" in SW

    def test_only_reads_are_intercepted(self):
        handler = SW[SW.index('self.addEventListener("fetch"'):]
        assert 'if (request.method !== "GET") { return; }' in handler
        assert 'path === "sw.js"' in handler

    def test_pages_fall_back_to_saved_copies_then_the_offline_page(self):
        nav = SW[SW.index("async function navigate"):SW.index("async function staticFile")]
        assert "NAV_TIMEOUT_MS" in nav and "PAGES_CACHE" in nav and "SHELL_URL" in nav

    def test_pdf_byte_ranges_come_from_the_saved_file(self):
        assert '"Content-Range": "bytes " + start + "-" + end + "/" + size' in SW and "status: 206" in SW

    def test_untaking_a_book_in_progress_sticks(self):
        assert "excluded: true" in SW and "dropBook(msg.id, !!(entry && entry.auto))" in SW

    def test_nothing_switches_on_without_a_secure_context(self):
        assert '!("serviceWorker" in navigator) || !window.isSecureContext' in JS
