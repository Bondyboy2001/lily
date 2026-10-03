# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Backend hardening: forced default-password change, content restrictions on reading,
missing User-Agent headers and web reader progress."""

import base64
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from .lily_env import lily_env, ADMIN_PASSWORD

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def env(tmp_path):
    with lily_env(tmp_path) as e:
        # lily_env doesn't install CSRFProtect, which normally provides this template global
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "")
        yield e


def _login(env, name=None, password=ADMIN_PASSWORD):
    client = env.app.test_client()
    resp = client.post("/login", data={"username": name or env.admin().name, "password": password})
    assert resp.status_code in (200, 302), resp.data[:300]
    return client


def _add_book_file(env, book_id, fmt="epub", content=b"PK\x03\x04 not really an epub"):
    """Create the book file lily_env's add_book points at (<author>/<title>/<title>.<fmt>)."""
    con = sqlite3.connect(env.library_dir / "metadata.db")
    try:
        path, name = con.execute("SELECT b.path, d.name FROM books b JOIN data d ON d.book = b.id "
                                 "WHERE b.id = ?", (book_id,)).fetchone()
    finally:
        con.close()
    folder = env.library_dir / path
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.{fmt}").write_bytes(content)


def _restricted_user(env):
    from cps import constants
    env.add_user("reader", role=constants.ROLE_VIEWER | constants.ROLE_DOWNLOAD, denied_tags="Secret")
    return _login(env, "reader", "pw")


# --------------------------------------------------------------------------- 1. forced password change
@pytest.fixture
def app_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from cps import ub

    engine = create_engine("sqlite://")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.mark.unit
class TestDefaultPasswordFlag:
    def test_new_default_admin_is_flagged(self, app_session):
        from cps import ub, constants
        ub.create_admin_user(app_session)
        admin = app_session.query(ub.User).filter(ub.User.name == constants.DEFAULT_ADMIN_NAME).one()
        assert admin.force_password_change is True

    def test_setting_a_password_clears_the_flag(self, app_session):
        from werkzeug.security import generate_password_hash
        from cps import ub, constants
        ub.create_admin_user(app_session)
        admin = app_session.query(ub.User).filter(ub.User.name == constants.DEFAULT_ADMIN_NAME).one()
        admin.password = generate_password_hash("something-else")
        app_session.commit()
        assert admin.force_password_change is False

    def test_startup_check_flags_admins_on_the_default_password(self, app_session):
        from werkzeug.security import generate_password_hash
        from cps import ub, constants
        ub.create_admin_user(app_session)
        admin = app_session.query(ub.User).filter(ub.User.name == constants.DEFAULT_ADMIN_NAME).one()
        admin.password = generate_password_hash(constants.LEGACY_DEFAULT_PASSWORD)  # an old install
        admin.force_password_change = False
        other = ub.User(name="other", email="o@example.org", role=constants.ROLE_ADMIN,
                        password=generate_password_hash("not-default"))
        app_session.add(other)
        app_session.commit()

        ub.flag_users_with_default_password(app_session)
        assert admin.force_password_change is True
        assert not other.force_password_change


