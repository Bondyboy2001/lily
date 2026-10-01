"""Forwarded host, scheme and prefix headers count only behind a trusted proxy."""

import pytest
from flask import Flask, request

from cps.reverseproxy import ReverseProxied

pytestmark = pytest.mark.unit

SPOOF = {"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https", "X-Scheme": "https",
         "X-Script-Name": "/prefix"}


def _app(trusted):
    app = Flask(__name__)

    @app.route("/where")
    def where():
        return "%s|%s|%s" % (request.host, request.scheme, request.script_root)

    app.wsgi_app = ReverseProxied(app.wsgi_app, trusted=trusted)
    return app


def test_headers_are_ignored_without_a_trusted_proxy():
    app = _app(trusted=False)
    resp = app.test_client().get("/where", headers=SPOOF)
    assert resp.get_data(as_text=True) == "localhost|http|"
    assert not app.wsgi_app.is_proxied


def test_headers_are_used_behind_a_trusted_proxy():
    app = _app(trusted=True)
    resp = app.test_client().get("/prefix/where", headers=SPOOF)
    assert resp.get_data(as_text=True) == "evil.example|https|/prefix"
    assert app.wsgi_app.is_proxied


def test_untrusted_is_the_default():
    assert ReverseProxied(object()).trusted is False
