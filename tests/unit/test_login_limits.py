"""Login rate limit, the OPDS limiter key without credentials, and Remember me off by default."""

import base64
import re
from pathlib import Path

import pytest

from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def env(tmp_path):
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(e.app)
        # Production enables the limiter; the shared harness turns it off.
        e.app.config.update(RATELIMIT_ENABLED=True, RATELIMIT_STORAGE_URI="memory://")
        from cps import limiter
        limiter.init_app(e.app)
        limiter.reset()
        yield e
        limiter.reset()


def _wrong(client, name):
    return client.post("/login", data={"username": name, "password": "wrong"})


def test_login_brute_force_is_limited_with_a_friendly_message(env):
    c = env.app.test_client()
    for _ in range(5):
        assert _wrong(c, "victim").status_code == 200
    resp = _wrong(c, "victim")
    assert resp.status_code == 429
    assert "Too many sign-in attempts" in resp.get_data(as_text=True)


def test_limit_is_per_username(env):
    c = env.app.test_client()
    for _ in range(6):
        _wrong(c, "victim-b")
    assert _wrong(c, "victim-b").status_code == 429
    assert _wrong(c, "other-user").status_code != 429


def test_limit_key_matches_login_normalisation(env):
    c = env.app.test_client()
    for name in ["normvictim", "n\normvictim", "​normvictim", "NORMVICTIM", "normvictim​"]:
        _wrong(c, name)
    assert _wrong(c, "normvictim").status_code == 429


def test_successful_login_clears_the_bucket(env):
    name = env.admin().name
    c = env.app.test_client()
    for _ in range(4):
        _wrong(c, name)
    assert c.post("/login", data={"username": name, "password": ADMIN_PASSWORD}).status_code == 302
    c.get("/logout")
    for _ in range(5):
        assert _wrong(c, name).status_code == 200


def test_opds_limit_key_survives_missing_credentials(env):
    from cps.main import request_username
    with env.app.test_request_context("/opds", environ_overrides={"REMOTE_ADDR": "10.0.0.9"}):
        assert request_username() == "10.0.0.9"
    creds = base64.b64encode(b"reader:secret").decode()
    with env.app.test_request_context("/opds", headers={"Authorization": "Basic " + creds},
                                      environ_overrides={"REMOTE_ADDR": "10.0.0.9"}):
        assert request_username() == "reader"


def test_remember_me_unchecked_by_default():
    html = (REPO_ROOT / "cps/templates/login.html").read_text(encoding="utf-8")
    tags = re.findall(r"<input[^>]*name=\"remember_me\"[^>]*>", html)
    assert tags
    for tag in tags:
        assert "checked" not in tag
