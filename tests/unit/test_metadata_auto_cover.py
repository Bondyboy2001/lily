"""Fetch metadata for new books applies a provider's cover only when the book has
none or the new one is larger, so a good embedded cover isn't swapped for a
thumbnail."""
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def library(tmp_path, monkeypatch):
    import cps
    from cps import metadata_helper
    (tmp_path / "Author" / "Book (1)").mkdir(parents=True)
    monkeypatch.setattr(cps.config, "get_book_path", lambda: str(tmp_path), raising=False)
    saved = []

    def fake_save(url, book_path):
        # An absolute path, so the download lands outside the library first
        saved.append(url)
        (tmp_path / book_path / "cover.jpg").write_bytes(b"new:" + url.encode())
        return True, None
    monkeypatch.setattr(metadata_helper.helper, "save_cover_from_url", fake_save)
    monkeypatch.setattr(metadata_helper.helper, "replace_cover_thumbnail_cache", lambda *a, **k: None)
    sizes = {}
    monkeypatch.setattr(metadata_helper, "_image_area",
                        lambda path: sizes.get(open(path, "rb").read()[:4], 0))
    return SimpleNamespace(root=tmp_path, saved=saved, sizes=sizes, helper=metadata_helper)


def _book(has_cover):
    return SimpleNamespace(id=1, path="Author/Book (1)", has_cover=has_cover)


def test_a_book_without_a_cover_gets_the_fetched_one(library):
    book = _book(0)
    assert library.helper._apply_cover(book, "https://covers.example/1.jpg") is True
    assert book.has_cover == 1
    assert (library.root / book.path / "cover.jpg").read_bytes() == b"new:https://covers.example/1.jpg"


def test_a_larger_fetched_cover_replaces_the_current_one(library):
    book = _book(1)
    (library.root / book.path / "cover.jpg").write_bytes(b"old!")
    library.sizes.update({b"old!": 100 * 150, b"new:": 600 * 900})
    assert library.helper._apply_cover(book, "https://covers.example/big.jpg") is True
    assert (library.root / book.path / "cover.jpg").read_bytes().startswith(b"new:")


def test_a_smaller_fetched_cover_keeps_the_current_one(library):
    book = _book(1)
    (library.root / book.path / "cover.jpg").write_bytes(b"old!")
    library.sizes.update({b"old!": 600 * 900, b"new:": 128 * 192})
    assert library.helper._apply_cover(book, "https://covers.example/thumb.jpg") is False
    assert (library.root / book.path / "cover.jpg").read_bytes() == b"old!"


def test_a_cover_that_cannot_be_measured_keeps_the_current_one(library):
    book = _book(1)
    (library.root / book.path / "cover.jpg").write_bytes(b"old!")
    assert library.helper._apply_cover(book, "https://covers.example/x.jpg") is False
    assert (library.root / book.path / "cover.jpg").read_bytes() == b"old!"


def test_a_placeholder_cover_is_not_downloaded(library):
    assert library.helper._apply_cover(_book(0), "/static/generic_cover.svg") is False
    assert library.helper._apply_cover(_book(0), "") is False
    assert library.saved == []


def test_fetching_applies_a_cover_alongside_the_other_fields(tmp_path, temp_cwa_db, monkeypatch):
    # Through the whole apply step, with no published date to parse first
    from cps import metadata_helper
    from .lily_env import lily_env
    with lily_env(tmp_path) as env:
        covers = []
        record = SimpleNamespace(title="Dune", authors=["Frank Herbert"], description="",
                                 publisher="", tags=[], series="", series_index=None,
                                 publishedDate="", rating=None, identifiers={}, languages=[],
                                 cover="https://covers.example/dune.jpg")
        provider = SimpleNamespace(__id__="google", __name__="google", active=True,
                                   identifier_types=frozenset({"isbn"}),
                                   is_globally_enabled=lambda enabled: True,
                                   search_identifiers=lambda ids, *a: [],
                                   search=lambda q, *a: [record])
        monkeypatch.setattr(metadata_helper, "metadata_providers", [provider])
        monkeypatch.setattr(metadata_helper, "pdf_first_page_text", lambda book: "")
        monkeypatch.setattr(metadata_helper, "_apply_cover",
                            lambda book, url: covers.append(url) or True)
        book_id = env.add_book("Dune", author="Frank Herbert")
        assert metadata_helper.fetch_and_apply_metadata(book_id, force=True) is True
        assert covers == ["https://covers.example/dune.jpg"]
