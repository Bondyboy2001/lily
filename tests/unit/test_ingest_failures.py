"""Failed-imports page: listing, retry back into ingest, delete, and name safety."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import ingest_failures as mod  # noqa: E402


@pytest.fixture
def dirs(tmp_path):
    failed, ingest = tmp_path / "failed", tmp_path / "ingest"
    failed.mkdir()
    ingest.mkdir()
    (failed / "20260101_030000_Book.epub").write_bytes(b"abc")
    return failed, ingest


@pytest.mark.unit
def test_original_name_strips_timestamp_only():
    assert mod.original_name("20260101_030000_Book.epub") == "Book.epub"
    assert mod.original_name("Book.epub") == "Book.epub"


@pytest.mark.unit
def test_list_failed_reports_files_and_tolerates_missing_dir(dirs, tmp_path):
    failed, _ = dirs
    (failed / ".hidden").write_text("x")
    items = mod.list_failed(str(failed))
    assert [(i["name"], i["original"], i["size"]) for i in items] == [("20260101_030000_Book.epub", "Book.epub", 3)]
    assert mod.list_failed(str(tmp_path / "nope")) == []


@pytest.mark.unit
def test_retry_moves_back_under_original_name_without_overwriting(dirs):
    failed, ingest = dirs
    (ingest / "Book.epub").write_text("already here")
    dest = mod.retry_failed(str(failed), str(ingest), "20260101_030000_Book.epub")
    assert Path(dest).name == "Book_1.epub" and Path(dest).read_bytes() == b"abc"
    assert (ingest / "Book.epub").read_text() == "already here"
    assert not (failed / "20260101_030000_Book.epub").exists()


@pytest.mark.unit
def test_delete_removes_file_and_directory(dirs):
    failed, _ = dirs
    (failed / "20260101_030001_Folder").mkdir()
    (failed / "20260101_030001_Folder" / "a.txt").write_text("x")
    mod.delete_failed(str(failed), "20260101_030001_Folder")
    mod.delete_failed(str(failed), "20260101_030000_Book.epub")
    assert list(failed.iterdir()) == []


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "..", ".", ".hidden", ""])
def test_unsafe_names_are_refused(dirs, bad):
    failed, ingest = dirs
    with pytest.raises(ValueError):
        mod.delete_failed(str(failed), bad)
    with pytest.raises(ValueError):
        mod.retry_failed(str(failed), str(ingest), bad)


@pytest.fixture
def admin_client(dirs, monkeypatch, tmp_path):
    from .lily_env import lily_env, ADMIN_PASSWORD
    from .test_lily_reader_static import _register_remaining_blueprints
    failed, ingest = dirs
    monkeypatch.setattr(mod, "FAILED_DIR", str(failed))
    monkeypatch.setattr("cps.admin._ingest_failure_dirs", lambda: (str(failed), str(ingest)))
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        _register_remaining_blueprints(env.app)
        c = env.app.test_client()
        c.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
        yield c, failed, ingest


@pytest.mark.unit
def test_page_lists_and_retry_route_moves_file(admin_client):
    c, failed, ingest = admin_client
    html = c.get("/admin/ingest_failures").get_data(as_text=True)
    assert "Book.epub" in html and "Retry" in html
    resp = c.post("/admin/ingest_failures/retry", data={"names": "20260101_030000_Book.epub"})
    assert resp.status_code == 302
    assert (ingest / "Book.epub").exists() and not any(failed.iterdir())


@pytest.mark.unit
def test_action_route_rejects_bad_input(admin_client):
    c, failed, _ = admin_client
    assert c.post("/admin/ingest_failures/explode", data={"names": "x"}).status_code == 400
    assert c.post("/admin/ingest_failures/delete", data={}).status_code == 400
    c.post("/admin/ingest_failures/delete", data={"names": "../../etc/passwd"})
    assert (failed / "20260101_030000_Book.epub").exists()
