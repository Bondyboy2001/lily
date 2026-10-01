# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Structural checks used before importing a dropped file and before replacing a polished book."""

import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import book_integrity as bi  # noqa: E402

pytestmark = pytest.mark.unit


def make_epub(path: Path, body: bytes = b"<html>" + b"x" * 4000 + b"</html>") -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("OEBPS/chapter.xhtml", body)
    return path


def make_azw3(path: Path, size: int = 4096) -> Path:
    header = b"Title".ljust(60, b"\0") + b"BOOKMOBI"
    path.write_bytes(header + b"\0" * (size - len(header)))
    return path


def test_complete_files_pass(tmp_path):
    epub = make_epub(tmp_path / "a.epub")
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.7\n" + b"x" * 10000 + b"\n%%EOF\n")
    other = tmp_path / "a.mobi"
    other.write_bytes(b"whatever")
    assert bi.incomplete_reason(str(epub)) is None
    assert bi.incomplete_reason(str(pdf)) is None
    assert bi.incomplete_reason(str(other)) is None


def test_truncated_zip_and_pdf_are_incomplete(tmp_path):
    epub = make_epub(tmp_path / "a.epub")
    data = epub.read_bytes()
    for ext in ("epub", "kepub", "cbz"):
        cut = tmp_path / f"cut.{ext}"
        cut.write_bytes(data[: len(data) // 2])
        assert "zip" in bi.incomplete_reason(str(cut))
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.7\n%%EOF\n" + b"x" * 10000)  # %%EOF only far from the end
    assert "%%EOF" in bi.incomplete_reason(str(pdf))
    empty = tmp_path / "e.epub"
    empty.write_bytes(b"")
    assert bi.incomplete_reason(str(empty)) == "file is empty"


def test_zip_with_bad_crc_opens_but_is_not_intact(tmp_path):
    epub = make_epub(tmp_path / "a.epub", body=b"A" * 5000)
    with zipfile.ZipFile(epub) as zf:
        info = zf.getinfo("mimetype")
        offset = info.header_offset + 30 + len(info.filename)
    raw = bytearray(epub.read_bytes())
    raw[offset] ^= 0xFF  # corrupt a stored member's data
    epub.write_bytes(bytes(raw))
    assert bi.zip_opens(str(epub))
    assert bi.incomplete_reason(str(epub)) is None  # ingest stays lenient
    assert not bi.zip_is_intact(str(epub))


def test_polished_output_checks(tmp_path):
    original = make_epub(tmp_path / "book.epub")
    good = make_epub(tmp_path / "good.epub")
    assert bi.polished_output_problem(str(original), str(good), 0.5) is None

    empty = tmp_path / "empty.epub"
    empty.write_bytes(b"")
    assert bi.polished_output_problem(str(original), str(empty), 0.5) == "output is empty"

    tiny = make_epub(tmp_path / "tiny.epub", body=b"")
    big = make_epub(tmp_path / "big.epub", body=bytes(range(256)) * 400)
    assert "suspiciously small" in bi.polished_output_problem(str(big), str(tiny), 0.5)

    broken = tmp_path / "broken.epub"
    broken.write_bytes(original.read_bytes()[:-30])  # lost the end of the central directory
    assert bi.polished_output_problem(str(original), str(broken), 0.1) == "output is not an intact zip"

    assert "cannot stat" in bi.polished_output_problem(str(original), str(tmp_path / "missing.epub"), 0.5)


def test_polished_azw3_must_keep_mobi_header(tmp_path):
    original = make_azw3(tmp_path / "book.azw3")
    good = make_azw3(tmp_path / "good.azw3")
    bad = tmp_path / "bad.azw3"
    bad.write_bytes(b"\0" * 4096)
    assert bi.polished_output_problem(str(original), str(good), 0.5) is None
    assert "BOOKMOBI" in bi.polished_output_problem(str(original), str(bad), 0.5)
