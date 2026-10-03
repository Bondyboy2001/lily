"""Tags are the user's own: an import clears the ones calibre read from the file, and metadata
lookups and Rebuild metadata never add, change or tidy them."""
import sqlite3
from types import SimpleNamespace

import pytest

from .lily_env import lily_env
from .metadata_fakes import FakeProvider

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as env:
        yield env


def _tags(env):
    con = sqlite3.connect(env.library_dir / "metadata.db")
    try:
        links = con.execute("SELECT l.book, t.name FROM books_tags_link l JOIN tags t ON t.id = l.tag "
                            "ORDER BY l.book, t.name").fetchall()
        names = {row[0] for row in con.execute("SELECT name FROM tags")}
    finally:
        con.close()
    by_book = {}
    for book, name in links:
        by_book.setdefault(book, []).append(name)
    return by_book, names


def test_a_new_import_arrives_with_no_tags(env):
    from cps.tag_cleanup import clear_new_book_details
    book = env.add_book("Measure Theory", tags=["Measure theory", "Hardcover: 436 pages"])
    keep = env.add_book("Other", tags=["Probability"])
    plain = env.add_book("Plain")
    assert clear_new_book_details(book) is True
    assert clear_new_book_details(plain) is False
    by_book, names = _tags(env)
    # Only that book's: the tag no book uses any more goes with them
    assert by_book == {keep: ["Probability"]} and names == {"Probability"}


def _lookup_finds(monkeypatch, **extra):
    from cps import metadata_helper
    settings = {"auto_metadata_fetch_enabled": 1}
    monkeypatch.setattr(metadata_helper, "CWA_DB", lambda: SimpleNamespace(get_cwa_settings=lambda: settings))
    record = SimpleNamespace(title="Abstract Algebra", authors=["Test Author"], description="",
                             series="", series_index=0, publishedDate=None, identifiers={}, cover=None,
                             source=SimpleNamespace(description="Google Books"), **extra)
    monkeypatch.setattr(metadata_helper, "metadata_providers", [FakeProvider(
        __id__="google", __name__="Google", identifier_types=frozenset(),
        search=lambda q, *a: [record])])
    return metadata_helper


def test_a_lookup_never_touches_tags(env, monkeypatch):
    # Even a record that somehow carries subjects brings none, and the book's own stay
    helper = _lookup_finds(monkeypatch, tags=["Mathematics", "Springer 2011"])
    bare = env.add_book("Abstract Algebra", author="Test Author")
    mine = env.add_book("Abstract Algebra", author="Test Author", tags=["To read", "9780511629563"])
    helper.fetch_and_apply_metadata(bare, force=True)
    helper.fetch_and_apply_metadata(mine, force=True)
    assert _tags(env)[0] == {mine: ["9780511629563", "To read"]}


def test_rebuild_leaves_tags_alone(env, monkeypatch):
    from cps import metadata_helper
    from cps.tasks.metadata_rebuild import TaskRebuildMetadata
    book = env.add_book("Analysis", tags=["Mathematics", "# Hardcover: 308 pages"])
    monkeypatch.setattr(metadata_helper, "fetch_and_apply_metadata",
                        lambda book_id, force=False, unanswered=None: False)
    monkeypatch.setattr("cps.duplicate_index.mark_duplicate_index_pending", lambda reason=None: None)
    with env.app.test_request_context():
        TaskRebuildMetadata().start(None)
    assert _tags(env)[0] == {book: ["# Hardcover: 308 pages", "Mathematics"]}


def test_a_new_import_keeps_its_date_but_no_publisher_language_or_rating(env):
    from cps.tag_cleanup import clear_new_book_details
    book = env.add_book("Dune")
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.create_function("title_sort", 1, lambda t: t)
    con.execute("UPDATE books SET pubdate = '1965-08-01 00:00:00+00:00' WHERE id = ?", (book,))
    con.execute("INSERT INTO publishers (name, sort) VALUES ('Ace', 'Ace')")
    con.execute("INSERT INTO books_publishers_link (book, publisher) VALUES (?, 1)", (book,))
    con.execute("INSERT INTO languages (lang_code) VALUES ('eng')")
    con.execute("INSERT INTO books_languages_link (book, lang_code) VALUES (?, 1)", (book,))
    con.execute("INSERT INTO ratings (rating) VALUES (8)")
    con.execute("INSERT INTO books_ratings_link (book, rating) VALUES (?, 1)", (book,))
    con.commit()
    con.close()
    assert clear_new_book_details(book) is True
    con = sqlite3.connect(env.library_dir / "metadata.db")
    try:
        for table in ("books_publishers_link", "books_languages_link", "books_ratings_link", "publishers",
                      "languages", "ratings"):
            assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
        assert con.execute("SELECT pubdate FROM books WHERE id = ?", (book,)).fetchone()[0].startswith("1965-08-01")
    finally:
        con.close()
