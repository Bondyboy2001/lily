"""The PDF reader's linearized copies (cps/pdf_fast.py) and how the reader and /show use them."""

import os
import shutil

import pytest

from cps import pdf_fast
from tests.unit.lily_env import lily_env, ADMIN_PASSWORD
from tests.unit.test_lily_reader_static import _register_remaining_blueprints

needs_qpdf = pytest.mark.skipif(not shutil.which("qpdf"), reason="qpdf not installed")


def _pdf(path, pages=3):
    """A small valid, non-linearized PDF."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join(f"{3 + i} 0 R" for i in range(pages)), pages)]
    objs += ["<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"] * pages
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)
    return str(path)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_fast.constants, "CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(pdf_fast, "MIN_SIZE", 0)
    return tmp_path


def _wait():
    pdf_fast._executor.submit(lambda: None).result(timeout=60)


@needs_qpdf
def test_prepare_makes_a_linearized_copy_once(cache):
    source = _pdf(cache / "lib" / "book.pdf")
    assert pdf_fast.ready(7, source) is None
    pdf_fast.prepare(7, source)
    _wait()
    copy = pdf_fast.ready(7, source)
    assert copy and copy.startswith(str(cache / "config" / "pdf_fast"))
    with open(copy, "rb") as f:
        assert b"/Linearized" in f.read(1024)
    # The library file is left alone
    with open(source, "rb") as f:
        assert b"/Linearized" not in f.read(1024)


@needs_qpdf
def test_a_changed_file_gets_a_new_copy_and_the_old_one_goes(cache):
    source = _pdf(cache / "lib" / "book.pdf")
    pdf_fast.prepare(7, source)
    _wait()
    old = pdf_fast.ready(7, source)
    _pdf(cache / "lib" / "book.pdf", pages=5)
    os.utime(source, (1, 1))
    assert pdf_fast.ready(7, source) is None
    pdf_fast.prepare(7, source)
    _wait()
    assert pdf_fast.ready(7, source) and not os.path.exists(old)


def test_small_or_already_linearized_files_are_skipped(cache, monkeypatch):
    source = _pdf(cache / "lib" / "book.pdf")
    monkeypatch.setattr(pdf_fast, "MIN_SIZE", 10 ** 9)
    assert not pdf_fast._wanted(source)
    monkeypatch.setattr(pdf_fast, "MIN_SIZE", 0)
    assert pdf_fast._wanted(source)
    (cache / "lin.pdf").write_bytes(b"%PDF-1.4\n1 0 obj << /Linearized 1 >> endobj\n")
    assert not pdf_fast._wanted(str(cache / "lin.pdf"))


def test_a_failed_file_is_not_retried(cache, monkeypatch):
    source = _pdf(cache / "lib" / "book.pdf")
    monkeypatch.setattr(pdf_fast.shutil, "which", lambda name: "/bin/false")
    path = pdf_fast._cache_path(7, source)
    os.makedirs(os.path.dirname(path))
    open(path + ".failed", "w").close()
    calls = []
    monkeypatch.setattr(pdf_fast._executor, "submit", lambda *a: calls.append(a))
    pdf_fast.prepare(7, source)
    assert calls == []


def test_prune_drops_the_oldest_copies_past_the_cap(cache, monkeypatch):
    folder = cache / "config" / "pdf_fast"
    folder.mkdir(parents=True)
    for n, age in ((1, 300), (2, 200), (3, 100)):
        (folder / f"{n}_0_10.pdf").write_bytes(b"x" * 10)
        os.utime(folder / f"{n}_0_10.pdf", (age, age))
    monkeypatch.setattr(pdf_fast, "CACHE_CAP", 20)
    pdf_fast._prune()
    assert sorted(os.listdir(folder)) == ["1_0_10.pdf", "2_0_10.pdf"]


@needs_qpdf
def test_reader_uses_the_fast_copy_once_it_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_fast.constants, "CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(pdf_fast, "MIN_SIZE", 0)
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        _register_remaining_blueprints(env.app)
        book_id = env.add_book("Big Book", fmt="PDF")
        source = _pdf(env.library_dir / "Test Author" / "Big Book" / "Big Book.pdf")
        c = env.app.test_client()
        c.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
        # First open: the file itself, and the copy gets made in the background
        html = c.get(f"/read/{book_id}/pdf").get_data(as_text=True)
        assert f'"/show/{book_id}/pdf"' in html
        _wait()
        assert pdf_fast.ready(book_id, source)
        html = c.get(f"/read/{book_id}/pdf").get_data(as_text=True)
        assert f'"/show/{book_id}/pdf?fast=1"' in html
        fast = c.get(f"/show/{book_id}/pdf?fast=1", headers={"Range": "bytes=0-1023"})
        assert fast.status_code == 206 and b"/Linearized" in fast.data
        # Every range of one viewing must come from the same file version (same ETag)
        again = c.get(f"/show/{book_id}/pdf?fast=1", headers={"Range": "bytes=1024-2047"})
        assert again.status_code == 206 and again.headers["ETag"] == fast.headers["ETag"]
        again.close()
        plain = c.get(f"/show/{book_id}/pdf")
        assert b"/Linearized" not in plain.data[:1024]
        plain.close()
        fast.close()