@pytest.mark.unit
class TestForcedPasswordChangeFlow:
    @pytest.fixture
    def forced(self, env, monkeypatch):
        # lily_env registers only some blueprints, so full pages (layout.html) can't be built here
        import cps.web
        import cps.web_auth
        for module in (cps.web, cps.web_auth):
            monkeypatch.setattr(module, "render_title_template",
                                lambda template, **kw: f"{template} forced={kw.get('forced')}")
        admin = env.admin()
        admin.force_password_change = True
        env.ub.session.commit()
        return env

    def test_web_pages_redirect_to_change_page(self, forced):
        client = _login(forced)
        resp = client.get("/")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/change-password")
        assert client.post("/ajax/toggleread/1").status_code == 403

    def test_exempt_endpoints_still_work(self, forced):
        client = _login(forced)
        resp = client.get("/change-password")
        assert resp.status_code == 200
        assert resp.get_data(as_text=True) == "change_password.html forced=True"
        assert client.get("/health").status_code in (200, 503)

    @pytest.mark.parametrize("path", ["/opds", "/opds/new"])
    def test_opds_requires_password_change(self, forced, path):
        creds = base64.b64encode(f"{forced.admin().name}:{ADMIN_PASSWORD}".encode()).decode()
        resp = forced.app.test_client().get(path, headers={"Authorization": f"Basic {creds}"})
        assert resp.status_code == 401
        assert resp.headers.get("WWW-Authenticate", "").startswith("Basic")

    def test_wrong_current_password_keeps_the_flag(self, forced):
        client = _login(forced)
        resp = client.post("/change-password", data={"current_password": "nope", "new_password": "N3w-passw0rd!",
                                                      "confirm_password": "N3w-passw0rd!"})
        assert resp.status_code == 200
        forced.ub.session.expire_all()
        assert forced.admin().force_password_change is True

    def test_default_password_is_rejected_as_new_password(self, forced):
        from cps import constants
        client = _login(forced)
        client.post("/change-password", data={"current_password": ADMIN_PASSWORD,
                                              "new_password": constants.LEGACY_DEFAULT_PASSWORD,
                                              "confirm_password": constants.LEGACY_DEFAULT_PASSWORD})
        forced.ub.session.expire_all()
        assert forced.admin().force_password_change is True

    def test_changing_the_password_unlocks_the_app(self, forced):
        from werkzeug.security import check_password_hash
        client = _login(forced)
        resp = client.post("/change-password", data={"current_password": ADMIN_PASSWORD,
                                                      "new_password": "N3w-passw0rd!",
                                                      "confirm_password": "N3w-passw0rd!"})
        assert resp.status_code == 302
        forced.ub.session.expire_all()
        admin = forced.admin()
        assert admin.force_password_change is False
        assert check_password_hash(admin.password, "N3w-passw0rd!")
        assert client.get("/ajax/emailstat").status_code == 200
        anon = forced.app.test_client()
        old_creds = base64.b64encode(f"{admin.name}:{ADMIN_PASSWORD}".encode()).decode()
        assert anon.get("/opds", headers={"Authorization": f"Basic {old_creds}"}).status_code == 401
        # OPDS uses HTTP basic auth and must keep working for e-readers
        new_creds = base64.b64encode(f"{admin.name}:N3w-passw0rd!".encode()).decode()
        resp = anon.get("/opds", headers={"Authorization": f"Basic {new_creds}"})
        assert resp.status_code == 200
        assert resp.headers.get("Content-Type", "").startswith("application/atom+xml")


@pytest.mark.unit
def test_change_password_template_uses_design_tokens_only():
    text = (REPO / "cps/templates/change_password.html").read_text()
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", text)
    assert "style=" not in text


# --------------------------------------------------------------------------- 2. content restrictions
@pytest.mark.unit
class TestContentRestrictions:
    def test_serve_book_respects_denied_tags(self, env):
        allowed = env.add_book("Open Book", tags=("Public",))
        hidden = env.add_book("Hidden Book", tags=("Secret",))
        _add_book_file(env, allowed)
        _add_book_file(env, hidden)
        client = _restricted_user(env)
        assert client.get(f"/show/{allowed}/epub").status_code == 200
        assert client.get(f"/show/{hidden}/epub").status_code == 404


# --------------------------------------------------------------------------- 3. missing User-Agent
@pytest.mark.unit
def test_no_single_argument_user_agent_lookups():
    offenders = []
    for path in (REPO / "cps").rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"in request\.headers\.get\(['\"]User-Agent['\"]\)", line):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}")
    assert not offenders, offenders


@pytest.mark.unit
def test_download_without_user_agent_does_not_crash(env):
    book_id = env.add_book("Plain Book")
    _add_book_file(env, book_id)
    client = _login(env)
    inner = env.app.wsgi_app

    def strip_user_agent(environ, start_response):
        environ.pop("HTTP_USER_AGENT", None)
        return inner(environ, start_response)

    env.app.wsgi_app = strip_user_agent
    assert client.get(f"/download/{book_id}/epub").status_code == 200


@pytest.mark.unit
@pytest.mark.parametrize("author, file_name", [
    ("Frank Herbert", "Dune - Frank Herbert.epub"),
    ("Unknown", "Dune.epub"),  # calibre's stand-in for no author is not one to name the file after
])
def test_a_download_is_named_after_the_title_and_the_author_it_has(env, author, file_name):
    from urllib.parse import quote
    book_id = env.add_book("Dune", author=author)
    _add_book_file(env, book_id)
    disposition = _login(env).get(f"/download/{book_id}/epub").headers["Content-Disposition"]
    assert disposition.startswith(f"attachment; filename={quote(file_name)};")


# --------------------------------------------------------------------------- 4. web reader progress
def _read_status(env, book_id):
    ub = env.ub
    ub.session.expire_all()
    row = ub.session.query(ub.ReadBook).filter(ub.ReadBook.book_id == book_id,
                                               ub.ReadBook.user_id == env.admin().id).first()
    return row.read_status if row else None


