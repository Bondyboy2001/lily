"""A regular user and a signed-out visitor must not reach admin-only routes."""

import pytest

from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit

ADMIN_GETS = ["/admin/user/new", "/admin/usertable", "/logs"]
ADMIN_POSTS = ["/admin/user/new", "/shutdown"]
# Settings pages that were deleted outright: not hidden, not redirected, gone.
REMOVED = ["/admin/view", "/admin/config", "/admin/dbconfig", "/admin/viewconfig", "/admin/scheduledtasks",
           "/admin/db_backups", "/admin/ingest_failures", "/admin/book-recovery", "/admin/metadata/suggestions",
           "/admin/hardcover/review-matches", "/cwa-stats-show", "/stats", "/tasks", "/account/security",
           "/reading", "/ajax/pathchooser/", "/ajax/deleteuser", "/metadata_backup",
           "/admin/debug", "/ajax/canceltask", "/cwa-check-monitoring"]


@pytest.fixture
def clients(tmp_path):
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(env.app)
        env.add_user("plain", password="pw")
        user = env.app.test_client()
        user.post("/login", data={"username": "plain", "password": "pw"})
        admin = env.app.test_client()
        admin.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
        yield env.app.test_client(), user, admin


def _blocked(resp, path):
    """Refused outright, or bounced somewhere other than the page asked for."""
    if resp.status_code in (401, 403):
        return True
    return resp.status_code == 302 and path not in resp.headers["Location"]


@pytest.mark.parametrize("path", ADMIN_GETS)
def test_admin_pages_are_closed_to_users_and_visitors(clients, path):
    visitor, user, admin = clients
    assert _blocked(visitor.get(path), path), "visitor reached " + path
    assert _blocked(user.get(path), path), "regular user reached " + path
    assert admin.get(path).status_code == 200


@pytest.mark.parametrize("path", ADMIN_POSTS)
def test_admin_actions_are_closed_to_users_and_visitors(clients, path):
    visitor, user, _ = clients
    for c in (visitor, user):
        resp = c.post(path, data={"names": "x"})
        assert _blocked(resp, path), "%s reached %s" % (path, resp.status_code)


@pytest.mark.parametrize("path", REMOVED)
def test_removed_settings_pages_have_no_route(clients, path):
    """Nothing serves these paths any more. Two-segment ones such as /admin/view fall through
    to the generic /<list>/<sort> book list, which is not the old page."""
    from flask import current_app
    from werkzeug.exceptions import NotFound
    from werkzeug.routing import RequestRedirect
    visitor, _, _ = clients
    with visitor.application.app_context():
        adapter = current_app.url_map.bind("localhost")
        try:
            endpoint, _args = adapter.match(path, method="GET")
        except NotFound:
            return
        except RequestRedirect as redirect:
            endpoint, _args = adapter.match(redirect.new_url.split("localhost", 1)[1], method="GET")
        assert endpoint == "web.books_list", (path, endpoint)
