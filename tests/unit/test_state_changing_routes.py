"""Routes that change state answer only POST (with the CSRF token), so a link or image on
another site can't trigger them."""

import re

import pytest
from werkzeug.exceptions import MethodNotAllowed

from tests.unit.lily_env import REPO, lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit

POST_ONLY = ["/logout", "/gdrive/watch/subscribe", "/gdrive/watch/revoke"]


@pytest.fixture
def admin(tmp_path):
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(env.app)
        c = env.app.test_client()
        c.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
        yield env, c


@pytest.mark.parametrize("path", POST_ONLY)
def test_only_post_reaches_the_handler(admin, path):
    env, c = admin
    rule = next(r for r in env.app.url_map.iter_rules() if r.rule == path)
    assert rule.methods - {"OPTIONS"} == {"POST"}
    try:
        endpoint, _ = env.app.url_map.bind("localhost").match(path, "GET")
    except MethodNotAllowed:
        return
    assert endpoint != rule.endpoint  # a GET falls through to the catch-all list page


def test_get_logout_is_refused(admin):
    _, c = admin
    assert c.get("/logout").status_code == 405
    assert c.get("/me").status_code == 200


def test_logout_post_signs_out(admin):
    _, c = admin
    assert c.get("/me").status_code == 200
    assert c.post("/logout").status_code == 302
    assert c.get("/me").status_code != 200


def test_templates_post_to_logout_and_gdrive_watch():
    for template in (REPO / "cps" / "templates").glob("*.html"):
        text = template.read_text()
        for endpoint in ("web.logout", "gdrive.watch_gdrive", "gdrive.revoke_watch_gdrive"):
            assert not re.search(r"href=\"\{\{\s*url_for\('%s'" % re.escape(endpoint), text), (template.name, endpoint)
    rail = (REPO / "cps" / "templates" / "settings_layout.html").read_text()
    assert "method=\"post\" action=\"{{ url_for('web.logout') }}\"" in rail
