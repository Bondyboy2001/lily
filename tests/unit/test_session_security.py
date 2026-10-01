"""Session signing key and remember-me cookie checks."""

import hashlib
import sqlite3

import pytest
from itsdangerous import URLSafeSerializer
from flask.json.tag import TaggedJSONSerializer
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import config_sql
from tests.unit.lily_env import REPO, lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------- session-signing key

def test_shipped_template_has_no_session_key_or_sessions():
    con = sqlite3.connect(REPO / "empty_library" / "app.db")
    try:
        assert con.execute("SELECT COUNT(*) FROM flask_settings").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM user_session").fetchone()[0] == 0
    finally:
        con.close()


def _settings_session(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "app.db"))
    config_sql._Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE user_session (id INTEGER PRIMARY KEY, user_id INTEGER, "
                             "session_key TEXT, random TEXT, expiry INTEGER)")
        conn.exec_driver_sql("INSERT INTO user_session (user_id, session_key, random) VALUES (1, 'k', 'r')")
    return sessionmaker(bind=engine)()


def test_known_template_key_is_replaced_and_sessions_dropped(tmp_path, monkeypatch):
    key = b"known-template-key"
    monkeypatch.setattr(config_sql, "_KNOWN_TEMPLATE_KEY_HASHES",
                        frozenset({hashlib.sha256(key).hexdigest()}))
    s = _settings_session(tmp_path)
    s.add(config_sql._Flask_Settings(key))
    s.commit()

    new_key = config_sql.get_flask_session_key(s)
    assert new_key != key and len(new_key) == 32
    assert config_sql.get_flask_session_key(s) == new_key  # persisted, stable afterwards
    assert s.execute(config_sql.text("SELECT COUNT(*) FROM user_session")).scalar() == 0


def test_private_key_is_kept(tmp_path):
    s = _settings_session(tmp_path)
    s.add(config_sql._Flask_Settings(b"x" * 32))
    s.commit()
    assert config_sql.get_flask_session_key(s) == b"x" * 32
    assert s.execute(config_sql.text("SELECT COUNT(*) FROM user_session")).scalar() == 1


def test_missing_key_is_generated(tmp_path):
    s = _settings_session(tmp_path)
    key = config_sql.get_flask_session_key(s)
    assert len(key) == 32 and config_sql.get_flask_session_key(s) == key


# ---------------------------------------------------------------- remember-me cookie

@pytest.fixture
def env(tmp_path):
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(e.app)
        yield e


def _remember_cookie(app, user_id, random):
    signer = URLSafeSerializer(app.secret_key, salt="remember", serializer=TaggedJSONSerializer(),
                               signer_kwargs=dict(key_derivation="hmac", digest_method=hashlib.sha1))
    return signer.dumps({"user": str(user_id), "random": random})


def _signed_in(client):
    resp = client.get("/me")
    return resp.status_code == 200


def test_valid_remember_cookie_signs_in(env):
    c = env.app.test_client()
    c.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD, "remember_me": "on"})
    cookie = c.get_cookie("remember_token")
    assert cookie is not None

    fresh = env.app.test_client()
    fresh.set_cookie("remember_token", cookie.value)
    assert _signed_in(fresh)


@pytest.mark.parametrize("random", ["", None, "no-such-session"])
def test_remember_cookie_without_session_record_is_refused(env, random):
    c = env.app.test_client()
    c.set_cookie("remember_token", _remember_cookie(env.app, env.admin().id, random))
    assert not _signed_in(c)


def test_remember_cookie_of_another_user_session_is_refused(env):
    ub = env.ub
    other = env.add_user("other")
    ub.session.add(ub.User_Sessions(other.id, "key", "other-random", 0))
    ub.session.commit()
    c = env.app.test_client()
    c.set_cookie("remember_token", _remember_cookie(env.app, env.admin().id, "other-random"))
    assert not _signed_in(c)


def test_remember_cookie_stops_working_after_logout(env):
    c = env.app.test_client()
    c.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD, "remember_me": "on"})
    cookie = c.get_cookie("remember_token").value
    c.post("/logout")

    replay = env.app.test_client()
    replay.set_cookie("remember_token", cookie)
    assert not _signed_in(replay)
