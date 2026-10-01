"""Password login rate limit, the wrong-code lockout that survives restarts, and the
login page not echoing a password back."""

import pytest

import cps
from cps import totp
from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, monkeypatch):
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(e.app)
        # lily_env turns rate limiting off; switch it on with fresh in-memory storage
        monkeypatch.setattr(cps.limiter, "enabled", cps.limiter.enabled)
        e.app.config["RATELIMIT_ENABLED"] = True
        cps.limiter.init_app(e.app)
        yield e


def _login(client, name, password):
    return client.post("/login", data={"username": name, "password": password})


def _signed_in(client):
    return client.get("/me").status_code == 200


def test_password_logins_are_rate_limited(env):
    c = env.app.test_client()
    for _ in range(5):
        assert _login(c, env.admin().name, "wrong").status_code == 200
    resp = _login(c, env.admin().name, ADMIN_PASSWORD)
    assert resp.status_code == 429
    assert "Too many failed sign-in attempts" in resp.get_data(as_text=True)
    assert not _signed_in(c)


def test_limit_is_per_username_too(env, monkeypatch):
    env.add_user("victim", password="pw")
    c = env.app.test_client()
    for i in range(5):
        c.post("/login", data={"username": "victim", "password": "wrong"},
               environ_base={"REMOTE_ADDR": "10.0.0.%d" % i})
    resp = c.post("/login", data={"username": "victim", "password": "pw"},
                  environ_base={"REMOTE_ADDR": "10.0.0.99"})
    assert resp.status_code == 429


def test_success_clears_the_failure_count(env):
    c = env.app.test_client()
    for _ in range(4):
        _login(c, env.admin().name, "wrong")
    assert _login(c, env.admin().name, ADMIN_PASSWORD).status_code == 302
    c.post("/logout")
    for _ in range(4):
        _login(c, env.admin().name, "wrong")
    assert _login(c, env.admin().name, ADMIN_PASSWORD).status_code == 302


def test_failed_login_does_not_echo_the_password(env):
    resp = _login(env.app.test_client(), env.admin().name, "s3cret-guess-123")
    html = resp.get_data(as_text=True)
    assert "Wrong Username or Password" in html
    assert "s3cret-guess-123" not in html


# ---------------------------------------------------------------- 2FA lockout

def test_lockouts_escalate():
    t = totp.FailureTracker(max_failures=2, lockout=60, max_lockout=200)
    t.failure(1, now=0)
    t.failure(1, now=0)
    assert t.locked(1, now=59) and not t.locked(1, now=60)
    t.failure(1, now=100)
    t.failure(1, now=100)
    assert t.locked(1, now=219) and not t.locked(1, now=220)      # doubled to 120 s
    t.failure(1, now=300)
    t.failure(1, now=300)
    assert t.locked(1, now=499) and not t.locked(1, now=500)      # capped at 200 s
    t.success(1)
    t.failure(1, now=600)
    t.failure(1, now=600)
    assert not t.locked(1, now=660)                               # back to the base lockout


def test_lockout_survives_a_restart(env):
    from cps import web_auth
    user = env.add_user("locked", password="pw")
    first = totp.FailureTracker(max_failures=2, lockout=600, store=web_auth._UserLockoutStore())
    first.failure(user.id)
    first.failure(user.id)
    assert first.locked(user.id)

    env.ub.session.expire_all()
    after_restart = totp.FailureTracker(max_failures=2, lockout=600, store=web_auth._UserLockoutStore())
    assert after_restart.locked(user.id)
    assert after_restart.locked_keys() == [user.id]
    assert 0 < after_restart.seconds_left(user.id) <= 600

    after_restart.success(user.id)   # what the admin's "unlock" does
    assert not after_restart.locked(user.id) and after_restart.locked_keys() == []
