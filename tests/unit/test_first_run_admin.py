# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""First-run admin: admin / admin123 (or LILY_ADMIN_PASSWORD), changed at first sign-in,
and existing app.db files left alone."""

import sqlite3

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

pytestmark = pytest.mark.unit


@pytest.fixture
def init_db(tmp_path, monkeypatch):
    """ub.init_db on tmp_path/app.db (callable, may run twice), restoring the ub globals."""
    from cps import ub, constants
    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv(constants.ADMIN_PASSWORD_ENV, raising=False)
    saved = (ub.session, ub.app_DB_path)
    path = tmp_path / "app.db"

    def run():
        if ub.session is not None and ub.session is not saved[0]:
            ub.session.close()
            ub.session.bind.dispose()
        ub.init_db(str(path))
        return path

    yield run
    try:
        if ub.session is not None and ub.session is not saved[0]:
            ub.session.close()
            ub.session.bind.dispose()
    finally:
        ub.session, ub.app_DB_path = saved


def _admin():
    from cps import ub, constants
    return ub.session.query(ub.User).filter(ub.User.name == constants.DEFAULT_ADMIN_NAME).one()


def test_new_install_signs_in_as_admin_admin123(init_db):
    from cps import constants
    init_db()
    admin = _admin()
    assert admin.name == "admin"
    assert check_password_hash(admin.password, "admin123")
    assert admin.force_password_change is True

    # A restart on the same app.db changes nothing
    stored = admin.password
    init_db()
    assert _admin().password == stored
    assert constants.DEFAULT_PASSWORD == "admin123"


def test_env_password_is_used(init_db, monkeypatch):
    from cps import constants
    monkeypatch.setenv(constants.ADMIN_PASSWORD_ENV, "chosen-first-pw")
    init_db()
    admin = _admin()
    assert check_password_hash(admin.password, "chosen-first-pw")
    assert admin.force_password_change is True


def test_existing_install_keeps_its_admin_password(init_db, monkeypatch):
    from cps import ub
    path = init_db()
    admin = _admin()
    admin.password = generate_password_hash("my-own-password")
    ub.session.commit()

    monkeypatch.setenv("LILY_ADMIN_PASSWORD", "ignored-after-first-run")
    init_db()
    assert check_password_hash(_admin().password, "my-own-password")
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM user WHERE role & 32 = 0").fetchone()[0] == 1


def test_app_db_without_accounts_gets_an_admin(init_db):
    """An app.db holding only the Guest row (like a stripped template) gets the first admin."""
    from cps import ub, constants
    path = init_db()
    ub.session.query(ub.User).filter(ub.User.name == constants.DEFAULT_ADMIN_NAME).delete()
    ub.session.commit()
    init_db()
    assert check_password_hash(_admin().password, "admin123")
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM user WHERE name='Guest'").fetchone()[0] == 1


def test_accounts_on_either_default_password_must_change_it(init_db):
    from cps import ub
    init_db()
    for pw in ("admin123", "harry10"):
        admin = _admin()
        admin.password = generate_password_hash(pw)
        admin.force_password_change = False
        ub.session.commit()
        ub.flag_users_with_default_password(ub.session)
        assert _admin().force_password_change is True, pw
