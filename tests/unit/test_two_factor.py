"""TOTP second factor and personal API tokens."""

import base64

import pytest

from cps import totp
from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------- pure functions

def test_rfc6238_vector():
    secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
    assert totp.current_code(secret, now=59) == "287082"
    assert totp.current_code(secret, now=1111111109) == "081804"


def test_verify_code_window_and_replay():
    secret = totp.generate_secret()
    now = 1_000_000.0
    code = totp.current_code(secret, now=now)
    step = totp.verify_code(secret, code, now=now)
    assert step == int(now // 30)
    assert totp.verify_code(secret, code, last_step=step, now=now) is None  # replay refused
    assert totp.verify_code(secret, code, now=now + 30) == step            # one step of drift ok
    assert totp.verify_code(secret, code, now=now + 90) is None            # too old
    assert totp.verify_code(secret, "12345", now=now) is None
    assert totp.verify_code(secret, "abcdef", now=now) is None


def test_failure_tracker_locks_and_expires():
    t = totp.FailureTracker(max_failures=3, lockout=60)
    for _ in range(2):
        t.failure(1, now=0)
    assert not t.locked(1, now=1)
    t.failure(1, now=2)
    assert t.locked(1, now=3) and t.locked_keys(now=3) == [1]
    assert not t.locked(1, now=63)
    t.failure(2, now=0)
    t.success(2)
    assert not t.locked(2)


def test_api_tokens_are_prefixed_and_hashed():
    token = totp.new_api_token()
    assert totp.looks_like_api_token(token) and not totp.looks_like_api_token("hunter2")
    assert totp.hash_api_token(token) != token and len(totp.hash_api_token(token)) == 64


# ---------------------------------------------------------------- web flow

@pytest.fixture
def env(tmp_path):
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(e.app)
        from cps import web
        web._2fa_failures = totp.FailureTracker()
        yield e


def _login(client, name, password):
    return client.post("/login", data={"username": name, "password": password})


def _enable_2fa(env, client):
    """Walks the setup UI and returns the secret."""
    assert client.post("/account/security/2fa/start").status_code == 302
    html = client.get("/account/security").get_data(as_text=True)
    secret = html.split('id="totp-secret">')[1].split("<")[0]
    resp = client.post("/account/security/2fa/enable", data={"code": totp.current_code(secret)})
    assert resp.status_code == 302
    return secret


def _fresh_code(secret, user_step):
    """A valid code for a step after the one the user last used."""
    import time
    return totp.current_code(secret, now=time.time() + 30 * (1 if user_step else 0))


def test_setup_then_login_requires_code(env):
    name = env.admin().name
    c = env.app.test_client()
    assert _login(c, name, ADMIN_PASSWORD).status_code == 302
    secret = _enable_2fa(env, c)
    assert env.admin().totp_enabled
    c.get("/logout")

    c2 = env.app.test_client()
    resp = _login(c2, name, ADMIN_PASSWORD)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/login/2fa")
    assert c2.get("/").status_code != 200 or "login" in c2.get("/").get_data(as_text=True).lower()

    # the step used during setup is spent, so the next valid code is for the following step
    bad = c2.post("/login/2fa", data={"code": "000000"})
    assert bad.status_code == 200 and "Wrong code" in bad.get_data(as_text=True)
    good = c2.post("/login/2fa", data={"code": _fresh_code(secret, True)})
    assert good.status_code == 302 and not good.headers["Location"].endswith("/login/2fa")
    assert c2.get("/account/security").status_code == 200


def test_too_many_wrong_codes_lock_the_account(env):
    name = env.admin().name
    c = env.app.test_client()
    _login(c, name, ADMIN_PASSWORD)
    secret = _enable_2fa(env, c)
    c.get("/logout")
    c2 = env.app.test_client()
    _login(c2, name, ADMIN_PASSWORD)
    for _ in range(totp.MAX_FAILURES):
        c2.post("/login/2fa", data={"code": "000000"})
    resp = c2.post("/login/2fa", data={"code": _fresh_code(secret, True)})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/login")


def test_2fa_page_without_password_step_redirects_to_login(env):
    resp = env.app.test_client().get("/login/2fa")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/login")


def test_disable_needs_password_and_code(env):
    name = env.admin().name
    c = env.app.test_client()
    _login(c, name, ADMIN_PASSWORD)
    secret = _enable_2fa(env, c)
    c.post("/account/security/2fa/disable", data={"password": "wrong", "code": _fresh_code(secret, True)})
    assert env.admin().totp_enabled
    c.post("/account/security/2fa/disable", data={"password": ADMIN_PASSWORD, "code": _fresh_code(secret, True)})
    assert not env.admin().totp_enabled


def test_wrong_confirmation_code_does_not_enable(env):
    c = env.app.test_client()
    _login(c, env.admin().name, ADMIN_PASSWORD)
    c.post("/account/security/2fa/start")
    c.post("/account/security/2fa/enable", data={"code": "000000"})
    assert not env.admin().totp_enabled


# ---------------------------------------------------------------- API tokens

def _basic(name, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{name}:{password}".encode()).decode()}


def _make_token(env, client):
    assert client.post("/account/security/token/create", data={"password": ADMIN_PASSWORD}).status_code == 302
    html = client.get("/account/security").get_data(as_text=True)
    return html.split('id="new-api-token">')[1].split("<")[0]


def test_token_requires_password_is_shown_once_and_stored_hashed(env):
    c = env.app.test_client()
    _login(c, env.admin().name, ADMIN_PASSWORD)
    c.post("/account/security/token/create", data={"password": "nope"})
    assert env.admin().api_token_hash is None
    token = _make_token(env, c)
    assert token.startswith("lily_") and env.admin().api_token_hash == totp.hash_api_token(token)
    assert "new-api-token" not in c.get("/account/security").get_data(as_text=True)


def test_opds_accepts_token_and_password_until_2fa_is_on(env):
    name = env.admin().name
    c = env.app.test_client()
    _login(c, name, ADMIN_PASSWORD)
    token = _make_token(env, c)
    opds = env.app.test_client()
    assert opds.get("/opds", headers=_basic(name, ADMIN_PASSWORD)).status_code == 200
    assert opds.get("/opds", headers=_basic(name, token)).status_code == 200
    assert opds.get("/opds", headers=_basic(name, totp.new_api_token())).status_code == 401

    _enable_2fa(env, c)
    assert opds.get("/opds", headers=_basic(name, ADMIN_PASSWORD)).status_code == 401  # no 2FA bypass
    assert opds.get("/opds", headers=_basic(name, token)).status_code == 200


def test_bearer_token_authenticates_web_endpoints_and_revoke_stops_it(env):
    c = env.app.test_client()
    _login(c, env.admin().name, ADMIN_PASSWORD)
    token = _make_token(env, c)
    anon = env.app.test_client()
    assert anon.get("/account/security").status_code in (302, 401)
    assert anon.get("/account/security", headers={"Authorization": "Bearer " + token}).status_code == 200
    c.post("/account/security/token/revoke")
    assert anon.get("/account/security", headers={"Authorization": "Bearer " + token}).status_code in (302, 401)
