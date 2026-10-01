import re

import pytest

from cps import constants

from .lily_env import lily_env, ADMIN_PASSWORD


def _rail_ids(html):
    return re.findall(r'data-section="(\w+)"', html)


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        yield e


def _login(env, name, password):
    client = env.app.test_client()
    resp = client.post("/login", data={"username": name, "password": password})
    assert resp.status_code in (200, 302), resp.data[:300]
    return client


@pytest.mark.unit
class TestSettingsRail:
    def test_admin_rail_lists_only_remaining_sections(self, env):
        html = _login(env, env.admin().name, ADMIN_PASSWORD).get("/me").get_data(as_text=True)
        assert _rail_ids(html) == ["profile", "import", "users", "duplicates", "logs"]
        for gone in ("security", "configuration", "library", "backups", "recovery", "statistics"):
            assert f'data-section="{gone}"' not in html, gone
        assert "id='logout'" in html

    def test_reader_rail_has_profile_and_logout_only(self, env):
        env.add_user("reader", password="pw")
        html = _login(env, "reader", "pw").get("/me").get_data(as_text=True)
        assert _rail_ids(html) == ["profile"]
        assert "id='logout'" in html
        for gone in ("security", "import", "users", "duplicates", "logs",
                     "configuration", "library", "backups", "recovery", "statistics"):
            assert f'data-section="{gone}"' not in html, gone

    def test_guest_session_has_no_security_link(self, env):
        html = env.app.test_client().get("/login").get_data(as_text=True)
        assert "/account/security" not in html


@pytest.mark.unit
class TestPageReachability:
    def test_profile_has_no_links_group(self, env):
        html = _login(env, env.admin().name, ADMIN_PASSWORD).get("/me").get_data(as_text=True)
        assert 'href="/account/security"' not in html
        assert 'href="/reading"' not in html

    def test_import_settings_has_no_tools_group(self, env):
        html = _login(env, env.admin().name, ADMIN_PASSWORD).get("/cwa-settings").get_data(as_text=True)
        for href in ("/admin/config", "/admin/dbconfig", "/admin/db_backups",
                     "/admin/book-recovery", "/cwa-stats-show",
                     "/admin/ingest_failures", "/admin/metadata/suggestions",
                     "/tasks"):
            assert f'href="{href}"' not in html, href

    def test_import_settings_has_no_automation_summary(self, env):
        html = _login(env, env.admin().name, ADMIN_PASSWORD).get("/cwa-settings").get_data(as_text=True)
        assert "Latest database snapshot" not in html and "Running jobs" not in html

    def test_import_settings_denied_to_reader(self, env):
        env.add_user("reader", password="pw")
        resp = _login(env, "reader", "pw").get("/cwa-settings")
        assert resp.status_code in (302, 403)


@pytest.mark.unit
class TestSettingsUtilityLinks:
    def test_admin_sees_duplicates_and_logs_in_rail(self, env):
        html = _login(env, env.admin().name, ADMIN_PASSWORD).get("/me").get_data(as_text=True)
        assert 'data-section="duplicates"' in html and 'href="/duplicates"' in html
        assert 'data-section="logs"' in html and 'href="/logs"' in html
        assert 'id="duplicate-count-badge"' in html

    def test_duplicates_page_lights_its_rail_item(self, env):
        html = _login(env, env.admin().name, ADMIN_PASSWORD).get("/duplicates").get_data(as_text=True)
        assert 'data-section="duplicates" class="active"' in html
        assert 'href="/duplicates" class="lp-rail-item" aria-current="page"' in html

    def test_logs_page_lights_its_rail_item(self, env):
        html = _login(env, env.admin().name, ADMIN_PASSWORD).get("/logs").get_data(as_text=True)
        assert 'data-section="logs" class="active"' in html
        assert 'href="/logs" class="lp-rail-item" aria-current="page"' in html

    def test_reader_sees_neither_utility_link(self, env):
        env.add_user("reader", password="pw")
        html = _login(env, "reader", "pw").get("/me").get_data(as_text=True)
        for href in ("/duplicates", "/logs"):
            assert f'href="{href}"' not in html, href

    def test_editor_sees_duplicates_but_not_logs(self, env):
        env.add_user("editor", password="pw",
                     role=constants.ROLE_USER | constants.ROLE_EDIT)
        html = _login(env, "editor", "pw").get("/me").get_data(as_text=True)
        assert 'href="/duplicates"' in html
        assert 'href="/logs"' not in html

    def test_utility_links_absent_from_main_sidebar(self, env):
        html = _login(env, env.admin().name, ADMIN_PASSWORD).get("/").get_data(as_text=True)
        assert 'id="nav_duplicates"' not in html and 'id="nav_logs"' not in html


@pytest.mark.unit
class TestMyReadingSidebar:
    def test_signed_in_user_has_no_my_reading_sidebar_link(self, env):
        html = _login(env, env.admin().name, ADMIN_PASSWORD).get("/").get_data(as_text=True)
        assert 'id="nav_reading"' not in html and 'href="/reading"' not in html
        assert 'id="nav_createshelf"' in html

    def test_my_reading_page_is_gone(self, env):
        resp = _login(env, env.admin().name, ADMIN_PASSWORD).get("/reading")
        assert resp.status_code == 404


@pytest.mark.unit
class TestGridQuickActions:
    def _home(self, env):
        c = _login(env, env.admin().name, ADMIN_PASSWORD)
        return c.get("/").get_data(as_text=True)

    def test_readable_book_gets_reader_url(self, env):
        book_id = env.add_book("Readable", fmt="EPUB")
        html = self._home(env)
        assert f'data-reader-url="/read/{book_id}/epub"' in html

    def test_unsupported_format_offers_download_not_reader(self, env):
        book_id = env.add_book("Text Only", fmt="TXT")
        html = self._home(env)
        assert "data-reader-url" not in html
        assert f'href="/download/{book_id}/txt' in html

    def test_kepub_and_audio_are_readable(self, env):
        kepub_id = env.add_book("Kepub Book", fmt="KEPUB")
        audio_id = env.add_book("Audio Book", fmt="M4B")
        html = self._home(env)
        assert f'data-reader-url="/read/{kepub_id}/kepub"' in html
        assert f'data-reader-url="/read/{audio_id}/m4b"' in html
