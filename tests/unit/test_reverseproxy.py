import pytest

from cps.reverseproxy import ReverseProxied


class _StubApp:
    def __init__(self):
        self.seen = None

    def __call__(self, environ, start_response):
        self.seen = dict(environ)
        return iter([b"ok"])


def _run(environ):
    app = _StubApp()
    proxied = ReverseProxied(app)
    result = b"".join(proxied(dict(environ), lambda *a, **k: None))
    assert result == b"ok"
    return app.seen


@pytest.mark.unit
class TestReverseProxied:
    def test_script_name_prefix_strips_path_info(self):
        seen = _run({"HTTP_X_SCRIPT_NAME": "/books",
                     "PATH_INFO": "/books/admin/view",
                     "HTTP_HOST": "internal:8083"})
        assert seen["SCRIPT_NAME"] == "/books"
        assert seen["PATH_INFO"] == "/admin/view"
        assert seen["HTTP_HOST"] == "internal:8083"

    def test_unrelated_path_prefix_left_unchanged(self):
        seen = _run({"HTTP_X_SCRIPT_NAME": "/books", "PATH_INFO": "/other/page"})
        assert seen["SCRIPT_NAME"] == "/books"
        assert seen["PATH_INFO"] == "/other/page"

    def test_x_scheme_takes_precedence_over_forwarded_proto(self):
        seen = _run({"HTTP_X_SCHEME": "https", "HTTP_X_FORWARDED_PROTO": "http"})
        assert seen["wsgi.url_scheme"] == "https"

    def test_forwarded_proto_used_when_x_scheme_absent(self):
        seen = _run({"HTTP_X_FORWARDED_PROTO": "https"})
        assert seen["wsgi.url_scheme"] == "https"

    def test_forwarded_host_rewrites_host(self):
        seen = _run({"HTTP_X_FORWARDED_HOST": "books.example.org",
                     "HTTP_HOST": "internal:8083"})
        assert seen["HTTP_HOST"] == "books.example.org"

    def test_no_proxy_headers_leave_environ_unchanged(self):
        original = {"PATH_INFO": "/admin/view", "HTTP_HOST": "internal:8083",
                    "wsgi.url_scheme": "http"}
        seen = _run(original)
        assert seen["PATH_INFO"] == "/admin/view"
        assert seen["HTTP_HOST"] == "internal:8083"
        assert seen["wsgi.url_scheme"] == "http"
        assert "SCRIPT_NAME" not in seen
