# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Backend hardening: forced default-password change, content restrictions on reading /
sending, stats SQL parameter binding, missing User-Agent headers and web reader progress."""

import re
import sqlite3
from datetime import datetime, timedelta, timezone
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
        # OPDS uses HTTP basic auth and must keep working for e-readers
        import base64
        creds = base64.b64encode(f"{forced.admin().name}:{ADMIN_PASSWORD}".encode()).decode()
        resp = forced.app.test_client().get("/opds", headers={"Authorization": f"Basic {creds}"})
        assert resp.status_code == 200

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
                                              "new_password": constants.DEFAULT_PASSWORD,
                                              "confirm_password": constants.DEFAULT_PASSWORD})
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


# --------------------------------------------------------------------------- 4. stats SQL
def _stats_queries(tmp_path):
    from scripts.cwa_stats_queries import CWAStatsQueries

    class Recorder:
        def __init__(self, cur):
            self.cur, self.sql = cur, []

        def execute(self, sql, params=()):
            self.sql.append(sql)
            return self.cur.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self.cur, name)

    class Queries(CWAStatsQueries):
        def _build_user_filter(self, user_id):
            return f" AND user_id = {int(user_id)}" if user_id is not None else ""

        def _has_user_filter(self, user_id):
            return user_id is not None

    con = sqlite3.connect(tmp_path / "cwa.db")
    con.execute("CREATE TABLE cwa_user_activity (id INTEGER PRIMARY KEY, user_id INTEGER, user_name TEXT, "
                "event_type TEXT, item_id INTEGER, item_title TEXT, extra_data TEXT, timestamp DATETIME)")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    old = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
    rows = [(1, "a", "READ", 1, "Book", '{"device_type": "desktop"}', now),
            (1, "a", "DOWNLOAD", 2, "Book 2", '{"device_type": "mobile"}', now),
            (2, "b", "READ", 3, "Book 3", '{"device_type": "desktop"}', old)]
    con.executemany("INSERT INTO cwa_user_activity (user_id, user_name, event_type, item_id, item_title, "
                    "extra_data, timestamp) VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    q = Queries()
    q.cur = Recorder(con.cursor())
    return q


@pytest.mark.unit
class TestStatsQueryBinding:
    INJECTION = "2020-01-01') OR 1=1 --"

    def test_valid_stats_date(self):
        from scripts.cwa_stats_queries import valid_stats_date
        assert valid_stats_date("2026-03-04") == "2026-03-04"
        for bad in (self.INJECTION, "2026-13-01", "", None, "yesterday"):
            with pytest.raises(ValueError):
                valid_stats_date(bad)

    def test_date_range_and_days_filters_are_bound(self, tmp_path):
        q = _stats_queries(tmp_path)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert dict(q.get_device_breakdown(start_date=today, end_date=today)) == {"desktop": 1, "mobile": 1}
        assert dict(q.get_device_breakdown(days=365)) == {"desktop": 2, "mobile": 1}
        assert dict(q.get_device_breakdown(days=30, user_id=2)) == {}
        assert not any(today in sql for sql in q.cur.sql)

    def test_injected_dates_never_reach_the_sql(self, tmp_path, capsys):
        q = _stats_queries(tmp_path)
        assert q.get_device_breakdown(start_date=self.INJECTION, end_date="2030-01-01") == []
        assert q.get_discovery_sources(start_date="2020-01-01", end_date=self.INJECTION) == []
        assert q.get_device_breakdown(days="30; DROP TABLE cwa_user_activity") == []
        assert not any("OR 1=1" in sql or "DROP" in sql for sql in q.cur.sql)

    def test_every_user_activity_query_runs(self, tmp_path, capsys):
        q = _stats_queries(tmp_path)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for name in ("get_discovery_sources", "get_device_breakdown", "get_session_duration_stats",
                     "get_search_success_rate", "get_shelf_activity_stats", "get_api_usage_breakdown",
                     "get_endpoint_frequency_grouped", "get_api_timing_heatmap", "get_hourly_activity_heatmap",
                     "get_reading_velocity", "get_format_preferences", "get_dashboard_stats"):
            getattr(q, name)(days=30)
            getattr(q, name)(start_date=today, end_date=today, user_id=1)
        q.get_failed_logins(days=7)
        q.get_failed_logins(start_date=today, end_date=today)
        assert "[cwa-db] Error" not in capsys.readouterr().out

    def test_no_date_values_are_formatted_into_sql(self):
        src = (REPO / "scripts/cwa_stats_queries.py").read_text()
        assert not re.search(r"\{(start_date|end_date|prev_start|prev_end|days|limit)\b", src)

    def test_csv_export_validates_dates(self):
        from cps.cwa_functions.stats import parse_stats_date_range
        assert parse_stats_date_range("2026-01-01", "2026-02-01") == ("2026-01-01", "2026-02-01")
        assert parse_stats_date_range(self.INJECTION, "2026-02-01") == (None, None)
        assert parse_stats_date_range("2026-01-01", None) == (None, None)


# --------------------------------------------------------------------------- 7. missing User-Agent
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


# --------------------------------------------------------------------------- 8. web reader progress
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
        assert resp.get_json() == {"cfi": None, "percent": None, "updated": None}

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
        client.post(f"/ajax/progress/{book_id}", json={"cfi": "epubcfi(/6/99)", "percent": 0.995})
        assert _read_status(env, book_id) == env.ub.ReadBook.STATUS_FINISHED
        # scrolling back after finishing keeps the book finished
        client.post(f"/ajax/progress/{book_id}", json={"cfi": "epubcfi(/6/2)", "percent": 0.1})
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
class TestContinueReadingWebSource:
    @pytest.fixture
    def session(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from cps import ub
        engine = create_engine("sqlite:///:memory:")
        ub.Base.metadata.create_all(engine)
        sess = sessionmaker(bind=engine)()
        yield sess
        sess.close()

    BASE = datetime(2026, 1, 1)

    def _reading(self, session, book_id, minutes, web=None):
        from cps import ub
        rb = ub.ReadBook(user_id=1, book_id=book_id, read_status=ub.ReadBook.STATUS_IN_PROGRESS)
        session.add(rb)
        if web is not None:
            session.add(ub.WebReaderProgress(user_id=1, book_id=book_id, cfi="x", percent=web[0]))
        session.commit()
        at = self.BASE + timedelta(minutes=minutes)
        session.query(ub.ReadBook).filter_by(book_id=book_id).update({ub.ReadBook.last_modified: at})
        if web is not None:
            session.query(ub.WebReaderProgress).filter_by(book_id=book_id).update(
                {ub.WebReaderProgress.last_modified: self.BASE + timedelta(minutes=web[1])})
        session.commit()

    def test_web_progress_orders_the_row(self, session):
        from cps.web import get_continue_reading_progress
        self._reading(session, 10, 1, web=(0.6, 5))
        self._reading(session, 11, 8)                  # no progress, but touched most recently
        self._reading(session, 12, 1, web=(0.1, 20))   # web position is the newest activity
        self._reading(session, 13, 4)
        result = get_continue_reading_progress(session, 1)
        assert [book_id for book_id, __ in result] == [12, 11, 10, 13]
        progress = dict(result)
        assert progress[10] == pytest.approx(60.0)
        assert progress[11] is None
        assert progress[12] == pytest.approx(10.0)
        assert progress[13] is None
