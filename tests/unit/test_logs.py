"""The admin Logs page: access control, source allowlisting, bounded tails and safe rendering."""

import os

import pytest

from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


@pytest.fixture
def clients(tmp_path):
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        env.add_user("plain", password="pw")
        user = env.app.test_client()
        user.post("/login", data={"username": "plain", "password": "pw"})
        admin = env.app.test_client()
        admin.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
        yield env, env.app.test_client(), user, admin


def _fake_source(tmp_path, monkeypatch, name="fake.log", body="INFO line\nERROR boom\n"):
    log_file = tmp_path / name
    log_file.write_text(body, encoding="utf-8")
    from cps import logs
    source = {"id": "src-test", "label": name, "path": str(log_file)}
    monkeypatch.setattr(logs, "_discover_sources", lambda: [source])
    return source


class TestAccess:
    def test_page_and_json_need_admin(self, clients):
        _, visitor, user, admin = clients
        for path in ("/logs", "/logs/data"):
            assert visitor.get(path).status_code in (302, 401)
            assert user.get(path).status_code == 403
            assert admin.get(path).status_code == 200

    def test_no_store_on_both(self, clients):
        _, _, _, admin = clients
        for path in ("/logs", "/logs/data"):
            assert admin.get(path).headers["Cache-Control"] == "no-store"


class TestDataEndpoint:
    def test_returns_all_sources_with_a_version(self, clients, tmp_path, monkeypatch):
        _, _, _, admin = clients
        _fake_source(tmp_path, monkeypatch)
        payload = admin.get("/logs/data").get_json()
        assert payload["success"] and "ERROR boom" in payload["text"]
        assert payload["version"] and "sources" not in payload

    def test_unchanged_since_skips_the_read(self, clients, tmp_path, monkeypatch):
        from cps import logs
        _, _, _, admin = clients
        source = _fake_source(tmp_path, monkeypatch)
        version = admin.get("/logs/data").get_json()["version"]
        real_read = logs._read_sources

        def no_read(*_args, **_kwargs):
            raise AssertionError("an unchanged poll must not read the logs")
        monkeypatch.setattr(logs, "_read_sources", no_read)
        payload = admin.get("/logs/data?since=" + version).get_json()
        assert payload == {"success": True, "version": version, "unchanged": True}
        monkeypatch.setattr(logs, "_read_sources", real_read)
        with open(source["path"], "a", encoding="utf-8") as fh:
            fh.write("INFO new line\n")
        payload = admin.get("/logs/data?since=" + version).get_json()
        assert payload["version"] != version and payload["text"].endswith("INFO new line\n")

    def test_stale_or_bogus_since_returns_text(self, clients, tmp_path, monkeypatch):
        _, _, _, admin = clients
        _fake_source(tmp_path, monkeypatch)
        payload = admin.get("/logs/data?since=../../etc/passwd").get_json()
        assert "ERROR boom" in payload["text"] and "unchanged" not in payload

    def test_service_dir_reads_oldest_first(self, clients, tmp_path, monkeypatch):
        from cps import logs
        _, _, _, admin = clients
        log_dir = tmp_path / "uncaught"
        log_dir.mkdir()
        (log_dir / "current").write_text("newest line\n", encoding="utf-8")
        (log_dir / "@4000000068a941db1c4a9e04.s").write_text("old archived line\n", encoding="utf-8")
        monkeypatch.setattr(logs, "S6_UNCAUGHT_LOG_DIR", str(log_dir))
        monkeypatch.setattr(logs, "RETAINED_LOG_DIR", str(tmp_path / "none"))
        monkeypatch.setattr(logs, "_configured_log_files", lambda: [])
        text = admin.get("/logs/data").get_json()["text"]
        assert text.index("old archived line") < text.index("newest line")
        assert text.rstrip().endswith("newest line")

    def test_retained_service_dirs_get_readable_names(self, clients, tmp_path, monkeypatch):
        from cps import logs
        _, _, _, admin = clients
        for service in ("cwa-auto-library", "cwa-ingest-service", "svc-calibre-web-automated"):
            sub = tmp_path / "retained" / service
            sub.mkdir(parents=True)
            (sub / "current").write_text(service + " now\n", encoding="utf-8")
            (sub / "@4000000068a941db1c4a9e04.u").write_text(service + " before\n", encoding="utf-8")
        monkeypatch.setattr(logs, "S6_UNCAUGHT_LOG_DIR", str(tmp_path / "none"))
        monkeypatch.setattr(logs, "RETAINED_LOG_DIR", str(tmp_path / "retained"))
        monkeypatch.setattr(logs, "_configured_log_files", lambda: [])
        text = admin.get("/logs/data").get_json()["text"]
        for label in ("Auto library (current)", "Ingest service (current)", "Lily web app (current)"):
            assert "===== %s =====" % label in text
        assert "cwa-auto-library" + " (current)" not in text
        assert text.index("cwa-auto-library before") < text.index("cwa-auto-library now")

    def test_source_param_is_ignored(self, clients, tmp_path, monkeypatch):
        _, _, _, admin = clients
        _fake_source(tmp_path, monkeypatch)
        for query in ("?source=nope", "?source=../../etc/passwd", "?source=/etc/passwd"):
            payload = admin.get("/logs/data" + query).get_json()
            assert payload["success"] and "ERROR boom" in payload["text"]

    def test_missing_file_is_skipped(self, clients, tmp_path, monkeypatch):
        from cps import logs
        _, _, _, admin = clients
        gone = {"id": "src-gone", "label": "gone.log", "path": str(tmp_path / "gone.log")}
        monkeypatch.setattr(logs, "_discover_sources", lambda: [gone])
        payload = admin.get("/logs/data").get_json()
        assert payload["success"] and payload["text"] == ""


