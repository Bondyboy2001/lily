# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for security hardening: shell injection, XSS, CSRF, proxy trust
and forced default-password change."""

import ast
import re
from pathlib import Path

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
@pytest.mark.parametrize("template", ["cwa_stats_full.html", "cwa_stats_system.html"])
def test_stats_and_log_templates_do_not_mark_content_safe(template):
    text = (REPO / "cps/templates" / template).read_text()
    assert not re.search(r"\|\s*safe\b", text)


@pytest.mark.unit
def test_log_content_is_escaped_when_rendered():
    from jinja2 import Environment
    env = Environment(autoescape=True)
    src = (REPO / "cps/templates/cwa_stats_full.html").read_text()
    row_tpl = re.search(r"<div class=\"stats-cell\">\{\{ cell \}\}</div>", src).group(0)
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


STATE_CHANGING = {"cwa_library_refresh"}


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


# Modules whose exempt routes serve callbacks (Google Drive) that authenticate
# without a browser session.
DEVICE_PROTOCOL_MODULES = {"cps/gdrive.py"}


@pytest.mark.unit
def test_csrf_exempt_routes_are_internal_or_device_only():
    for path in sorted((REPO / "cps").rglob("*.py")):
        rel = str(path.relative_to(REPO))
        if rel in DEVICE_PROTOCOL_MODULES:
            continue
        for name, names, _ in _route_decorators(rel):
            if "exempt" in names:
                assert "internal_only" in names, f"{rel}:{name} is csrf.exempt without @internal_only"


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


# --------------------------------------------------------------------------- default admin
@pytest.fixture
def app_session(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from cps import ub

    engine = create_engine("sqlite://")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.mark.unit
class TestDefaultAdmin:
    def test_default_admin_is_harry(self, app_session):
        from werkzeug.security import check_password_hash
        from cps import ub, constants
        ub.create_admin_user(app_session)
        admin = app_session.query(ub.User).filter(ub.User.name == constants.DEFAULT_ADMIN_NAME).one()
        assert admin.name == "harry"
        assert check_password_hash(admin.password, "harry10")
