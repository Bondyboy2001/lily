"""A regular user and a signed-out visitor must not reach admin-only routes."""

import pytest

from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit

ADMIN_GETS = ["/admin/ingest_failures", "/admin/db_backups", "/admin/db_backups/download/20260101_030000",
              "/admin/view", "/admin/user/new"]
ADMIN_POSTS = ["/admin/ingest_failures/delete", "/admin/ingest_failures/retry", "/admin/db_backups/restore",
               "/admin/db_backups/settings", "/account/security/unlock/1"]


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
    # 404 is fine for the snapshot download: the admin got past the gate, the snapshot just doesn't exist here
    assert admin.get(path).status_code in (200, 404)


@pytest.mark.parametrize("path", ADMIN_POSTS)
def test_admin_actions_are_closed_to_users_and_visitors(clients, path):
    visitor, user, _ = clients
    for c in (visitor, user):
        resp = c.post(path, data={"names": "x", "snapshot": "20260101_030000"})
        assert _blocked(resp, path), "%s reached %s" % (path, resp.status_code)
