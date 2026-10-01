"""Changing a password needs the current one and signs out every other session;
turning on 2FA does the same."""

import pytest
from werkzeug.security import check_password_hash

from cps import totp
from tests.unit.lily_env import lily_env

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path):
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(e.app)
        yield e


def _reader(env):
    from cps import constants
    env.add_user("reader", password="old-pw-1",
                 role=constants.ROLE_USER | constants.ROLE_DOWNLOAD | constants.ROLE_PASSWD)


def _client(env, name, password):
    c = env.app.test_client()
    assert c.post("/login", data={"username": name, "password": password}).status_code == 302
    return c


def _signed_in(c):
    return c.get("/me").status_code == 200


def _password_ok(env, name, password):
    env.ub.session.expire_all()
    user = env.ub.session.query(env.ub.User).filter(env.ub.User.name == name).one()
    return check_password_hash(user.password, password)


def _profile_form(**extra):
    return dict({"email": "reader@example.org", "locale": "en", "default_language": "all"}, **extra)


def test_profile_password_change_needs_the_current_password(env):
    _reader(env)
    c = _client(env, "reader", "old-pw-1")
    resp = c.post("/me", data=_profile_form(password="New-pw-12345"))
    assert "Current password is incorrect" in resp.get_data(as_text=True)
    assert _password_ok(env, "reader", "old-pw-1")

    c.post("/me", data=_profile_form(password="New-pw-12345", current_password="wrong"))
    assert _password_ok(env, "reader", "old-pw-1")

    c.post("/me", data=_profile_form(password="New-pw-12345", current_password="old-pw-1"))
    assert _password_ok(env, "reader", "New-pw-12345")


def test_profile_without_a_new_password_needs_no_current_password(env):
    _reader(env)
    c = _client(env, "reader", "old-pw-1")
    assert c.post("/me", data=_profile_form()).status_code == 302


def test_profile_password_change_signs_out_other_sessions(env):
    _reader(env)
    here = _client(env, "reader", "old-pw-1")
    elsewhere = _client(env, "reader", "old-pw-1")
    here.post("/me", data=_profile_form(password="New-pw-12345", current_password="old-pw-1"))
    assert _signed_in(here)
    assert not _signed_in(elsewhere)


def test_change_password_page_signs_out_other_sessions(env):
    _reader(env)
    here = _client(env, "reader", "old-pw-1")
    elsewhere = _client(env, "reader", "old-pw-1")
    resp = here.post("/change-password", data={"current_password": "old-pw-1", "new_password": "New-pw-12345",
                                               "confirm_password": "New-pw-12345"})
    assert resp.status_code == 302
    assert _signed_in(here)
    assert not _signed_in(elsewhere)


def test_enabling_2fa_signs_out_other_sessions(env):
    _reader(env)
    here = _client(env, "reader", "old-pw-1")
    elsewhere = _client(env, "reader", "old-pw-1")
    here.post("/account/security/2fa/start")
    html = here.get("/account/security").get_data(as_text=True)
    secret = html.split('id="totp-secret">')[1].split("<")[0]
    here.post("/account/security/2fa/enable", data={"code": totp.current_code(secret)})
    assert _signed_in(here)
    assert not _signed_in(elsewhere)
