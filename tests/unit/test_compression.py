import gzip

from flask import Flask, jsonify, send_file

from cps import compression

BODY = "<p>hello lily</p>" * 100


def make_client(tmp_path):
    app = Flask(__name__, static_folder=str(tmp_path), static_url_path="/static")
    (tmp_path / "a.css").write_text("body{color:red}" * 100)
    compression.init_compression(app)

    @app.route("/page")
    def page():
        return BODY

    @app.route("/tiny")
    def tiny():
        return "ok"

    @app.route("/json")
    def js():
        return jsonify(items=list(range(500)))

    @app.route("/file")
    def file():
        return send_file(tmp_path / "a.css", mimetype="text/css")

    return app.test_client()


def test_html_is_gzipped_when_accepted(tmp_path):
    r = make_client(tmp_path).get("/page", headers={"Accept-Encoding": "gzip"})
    assert r.headers["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in r.headers["Vary"]
    assert gzip.decompress(r.data).decode() == BODY
    assert int(r.headers["Content-Length"]) == len(r.data) < len(BODY)


def test_plain_when_not_accepted_or_tiny(tmp_path):
    c = make_client(tmp_path)
    assert "Content-Encoding" not in c.get("/page").headers
    assert "Content-Encoding" not in c.get("/tiny", headers={"Accept-Encoding": "gzip"}).headers


def test_json_gzipped(tmp_path):
    r = make_client(tmp_path).get("/json", headers={"Accept-Encoding": "gzip"})
    assert r.headers["Content-Encoding"] == "gzip"


def test_static_gzipped_and_cached(tmp_path):
    c = make_client(tmp_path)
    for _ in range(2):
        r = c.get("/static/a.css", headers={"Accept-Encoding": "gzip"})
        assert r.headers["Content-Encoding"] == "gzip"
        assert gzip.decompress(r.data).decode() == "body{color:red}" * 100


def test_non_static_files_left_alone(tmp_path):
    r = make_client(tmp_path).get("/file", headers={"Accept-Encoding": "gzip"})
    assert "Content-Encoding" not in r.headers
