"""Fetch metadata for new books applies a provider's cover only when the book has
none or the new one is larger, so a good embedded cover isn't swapped for a
thumbnail. A cover already weighed, or one that can't be larger, isn't downloaded."""
import sqlite3
from types import SimpleNamespace

import pytest

from .lily_env import lily_env
from .metadata_fakes import FakeProvider

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
        # The real cwa.db (temp_cwa_db) remembers the covers weighed; only the switch is set
        from cwa_db import CWA_DB

        class Store(CWA_DB):
            def get_cwa_settings(self):
                return {"auto_metadata_fetch_enabled": 1}
        monkeypatch.setattr(metadata_helper, "CWA_DB", Store)
        env.sizes = {}
        monkeypatch.setattr(metadata_helper, "_image_area",
                            lambda path: env.sizes.get(open(path, "rb").read()[:4], 0))
        env.downloads = downloads
        yield env


def _fetch(env, monkeypatch, cover, has_cover, old=None, largest=0):
    """Looks up a book (with a cover.jpg holding old, when given) and returns (changed, cover bytes)."""
    from cps import metadata_helper
    record = SimpleNamespace(title="Dune", authors=["Frank Herbert"], description="", publisher="",
                             tags=[], series="", series_index=0, publishedDate="", identifiers={},
                             cover=cover, cover_max_pixels=largest,
                             source=SimpleNamespace(description="Google Books"))
    monkeypatch.setattr(metadata_helper, "metadata_providers", [FakeProvider(
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
    env.book_id, env.folder = book_id, folder
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


@pytest.mark.parametrize("old, new", [(600 * 900, 128 * 192), (100 * 150, 600 * 900)])
def test_a_cover_already_weighed_is_not_downloaded_again(env, monkeypatch, old, new):
    # Whichever cover won: a rebuild would otherwise fetch every matched book's cover each time
    from cps import metadata_helper
    env.sizes.update({b"old!": old, b"new:": new})
    _fetch(env, monkeypatch, "https://covers.example/1.jpg", 1, old=b"old!")
    assert env.downloads == ["https://covers.example/1.jpg"]
    assert metadata_helper.fetch_and_apply_metadata(env.book_id) is False
    assert env.downloads == ["https://covers.example/1.jpg"]


def test_a_cover_is_weighed_again_once_the_books_own_changes(env, monkeypatch):
    from cps import metadata_helper
    env.sizes.update({b"old!": 600 * 900, b"new:": 128 * 192, b"tiny": 10 * 15})
    _fetch(env, monkeypatch, "https://covers.example/1.jpg", 1, old=b"old!")
    (env.folder / "cover.jpg").write_bytes(b"tiny cover uploaded since")
    assert metadata_helper.fetch_and_apply_metadata(env.book_id) is True
    assert len(env.downloads) == 2 and (env.folder / "cover.jpg").read_bytes().startswith(b"new:")


def test_a_cover_that_cannot_be_larger_is_not_downloaded(env, monkeypatch):
    # Google's covers come within 800x900; a page render is larger than that
    env.sizes.update({b"old!": 1275 * 1650})
    assert _fetch(env, monkeypatch, "https://covers.example/g.jpg", 1, old=b"old!", largest=800 * 900) == (
        False, b"old!")
    assert env.downloads == []


def test_a_book_without_a_cover_always_gets_the_providers(env, monkeypatch):
    changed, cover = _fetch(env, monkeypatch, "https://covers.example/g.jpg", 0, largest=800 * 900)
    assert changed and cover.startswith(b"new:")
