"""'unsafe-eval' is granted only to the pages that need it."""

import pytest

from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


@pytest.fixture
def client(tmp_path):
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(env.app)
        from cps import web
        env.app.after_request(web.add_security_headers.__wrapped__ if hasattr(web.add_security_headers, "__wrapped__")
                              else web.add_security_headers)
        c = env.app.test_client()
        c.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
        yield env, c


def test_default_pages_have_no_unsafe_eval(client):
    env, c = client
    for path in ("/", "/login", "/me", "/admin/usertable", "/logs"):
        resp = c.get(path)
        assert resp.status_code in (200, 302), path  # /login redirects a signed-in user
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "default-src" in csp and "'unsafe-eval'" not in csp, path
        assert "'unsafe-inline'" in csp


def test_edit_and_reader_pages_keep_unsafe_eval(client):
    env, c = client
    book = env.add_book("Edit Me")
    for path in (f"/admin/book/{book}", f"/read/{book}/epub"):
        resp = c.get(path)
        assert resp.status_code == 200, path
        assert "'unsafe-eval'" in resp.headers["Content-Security-Policy"], path


def test_other_headers_present(client):
    _, c = client
    h = c.get("/").headers
    assert h["Referrer-Policy"] == "same-origin"
    assert "X-XSS-Protection" not in h
    assert h["X-Content-Type-Options"] == "nosniff"
