# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for security hardening: shell injection, XSS, CSRF, proxy trust,
KOSync brute force, magic shelf input validation and forced default-password change."""

import ast
import importlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- shell injection
@pytest.mark.unit
def test_no_os_system_or_shell_true_in_python_code():
    offenders = []
    for base in ("scripts", "cps"):
        for path in (REPO / base).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in ("system", "popen") \
                        and getattr(func.value, "id", "") == "os":
                    offenders.append(f"{path}:{node.lineno}")
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        offenders.append(f"{path}:{node.lineno}")
    assert not offenders, offenders


@pytest.mark.unit
@pytest.mark.parametrize("service", ["metadata-change-detector", "cwa-auto-zipper"])
def test_python_services_drop_root(service):
    run = (REPO / "root/etc/s6-overlay/s6-rc.d" / service / "run").read_text()
    for line in run.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "python3 /app" not in stripped:
            continue
        assert "s6-setuidgid abc python3" in stripped, stripped


# --------------------------------------------------------------------------- stored XSS
@pytest.mark.unit
@pytest.mark.parametrize("template", ["cwa_stats.html", "cwa_stats_full.html",
                                      "cwa_stats_system.html", "cwa_read_log.html"])
def test_stats_and_log_templates_do_not_mark_content_safe(template):
    text = (REPO / "cps/templates" / template).read_text()
    assert not re.search(r"\|\s*safe\b", text)


@pytest.mark.unit
def test_log_content_is_escaped_when_rendered():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(autoescape=True)
    src = (REPO / "cps/templates/cwa_stats_full.html").read_text()
    row_tpl = re.search(r"<div style=\"max-height[^>]*>\{\{ cell \}\}</div>", src).group(0)
    out = env.from_string(row_tpl).render(cell="<script>alert(1)</script>\nnext")
    assert "<script>" not in out and "&lt;script&gt;" in out


# --------------------------------------------------------------------------- CSRF / GET state changes
def _route_decorators(relpath):
    tree = ast.parse((REPO / relpath).read_text())
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        methods, names = None, []
        for d in node.decorator_list:
            target = d.func if isinstance(d, ast.Call) else d
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
            names.append(name)
            if name == "route" and isinstance(d, ast.Call):
                for kw in d.keywords:
                    if kw.arg == "methods":
                        methods = {e.value for e in kw.value.elts}
        if "route" in names:
            yield node.name, names, methods


STATE_CHANGING = {"cwa_library_refresh", "schedule_convert_library", "start_conversion",
                  "cancel_convert_library", "schedule_epub_fixer", "start_epub_fixer",
                  "cancel_epub_fixer", "cwa_scheduled_cancel", "run_epub_fixer_for_book"}


@pytest.mark.unit
def test_state_changing_cwa_routes_are_post_only_and_csrf_protected():
    routes = {name: (names, methods)
              for path in sorted((REPO / "cps/cwa_functions").glob("*.py"))
              for name, names, methods in _route_decorators(str(path.relative_to(REPO)))}
    for name in STATE_CHANGING:
        names, methods = routes[name]
        assert methods == {"POST"}, name
        assert "exempt" not in names, name
    assert "exempt" not in routes["set_cwa_settings"][0]
    assert "admin_required" in routes["cwa_library_refresh"][0]


@pytest.mark.unit
@pytest.mark.parametrize("template", ["cwa_convert_library.html", "cwa_epub_fixer.html"])
def test_service_pages_post_with_csrf_token(template):
    text = (REPO / "cps/templates" / template).read_text()
    for endpoint in ("start_", "cancel_", "schedule_"):
        assert not re.search(r"href=\"\{\{ url_for\('[a-z_]+\." + endpoint, text), endpoint
    # start, cancel and the (looped) schedule form
    assert text.count('name="csrf_token"') >= 3


# --------------------------------------------------------------------------- cookies / proxy
@pytest.mark.unit
def test_remember_cookie_flags():
    import cps
    assert cps.app.config["REMEMBER_COOKIE_HTTPONLY"] is True
    assert cps.app.config["REMEMBER_COOKIE_SECURE"] == cps.app.config["SESSION_COOKIE_SECURE"]


@pytest.mark.unit
def test_proxyfix_default_is_off():
    src = (REPO / "cps/__init__.py").read_text()
    assert "os.environ.get('TRUSTED_PROXY_COUNT', '0')" in src
    assert "if num_proxies > 0:" in src


def _req(remote, orig=None, headers=None):
    environ = {"REMOTE_ADDR": remote}
    if orig:
        environ["werkzeug.proxy_fix.orig"] = {"REMOTE_ADDR": orig}
    return SimpleNamespace(environ=environ, headers=headers or {})


@pytest.mark.unit
class TestTrustedProxy:
    @pytest.mark.parametrize("addr", ["127.0.0.1", "::1", "172.18.0.4", "192.168.1.10",
                                      "10.1.2.3", "::ffff:192.168.1.2"])
    def test_default_trusts_loopback_and_private(self, monkeypatch, addr):
        from cps import usermanagement as um
        monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)
        assert um.request_from_trusted_proxy(_req(addr))

    @pytest.mark.parametrize("addr", ["203.0.113.9", "8.8.8.8", "2001:db8::1", "garbage", ""])
    def test_default_rejects_public(self, monkeypatch, addr):
        from cps import usermanagement as um
        monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)
        assert not um.request_from_trusted_proxy(_req(addr))

    def test_uses_socket_peer_not_forwarded_for(self, monkeypatch):
        from cps import usermanagement as um
        monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)
        # ProxyFix rewrote REMOTE_ADDR from a spoofed X-Forwarded-For
        assert not um.request_from_trusted_proxy(_req("127.0.0.1", orig="203.0.113.9"))

    def test_custom_list(self, monkeypatch):
        from cps import usermanagement as um
        monkeypatch.setenv("TRUSTED_PROXY_IPS", "203.0.113.0/24, bogus")
        assert um.request_from_trusted_proxy(_req("203.0.113.9"))
        assert not um.request_from_trusted_proxy(_req("127.0.0.1"))

    def test_header_ignored_from_untrusted_peer(self, monkeypatch):
        from cps import usermanagement as um
        monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)
        monkeypatch.setattr(um, "config", SimpleNamespace(config_reverse_proxy_login_header_name="X-User"))

        class Boom:
            def query(self, *a, **k):
                raise AssertionError("user lookup must not happen for untrusted peers")
        monkeypatch.setattr(um.ub, "session", Boom())
        assert um.load_user_from_reverse_proxy_header(_req("203.0.113.9", headers={"X-User": "admin"})) is None


# --------------------------------------------------------------------------- KOSync brute force
@pytest.mark.unit
def test_kosync_limits_failed_attempts_only(monkeypatch):
    from limits.storage import MemoryStorage
    from limits.strategies import FixedWindowRateLimiter
    kosync = importlib.import_module("cps.progress_syncing.protocols.kosync")

    fake = SimpleNamespace(enabled=True, limiter=FixedWindowRateLimiter(MemoryStorage()))
    monkeypatch.setattr(kosync, "limiter", fake)

    assert not kosync.kosync_auth_blocked("Alice")
    for _ in range(5):
        kosync.kosync_record_auth_failure("alice")
    assert kosync.kosync_auth_blocked("ALICE")
    assert not kosync.kosync_auth_blocked("bob")


@pytest.mark.unit
def test_kosync_limiter_disabled_is_noop(monkeypatch):
    kosync = importlib.import_module("cps.progress_syncing.protocols.kosync")
    monkeypatch.setattr(kosync, "limiter", SimpleNamespace(enabled=False))
    kosync.kosync_record_auth_failure("alice")
    assert not kosync.kosync_auth_blocked("alice")


# --------------------------------------------------------------------------- magic shelf input
@pytest.mark.unit
def test_magic_shelf_icon_and_rules_validation():
    from cps import web
    assert web._sanitize_magic_shelf_icon("📚") == "📚"
    assert web._sanitize_magic_shelf_icon('<img src=x onerror=alert(1)>') == web.MAGIC_SHELF_DEFAULT_ICON
    assert web._sanitize_magic_shelf_icon(None) == web.MAGIC_SHELF_DEFAULT_ICON
    assert web._sanitize_magic_shelf_icon(["📚"]) == web.MAGIC_SHELF_DEFAULT_ICON
    assert web._magic_shelf_rules_valid({"condition": "AND", "rules": []})
    assert not web._magic_shelf_rules_valid("<script>")
    assert not web._magic_shelf_rules_valid({"x": "a" * (web.MAGIC_SHELF_RULES_MAX_BYTES + 1)})


# --------------------------------------------------------------------------- forced password change
@pytest.fixture
def app_session(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from cps import ub, magic_shelf

    monkeypatch.setattr(magic_shelf, "create_system_magic_shelves", lambda user_id: 0)
    engine = create_engine("sqlite://")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.mark.unit
class TestForcedPasswordChange:
    def test_default_admin_is_flagged_and_cleared_on_change(self, app_session):
        from werkzeug.security import generate_password_hash
        from cps import ub
        ub.create_admin_user(app_session)
        admin = app_session.query(ub.User).filter(ub.User.name == "admin").one()
        assert admin.force_password_change is True
        admin.password = generate_password_hash("something-better")
        app_session.commit()
        assert app_session.query(ub.User).filter(ub.User.name == "admin").one().force_password_change is False

    def test_existing_default_password_is_flagged_on_migration(self, app_session):
        from werkzeug.security import generate_password_hash
        from cps import ub, constants
        default = ub.User(name="admin", email="a@x", role=constants.ADMIN_USER_ROLES,
                          password=generate_password_hash(constants.DEFAULT_PASSWORD))
        changed = ub.User(name="boss", email="b@x", role=constants.ADMIN_USER_ROLES,
                          password=generate_password_hash("s3cret-pass"))
        app_session.add_all([default, changed])
        app_session.commit()
        ub.flag_users_with_default_password(app_session)
        assert default.force_password_change is True
        assert not changed.force_password_change

    @pytest.fixture
    def hook_app(self, monkeypatch):
        from flask import Flask, Blueprint
        from cps import web as web_module

        app = Flask(__name__)
        app.secret_key = "test"
        # Minimal stand-ins for the relevant endpoints (registering the real blueprint
        # would pull in every view's dependencies).
        webbp = Blueprint("web", __name__)
        for rule, name in (("/", "index"), ("/logout", "logout"), ("/change-password", "change_password")):
            webbp.add_url_rule(rule, name, lambda: "ok", methods=["GET", "POST"])
        opds = Blueprint("opds", __name__)
        opds.add_url_rule("/opds", "feed_index", lambda: "feed")
        kosync = Blueprint("kosync", __name__)
        kosync.add_url_rule("/kosync/users/auth", "auth_user", lambda: "ok")
        for bp in (webbp, opds, kosync):
            app.register_blueprint(bp)
        app.before_request(web_module.enforce_forced_password_change)

        def login(flag):
            monkeypatch.setattr(web_module, "current_user",
                                SimpleNamespace(is_authenticated=True, force_password_change=flag))
        return app.test_client(), login

    def test_flagged_user_is_redirected(self, hook_app):
        client, login = hook_app
        login(True)
        resp = client.get("/")
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/change-password")
        assert client.post("/").status_code == 403

    def test_exempt_endpoints(self, hook_app):
        client, login = hook_app
        login(True)
        for path in ("/change-password", "/logout", "/opds", "/kosync/users/auth"):
            assert client.get(path).status_code == 200, path

    def test_unflagged_user_passes(self, hook_app):
        client, login = hook_app
        login(False)
        assert client.get("/").status_code == 200
