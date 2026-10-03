"""A scanned PDF has no text layer: the automatic lookup reads its pages by OCR, and its back
cover's ISBN barcode."""
from types import SimpleNamespace

import pytest

from cps import page_ocr

from .lily_env import lily_env
from .metadata_fakes import lookup_setup, recording_provider as _provider

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as env:
        yield env


def test_a_page_with_next_to_no_text_needs_ocr():
    assert page_ocr.needs_ocr("") and page_ocr.needs_ocr("  \n 12 \n")
    assert not page_ocr.needs_ocr("Calculus: Early Transcendentals, James Stewart")


def test_only_a_books_ean_is_an_isbn():
    # A back cover's barcode, its price add-on and a shop's own label
    assert page_ocr.isbns_in("9780691169866\n51995\n5012345678900\n9780691169866\n") == ["9780691169866"]
    assert page_ocr.isbns_in("") == []


def test_without_the_tools_nothing_is_read(monkeypatch):
    monkeypatch.setattr(page_ocr.shutil, "which", lambda name: None)
    page_ocr.ocr_pages.cache_clear()
    page_ocr.back_cover_isbn.cache_clear()
    assert page_ocr.ocr_pages("/nowhere.pdf", 1.0, 0, 1) == ""
    assert page_ocr.back_cover_isbn("/nowhere.pdf", 1.0) == ""


def _scan(monkeypatch, helper, pages, barcode=""):
    """The book's PDF is a scan: OCR reads `pages` (first, last) -> text, and the barcode."""
    monkeypatch.setattr(helper, "_pdf_file", lambda book: ("/library/scan.pdf", 1.0))
    monkeypatch.setattr(page_ocr, "available", lambda: True)
    monkeypatch.setattr(page_ocr, "ocr_pages", lambda path, mtime, first, last: pages.get((first, last), ""))
    monkeypatch.setattr(page_ocr, "back_cover_isbn", lambda path, mtime: barcode)


def test_a_page_with_a_text_layer_is_not_read_again(monkeypatch):
    from cps import metadata_helper
    _scan(monkeypatch, metadata_helper, {(0, 1): pytest.fail})
    text = "Deep Learning\nIan Goodfellow"
    assert metadata_helper.scanned_text(SimpleNamespace(), text, 0, 1) == text


def test_a_scanned_book_with_no_author_is_matched_by_the_author_ocr_reads(env, monkeypatch):
    stewart = SimpleNamespace(title="Calculus", authors=["James Stewart"])
    helper, applied = lookup_setup(monkeypatch, [_provider("openlibrary", by_text=[stewart])])
    _scan(monkeypatch, helper, {(0, 1): "CALCULUS\nJAMES STEWART"})
    book_id = env.add_book("Calculus", author="Unknown", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is True
    assert applied == ["Calculus"]


def test_a_scan_named_by_its_file_is_found_by_its_back_cover_barcode(env, monkeypatch):
    calls = []
    princeton = SimpleNamespace(title="Visual Complex Analysis", authors=["Tristan Needham"],
                                identifiers={"isbn": "9780198534464"})
    helper, applied = lookup_setup(monkeypatch, [_provider("openlibrary", by_id=[princeton], calls=calls)])
    _scan(monkeypatch, helper, {(1, helper.OCR_FRONT_PAGES): "VISUAL\nCOMPLEX\nANALYSIS\nTristan Needham"},
          barcode="9780198534464")
    book_id = env.add_book("scan_0042.pdf", author="Unknown", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is True
    assert applied == ["Visual Complex Analysis"]
    assert calls == [("openlibrary", "ids", {"isbn": "9780198534464"})]


def test_a_barcode_for_another_book_is_not_applied(env, monkeypatch):
    other = SimpleNamespace(title="Some Other Book", authors=["Someone Else"], identifiers={"isbn": "9780198534464"})
    helper, applied = lookup_setup(monkeypatch, [_provider("openlibrary", by_id=[other])])
    _scan(monkeypatch, helper, {(1, helper.OCR_FRONT_PAGES): "VISUAL COMPLEX ANALYSIS"}, barcode="9780198534464")
    book_id = env.add_book("scan_0042.pdf", author="Unknown", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is False
    assert applied == []