class TestHelpers:
    def test_s6_dir_lists_rotations_then_current(self, tmp_path):
        from cps import logs
        for name in ("current", "@4000000068a941db1c4a9e04.s", "@4000000068a9410000000001.u",
                     "lock", "state"):
            (tmp_path / name).write_text("x", encoding="utf-8")
        entries = logs._s6_uncaught_files(str(tmp_path))
        names = [os.path.basename(p) for p, _ in entries]
        assert names == ["@4000000068a9410000000001.u", "@4000000068a941db1c4a9e04.s", "current"]

    def test_s6_dir_missing(self, tmp_path):
        from cps import logs
        assert logs._s6_uncaught_files(str(tmp_path / "nope")) == []

    def test_configured_log_files_include_rotations(self, tmp_path):
        import logging
        from logging.handlers import RotatingFileHandler
        from cps import logs
        base = tmp_path / "unit-app.log"
        base.write_text("live\n", encoding="utf-8")
        (tmp_path / "unit-app.log.1").write_text("rotated\n", encoding="utf-8")
        (tmp_path / "unit-app.log.2").write_text("older\n", encoding="utf-8")
        handler = RotatingFileHandler(str(base), maxBytes=1024, backupCount=2)
        test_logger = logging.getLogger("lily-test-logs-unit")
        test_logger.addHandler(handler)
        try:
            entries = logs._configured_log_files()
        finally:
            test_logger.removeHandler(handler)
            handler.close()
        paths = [p for p, _ in entries if p.startswith(str(tmp_path))]
        assert paths == [str(tmp_path / "unit-app.log.2"), str(tmp_path / "unit-app.log.1"), str(base)]

    def test_tail_truncates_and_drops_partial_line(self, tmp_path):
        from cps import logs
        f = tmp_path / "app.log"
        f.write_bytes(("first line\n" + "x" * 200 + "\nlast line\n").encode())
        text, cut, read = logs._tail_file(str(f), 50)
        assert cut and read == 50 and "first line" not in text and "last line" in text

    def test_utf8_errors_are_replaced(self, tmp_path):
        from cps import logs
        f = tmp_path / "bad.log"
        f.write_bytes(b"ok \xff\xfe done\n")
        text, cut, read = logs._tail_file(str(f), 1024)
        assert not cut and read == len(b"ok \xff\xfe done\n") and "done" in text

    def test_budget_spans_files(self, tmp_path):
        from cps import logs
        a = tmp_path / "a.log"
        b = tmp_path / "b.log"
        a.write_text("A" * 20 + "\n", encoding="utf-8")
        b.write_text(("B" * 20 + "\n") * 100, encoding="utf-8")
        sources = [{"id": "1", "label": "b", "path": str(b)},
                   {"id": "2", "label": "a", "path": str(a)}]
        text, truncated = logs._read_sources(sources, max_bytes=100)
        assert "A" * 20 in text and truncated
        # The newest (last) source fits whole; the older one fills what is left, and comes first.
        assert text.index("===== b =====") < text.index("===== a =====")

    def test_raw_bytes_bound_covers_files_without_newlines(self, tmp_path):
        from cps import logs
        files = []
        for i in range(3):
            f = tmp_path / ("big%d.log" % i)
            f.write_text("head%d\n" % i + "x" * (800 * 1024) + "\ntail%d\n" % i, encoding="utf-8")
            files.append({"id": str(i), "label": "big%d" % i, "path": str(f)})
        text, truncated = logs._read_sources(files, max_bytes=1024 * 1024)
        assert truncated
        # Spent from the newest (last) file backwards: big2 and big1 fit, big0 is dropped.
        assert "tail2" in text and "tail1" in text
        assert "tail0" not in text and "===== big0 =====" not in text

    def test_symlink_escaping_root_is_rejected(self, tmp_path):
        from cps import logs
        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("secret", encoding="utf-8")
        link = tmp_path / "link.log"
        link.symlink_to(secret)
        assert logs._resolve_file(str(link)) is None
        with pytest.raises(OSError):
            logs._tail_file(str(link), 1024)

    def test_nonregular_file_is_rejected(self, tmp_path):
        from cps import logs
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        assert logs._resolve_file(str(fifo)) is None
        result = logs._read_sources([{"id": "f", "label": "fifo", "path": str(fifo)}])
        assert result == ("", False)

    def test_missing_file_resolves_none(self, tmp_path):
        from cps import logs
        assert logs._resolve_file(str(tmp_path / "gone.log")) is None


class TestRendering:
    def test_script_payload_stays_text(self, clients, tmp_path, monkeypatch):
        env, _, _, admin = clients
        _fake_source(tmp_path, monkeypatch, body="<script>alert(1)</script>\n")
        html = admin.get("/logs").get_data(as_text=True)
        assert "<script>alert(1)</script>" not in html
        assert 'id="log_output"' in html and "js/logs.js" in html
        assert "data-logs-url" in html and "data-empty-message" in html
