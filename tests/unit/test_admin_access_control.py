"""A regular user and a signed-out visitor must not reach admin-only routes, every route is
either public on purpose (PUBLIC_ENDPOINTS) or refuses visitors, and the deleted settings
pages have no route."""

import pytest

from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit

ADMIN_GETS = ["/admin/user/new", "/admin/usertable", "/logs"]
ADMIN_POSTS = ["/admin/user/new", "/ajax/deleteuser", "/shutdown", "/metadata_backup"]
# Settings pages that were deleted outright: not hidden, not redirected, gone.
REMOVED = ["/admin/view", "/admin/config", "/admin/dbconfig", "/admin/viewconfig", "/admin/scheduledtasks",
           "/admin/db_backups", "/admin/ingest_failures", "/admin/book-recovery", "/admin/metadata/suggestions",
           "/admin/hardcover/review-matches", "/cwa-stats-show", "/stats", "/tasks", "/account/security",
           "/reading", "/ajax/pathchooser/"]


@pytest.fixture
def clients(tmp_path):
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(env.app)
        env.add_user("plain", password="pw")
        user = env.app.test_client()
        user.post("/login", data={"username": "plain", "password": "pw"})
        admin = env.app.test_client()
        admin.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
        yield env.app.test_client(), user, admin


def _blocked(resp, path):
    """Refused outright, or bounced somewhere other than the page asked for."""
    if resp.status_code in (401, 403):
        return True
    return resp.status_code == 302 and path not in resp.headers["Location"]


@pytest.mark.parametrize("path", ADMIN_GETS)
def test_admin_pages_are_closed_to_users_and_visitors(clients, path):
    visitor, user, admin = clients
    assert _blocked(visitor.get(path), path), "visitor reached " + path
    assert _blocked(user.get(path), path), "regular user reached " + path
    assert admin.get(path).status_code == 200


@pytest.mark.parametrize("path", ADMIN_POSTS)
def test_admin_actions_are_closed_to_users_and_visitors(clients, path):
    visitor, user, _ = clients
    for c in (visitor, user):
        resp = c.post(path, data={"names": "x", "snapshot": "20260101_030000"})
        assert _blocked(resp, path), "%s reached %s" % (path, resp.status_code)


@pytest.mark.parametrize("path", REMOVED)
def test_removed_settings_pages_have_no_route(clients, path):
    """Nothing serves these paths any more. Two-segment ones such as /admin/view fall through
    to the generic /<list>/<sort> book list, which is not the old page."""
    from flask import current_app
    from werkzeug.exceptions import NotFound
    from werkzeug.routing import RequestRedirect
    visitor, _, _ = clients
    with visitor.application.app_context():
        adapter = current_app.url_map.bind("localhost")
        try:
            endpoint, _args = adapter.match(path, method="GET")
        except NotFound:
            return
        except RequestRedirect as redirect:
            endpoint, _args = adapter.match(redirect.new_url.split("localhost", 1)[1], method="GET")
        assert endpoint == "web.books_list", (path, endpoint)


# ---------------------------------------------------------------- every route

# Reachable without signing in, and why.
PUBLIC_ENDPOINTS = {
    "web.login", "web.login_post",                 # the sign-in page
    "web.health_check",                            # container health check
    "web.reader_service_worker",                   # static service-worker script; caches only signed-in responses
    "gdrive.on_received_watch_confirmation",       # Google's push callback; checks its own channel token
    "admin.reconnect",                             # 404 unless started with -r (calibre-web's reconnect hook)
}


def _url(rule):
    args = {name: 1 if type(conv).__name__ in ("IntegerConverter", "FloatConverter") else "x"
            for name, conv in rule._converters.items()}
    return rule.build(args, append_unknown=False)[1]


def _routes(app):
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: (r.rule, r.endpoint)):
        if rule.endpoint == "static" or rule.endpoint.endswith(".static"):
            continue
        yield rule, ("GET" if "GET" in rule.methods else "POST"), _url(rule)


def _refused(client, method, url):
    resp = client.open(url, method=method)
    if resp.status_code == 308:  # canonical-slash redirect: judge where it leads
        resp = client.open(resp.headers["Location"], method=method)
    if resp.status_code in (401, 403):
        return True
    return resp.status_code == 302 and "/login" in resp.headers.get("Location", "")


def test_every_route_is_public_by_choice_or_refuses_visitors(clients):
    visitor, _, admin = clients
    app = admin.application
    checked, open_routes = 0, []
    for rule, method, url in _routes(app):
        checked += 1
        if rule.endpoint in PUBLIC_ENDPOINTS:
            continue
        if not _refused(visitor, method, url):
            open_routes.append("%s %s (%s)" % (method, url, rule.endpoint))
    assert checked > 150  # the walk really covered the app
    assert not open_routes, "reachable without signing in:\n" + "\n".join(open_routes)


def _admin_endpoints(app):
    """Endpoints whose view is wrapped by @admin_required (found through functools.wraps)."""
    from cps.admin import admin_required
    gate = admin_required(lambda: None).__code__
    found = set()
    for endpoint, view in app.view_functions.items():
        fn = view
        while fn is not None:
            if getattr(fn, "__code__", None) is gate:
                found.add(endpoint)
                break
            fn = getattr(fn, "__wrapped__", None)
    return found


def test_admin_routes_refuse_regular_users(clients):
    _, user, admin = clients
    app = admin.application
    admin_endpoints = _admin_endpoints(app)
    assert len(admin_endpoints) > 20
    reached_by_user = [rule.endpoint for rule, method, url in _routes(app)
                       if rule.endpoint in admin_endpoints and not _refused(user, method, url)]
    assert not reached_by_user, reached_by_user
