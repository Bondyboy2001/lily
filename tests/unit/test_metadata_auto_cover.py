"""Fetch metadata for new books applies a provider's cover only when the book has
none or the new one is larger, so a good embedded cover isn't swapped for a
thumbnail."""
import sqlite3
from types import SimpleNamespace

import pytest

from .lily_env import lily_env

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, temp_cwa_db, monkeypatch):
    from cps import metadata_helper
    with lily_env(tmp_path) as env:
        downloads = []

        def fake_save(url, folder):
            # An absolute folder: the download lands outside the library first
            downloads.append(url)
            with open(folder + "/cover.jpg", "wb") as f:
                f.write(b"new:" + url.encode())
            return True, None
        monkeypatch.setattr(metadata_helper.helper, "save_cover_from_url", fake_save)
        monkeypatch.setattr(metadata_helper.helper, "replace_cover_thumbnail_cache", lambda *a, **k: None)
        monkeypatch.setattr(metadata_helper, "pdf_first_page_text", lambda book: "")
        monkeypatch.setattr(metadata_helper, "CWA_DB", lambda: SimpleNamespace(
            get_cwa_settings=lambda: {"auto_metadata_fetch_enabled": 1}))
        env.sizes = {}
        monkeypatch.setattr(metadata_helper, "_image_area",
                            lambda path: env.sizes.get(open(path, "rb").read()[:4], 0))
        env.downloads = downloads
        yield env


def _fetch(env, monkeypatch, cover, has_cover, old=None):
    """Looks up a book (with a cover.jpg holding old, when given) and returns (changed, cover bytes)."""
    from cps import metadata_helper
    record = SimpleNamespace(title="Dune", authors=["Frank Herbert"], description="", publisher="",
                             tags=[], series="", series_index=0, publishedDate="", identifiers={},
                             cover=cover, source=SimpleNamespace(description="Google Books"))
    monkeypatch.setattr(metadata_helper, "metadata_providers", [SimpleNamespace(
        __id__="google", __name__="Google", identifier_types=frozenset(),
        search=lambda q, *a: [record])])
    book_id = env.add_book("Dune", author="Frank Herbert")
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.create_function("title_sort", 1, lambda t: t)
    con.execute("UPDATE books SET has_cover=? WHERE id=?", (has_cover, book_id))
    con.commit()
    con.close()
    folder = env.library_dir / "Frank Herbert" / "Dune"
    folder.mkdir(parents=True)
    if old is not None:
        (folder / "cover.jpg").write_bytes(old)
    changed = metadata_helper.fetch_and_apply_metadata(book_id)
    cover_file = folder / "cover.jpg"
    # No backup left behind
    assert [p.name for p in folder.iterdir() if p.name != "cover.jpg"] == []
    return changed, cover_file.read_bytes() if cover_file.exists() else None


def test_a_book_without_a_cover_gets_the_fetched_one(env, monkeypatch):
    assert _fetch(env, monkeypatch, "https://covers.example/1.jpg", 0) == (
        True, b"new:https://covers.example/1.jpg")


def test_a_larger_fetched_cover_replaces_the_current_one(env, monkeypatch):
    env.sizes.update({b"old!": 100 * 150, b"new:": 600 * 900})
    changed, cover = _fetch(env, monkeypatch, "https://covers.example/big.jpg", 1, old=b"old!")
    assert changed and cover.startswith(b"new:")


def test_a_smaller_fetched_cover_keeps_the_current_one(env, monkeypatch):
    env.sizes.update({b"old!": 600 * 900, b"new:": 128 * 192})
    assert _fetch(env, monkeypatch, "https://covers.example/thumb.jpg", 1, old=b"old!") == (False, b"old!")


def test_a_cover_that_cannot_be_measured_keeps_the_current_one(env, monkeypatch):
    assert _fetch(env, monkeypatch, "https://covers.example/x.jpg", 1, old=b"old!") == (False, b"old!")


def test_a_placeholder_cover_is_not_downloaded(env, monkeypatch):
    assert _fetch(env, monkeypatch, "/static/generic_cover.svg", 0) == (False, None)
    assert env.downloads == []


def test_a_failed_save_puts_the_old_cover_back(env, monkeypatch):
    env.sizes.update({b"old!": 100 * 150, b"new:": 600 * 900})

    def failing_commit(*a, **k):
        raise RuntimeError("database is locked")
    monkeypatch.setattr("sqlalchemy.orm.Session.commit", failing_commit)
    assert _fetch(env, monkeypatch, "https://covers.example/big.jpg", 1, old=b"old!") == (False, b"old!")
