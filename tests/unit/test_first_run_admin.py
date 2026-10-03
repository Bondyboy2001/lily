# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""First-run admin: a random password printed once (or LILY_ADMIN_PASSWORD), never the
published default, and existing app.db files left alone."""

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


def _printed_passwords(out):
    return [line.split("Password:", 1)[1].strip() for line in out.splitlines() if "Password:" in line]


def test_new_install_gets_a_random_password_printed_once(init_db, capsys):
    from cps import constants
    init_db()
    printed = _printed_passwords(capsys.readouterr().out)
    assert len(printed) == 1
    admin = _admin()
    assert len(printed[0]) >= 16
    assert check_password_hash(admin.password, printed[0])
    assert not check_password_hash(admin.password, constants.LEGACY_DEFAULT_PASSWORD)
    assert admin.force_password_change is True

    # A restart on the same app.db neither prints nor changes anything
    stored = admin.password
    init_db()
    assert _printed_passwords(capsys.readouterr().out) == []
    assert _admin().password == stored


def test_each_install_gets_a_different_password(tmp_path, monkeypatch):
    from cps import ub, constants
    monkeypatch.delenv(constants.ADMIN_PASSWORD_ENV, raising=False)
    assert ub._initial_admin_password()[0] != ub._initial_admin_password()[0]
    assert ub._initial_admin_password()[1] is True


def test_env_password_is_used_and_not_printed(init_db, capsys, monkeypatch):
    from cps import constants
    monkeypatch.setenv(constants.ADMIN_PASSWORD_ENV, "chosen-first-pw")
    init_db()
    assert _printed_passwords(capsys.readouterr().out) == []
    admin = _admin()
    assert check_password_hash(admin.password, "chosen-first-pw")
    assert admin.force_password_change is True


def test_existing_install_keeps_its_admin_password(init_db, capsys, monkeypatch):
    from cps import ub
    path = init_db()
    admin = _admin()
    admin.password = generate_password_hash("my-own-password")
    ub.session.commit()
    capsys.readouterr()

    monkeypatch.setenv("LILY_ADMIN_PASSWORD", "ignored-after-first-run")
    init_db()
    assert _printed_passwords(capsys.readouterr().out) == []
    assert check_password_hash(_admin().password, "my-own-password")
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM user WHERE role & 32 = 0").fetchone()[0] == 1


def test_app_db_without_accounts_gets_an_admin(init_db, capsys):
    """An app.db holding only the Guest row (like a stripped template) gets the first admin."""
    from cps import ub, constants
    path = init_db()
    ub.session.query(ub.User).filter(ub.User.name == constants.DEFAULT_ADMIN_NAME).delete()
    ub.session.commit()
    capsys.readouterr()
    init_db()
    printed = _printed_passwords(capsys.readouterr().out)
    assert len(printed) == 1
    assert check_password_hash(_admin().password, printed[0])
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM user WHERE name='Guest'").fetchone()[0] == 1
