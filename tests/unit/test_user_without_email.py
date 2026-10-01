# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""E-mail is gone from the user forms: users are created and edited without one, and
several users without an address can coexist (the old UNIQUE column stays)."""

import pytest

from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


@pytest.fixture
def admin_client(tmp_path):
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(env.app)
        client = env.app.test_client()
        client.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
        yield env, client


def _new_user(client, name):
    return client.post("/admin/user/new", data={"name": name, "password": "Sec-ret-123!",
                                                 "default_language": "all", "locale": "en"})


def test_admin_creates_several_users_without_email(admin_client):
    env, client = admin_client
    for name in ("alice", "bob"):
        resp = _new_user(client, name)
        assert resp.status_code == 302, resp.get_data(as_text=True)[:500]
    ub = env.ub
    users = {u.name: u for u in ub.session.query(ub.User).filter(ub.User.name.in_(["alice", "bob"]))}
    assert set(users) == {"alice", "bob"}
    assert all(u.email is None for u in users.values())


def test_user_forms_have_no_email_field(admin_client):
    env, client = admin_client
    page = client.get("/admin/user/new").get_data(as_text=True)
    assert 'name="name"' in page
    assert 'name="email"' not in page


def test_user_table_rejects_email_edits(admin_client):
    env, client = admin_client
    user = env.add_user("carol")
    resp = client.post("/ajax/editlistusers/email", data={"pk": user.id, "value": "c@example.org"})
    env.ub.session.expire_all()
    assert env.ub.session.get(env.ub.User, user.id).email == "carol@example.org"
    assert resp.status_code in (200, 400)