@pytest.mark.unit
class TestWebReaderProgress:
    def test_empty_progress(self, env):
        book_id = env.add_book("Progress Book")
        client = _login(env)
        resp = client.get(f"/ajax/progress/{book_id}")
        assert resp.status_code == 200
        assert resp.get_json() == {"cfi": None, "percent": None, "updated": None, "format": None}

    def test_save_and_load(self, env):
        book_id = env.add_book("Progress Book")
        client = _login(env)
        resp = client.post(f"/ajax/progress/{book_id}", json={"cfi": "epubcfi(/6/4!/4/2/1:0)", "percent": 0.25})
        assert resp.status_code == 200
        data = client.get(f"/ajax/progress/{book_id}").get_json()
        assert data["cfi"] == "epubcfi(/6/4!/4/2/1:0)"
        assert data["percent"] == pytest.approx(0.25)
        updated = datetime.fromisoformat(data["updated"])
        assert updated.tzinfo is not None
        assert _read_status(env, book_id) == env.ub.ReadBook.STATUS_IN_PROGRESS

        client.post(f"/ajax/progress/{book_id}", json={"cfi": "epubcfi(/6/8)", "percent": 0.5})
        assert client.get(f"/ajax/progress/{book_id}").get_json()["cfi"] == "epubcfi(/6/8)"
        rows = env.ub.session.query(env.ub.WebReaderProgress).filter_by(book_id=book_id).count()
        assert rows == 1

    def test_finishing_marks_read_and_does_not_reopen(self, env):
        book_id = env.add_book("Progress Book")
        client = _login(env)
        client.post(f"/ajax/progress/{book_id}", json={"cfi": "epubcfi(/6/99)", "percent": 0.995, "seconds": 300})
        assert _read_status(env, book_id) == env.ub.ReadBook.STATUS_FINISHED
        # scrolling back after finishing keeps the book finished
        client.post(f"/ajax/progress/{book_id}", json={"cfi": "epubcfi(/6/2)", "percent": 0.1})
        assert _read_status(env, book_id) == env.ub.ReadBook.STATUS_FINISHED

    def test_reading_a_finished_book_again_from_the_start_reopens_it(self, env):
        book_id = env.add_book("Progress Book")
        client = _login(env)
        client.post(f"/ajax/progress/{book_id}", json={"cfi": "epubcfi(/6/99)", "percent": 1.0, "seconds": 300})
        assert _read_status(env, book_id) == env.ub.ReadBook.STATUS_FINISHED
        # just opening it at the very start (the cover) doesn't count yet
        client.post(f"/ajax/progress/{book_id}", json={"cfi": "epubcfi(/6/2)", "percent": 0.0})
        assert _read_status(env, book_id) == env.ub.ReadBook.STATUS_FINISHED
        client.post(f"/ajax/progress/{book_id}", json={"cfi": "epubcfi(/6/4)", "percent": 0.02})
        assert _read_status(env, book_id) == env.ub.ReadBook.STATUS_IN_PROGRESS
        # The re-read's time starts over: the first read's doesn't finish it again
        client.post(f"/ajax/progress/{book_id}", json={"cfi": "epubcfi(/6/99)", "percent": 1.0, "seconds": 60})
        assert _read_status(env, book_id) == env.ub.ReadBook.STATUS_IN_PROGRESS

    def test_skimming_to_the_end_does_not_finish_a_book(self, env):
        book_id = env.add_book("Progress Book")
        client = _login(env)
        client.post(f"/ajax/progress/{book_id}", json={"cfi": "epubcfi(/6/4)", "percent": 0.3, "seconds": 40})
        client.post(f"/ajax/progress/{book_id}", json={"cfi": "epubcfi(/6/99)", "percent": 1.0, "seconds": 40})
        assert _read_status(env, book_id) == env.ub.ReadBook.STATUS_IN_PROGRESS
        # An old reader that sends no reading time can't finish one either
        client.post(f"/ajax/progress/{book_id}", json={"cfi": "epubcfi(/6/99)", "percent": 1.0})
        assert _read_status(env, book_id) == env.ub.ReadBook.STATUS_IN_PROGRESS

    def test_reading_time_adds_up_across_posts_until_the_end_finishes_it(self, env):
        book_id = env.add_book("Progress Book")
        client = _login(env)
        for percent in (0.3, 0.6, 0.9):
            client.post(f"/ajax/progress/{book_id}", json={"cfi": "epubcfi(/6/4)", "percent": percent, "seconds": 100})
        assert _read_status(env, book_id) == env.ub.ReadBook.STATUS_IN_PROGRESS
        client.post(f"/ajax/progress/{book_id}", json={"cfi": "epubcfi(/6/99)", "percent": 1.0, "seconds": 0})
        assert _read_status(env, book_id) == env.ub.ReadBook.STATUS_FINISHED

    def test_a_longer_book_needs_longer(self, env):
        book_id = env.add_book("Progress Book")
        client = _login(env)
        # An epub of 100 pages: 15 seconds a page
        client.post(f"/ajax/progress/{book_id}",
                    json={"cfi": "epubcfi(/6/99)", "percent": 1.0, "seconds": 1400, "pages": 100})
        assert _read_status(env, book_id) == env.ub.ReadBook.STATUS_IN_PROGRESS
        client.post(f"/ajax/progress/{book_id}",
                    json={"cfi": "epubcfi(/6/99)", "percent": 1.0, "seconds": 100, "pages": 100})
        assert _read_status(env, book_id) == env.ub.ReadBook.STATUS_FINISHED

    @pytest.mark.parametrize("payload", [None, [], {"cfi": "x"}, {"percent": 0.5}, {"cfi": "", "percent": 0.5},
                                         {"cfi": 5, "percent": 0.5}, {"cfi": "x", "percent": 1.5},
                                         {"cfi": "x", "percent": -0.1}, {"cfi": "x", "percent": "0.5"},
                                         {"cfi": "x", "percent": True}, {"cfi": "x" * 5000, "percent": 0.5}])
    def test_invalid_payloads(self, env, payload):
        book_id = env.add_book("Progress Book")
        client = _login(env)
        if payload is None:
            resp = client.post(f"/ajax/progress/{book_id}", data="not json", content_type="application/json")
        else:
            resp = client.post(f"/ajax/progress/{book_id}", json=payload)
        assert resp.status_code == 400
        assert _read_status(env, book_id) is None

    def test_requires_login(self, env):
        book_id = env.add_book("Progress Book")
        client = env.app.test_client()
        assert client.get(f"/ajax/progress/{book_id}").status_code in (302, 401)
        assert client.post(f"/ajax/progress/{book_id}", json={"cfi": "x", "percent": 0.5}).status_code in (302, 401)

    def test_restricted_book_is_not_found(self, env):
        hidden = env.add_book("Hidden Book", tags=("Secret",))
        client = _restricted_user(env)
        assert client.get(f"/ajax/progress/{hidden}").status_code == 404
        assert client.post(f"/ajax/progress/{hidden}", json={"cfi": "x", "percent": 0.5}).status_code == 404
        assert client.get("/ajax/progress/999999").status_code == 404

    def test_post_requires_csrf_header(self, env):
        from flask_wtf.csrf import CSRFProtect, generate_csrf
        CSRFProtect(env.app)
        env.app.add_url_rule("/_test_csrf", "test_csrf", generate_csrf)
        book_id = env.add_book("Progress Book")
        client = _login(env)  # CSRF is still disabled (lily_env) while logging in
        env.app.config["WTF_CSRF_ENABLED"] = True
        payload = {"cfi": "epubcfi(/6/4)", "percent": 0.3}
        assert client.post(f"/ajax/progress/{book_id}", json=payload).status_code == 400
        token = client.get("/_test_csrf").get_data(as_text=True)
        resp = client.post(f"/ajax/progress/{book_id}", json=payload, headers={"X-CSRFToken": token})
        assert resp.status_code == 200


@pytest.mark.unit
class TestReadingTimeToFinish:
    def test_pages_from_the_position_or_the_reader(self):
        from cps.web import _seconds_to_finish
        assert _seconds_to_finish("page:300", 1.0) == 300 * 15
        assert _seconds_to_finish("page:150", 0.5) == 300 * 15
        assert _seconds_to_finish("page:4", 1.0) == 5 * 60  # a short paper still takes five minutes
        assert _seconds_to_finish("epubcfi(/6/2)", 1.0, pages=200) == 200 * 15
        assert _seconds_to_finish("epubcfi(/6/2)", 1.0) == 5 * 60

    def test_an_audiobook_needs_half_its_length(self):
        from cps.web import _seconds_to_finish
        assert _seconds_to_finish("time:3600", 1.0) == 1800

    @pytest.mark.parametrize("data, expected", [
        ({}, (0, None)), ({"seconds": -5}, (0, None)), ({"seconds": True}, (0, None)),
        ({"seconds": 99999}, (3600, None)), ({"seconds": 12.7, "pages": 40}, (12, 40)),
        ({"pages": 0}, (0, None)), ({"pages": "40"}, (0, None)),
    ])
    def test_reading_time_from_a_post(self, data, expected):
        from cps.web import _reading_time
        assert _reading_time(data) == expected
