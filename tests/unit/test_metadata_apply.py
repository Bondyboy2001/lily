"""Applying a provider's record to a book (imports and Rebuild metadata): only what
changes is written, at once, and one book's failure stays with that book."""
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from .lily_env import lily_env

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as env:
        yield env


def _record(**kw):
    rec = SimpleNamespace(title="", authors=[], description="", publisher="", tags=[], series="",
                          series_index=0, publishedDate=None, rating=None, identifiers={}, cover="",
                          source=SimpleNamespace(description="Google Books"))
    rec.__dict__.update(kw)
    return rec


def _setup(monkeypatch, record=None, by_id=(), id_types=(), page="", settings=None):
    from cps import metadata_helper
    settings = settings or {"auto_metadata_fetch_enabled": 1}
    monkeypatch.setattr(metadata_helper, "CWA_DB", lambda: SimpleNamespace(get_cwa_settings=lambda: settings))
    monkeypatch.setattr(metadata_helper, "pdf_first_page_text", lambda book: page)
    provider = SimpleNamespace(__id__="google", __name__="Google", identifier_types=frozenset(id_types),
                               search_identifiers=lambda ids, *a: list(by_id),
                               search=lambda q, *a: [record] if record else [])
    monkeypatch.setattr(metadata_helper, "metadata_providers", [provider])
    return metadata_helper


def _q(env, sql, *args):
    con = sqlite3.connect(env.library_dir / "metadata.db")
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _sql(env, *statements):
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.create_function("title_sort", 1, lambda t: t)  # calibre's triggers call it
    for statement, args in statements:
        con.execute(statement, args)
    con.commit()
    con.close()


def _rate(env, book_id, value):
    _sql(env, ("INSERT OR IGNORE INTO ratings (rating) VALUES (?)", (value,)),
         ("INSERT INTO books_ratings_link (book, rating) VALUES (?, (SELECT id FROM ratings WHERE rating=?))",
          (book_id, value)))


def _ratings(env):
    return _q(env, "SELECT l.book, r.rating FROM books_ratings_link l JOIN ratings r ON r.id=l.rating ORDER BY l.book")


def test_a_providers_rating_is_left_alone(env, monkeypatch):
    # It is its readers' average; and ratings rows are shared, unique values
    other = env.add_book("Other Book", author="Jane Roe")
    dune = env.add_book("Dune", author="Frank Herbert")
    _rate(env, other, 6)
    _rate(env, dune, 6)
    helper = _setup(monkeypatch, _record(title="Dune", authors=["Frank Herbert"], description="Spice.", rating=4.5))
    assert helper.fetch_and_apply_metadata(dune) is True
    assert _ratings(env) == [(other, 6), (dune, 6)]
    assert _q(env, "SELECT text FROM comments") == [("Spice.",)]


def test_a_repeated_author_is_added_once(env, monkeypatch):
    dune = env.add_book("Dune", author="Unknown")
    helper = _setup(monkeypatch, _record(title="Dune", authors=["Frank Herbert", "Frank Herbert", " "]))
    assert helper.fetch_and_apply_metadata(dune) is True
    assert _q(env, "SELECT a.name FROM books_authors_link l JOIN authors a ON a.id=l.author") == [("Frank Herbert",)]


def test_an_identifier_type_in_other_case_is_kept_not_duplicated(env, monkeypatch):
    dune = env.add_book("Dune", author="Frank Herbert")
    _sql(env, ("INSERT INTO identifiers (book, type, val) VALUES (?, 'ISBN', '9780441013593')", (dune,)))
    helper = _setup(monkeypatch, _record(title="Dune", authors=["Frank Herbert"], description="Spice.",
                                         identifiers={"isbn": "9780441172719", "google": "abc"}))
    assert helper.fetch_and_apply_metadata(dune) is True
    # The book's own ISBN stays; the new kind of identifier is added
    assert sorted(_q(env, "SELECT lower(type), val FROM identifiers")) == [
        ("google", "abc"), ("isbn", "9780441013593")]


def test_nothing_new_is_not_an_update(env, monkeypatch):
    dune = env.add_book("Dune", author="Frank Herbert")
    helper = _setup(monkeypatch, _record(title="Dune", authors=["Frank Herbert"], description="Spice.",
                                         publisher="Ace", identifiers={"google": "abc"}, tags=["Science fiction"]))
    assert helper.fetch_and_apply_metadata(dune) is True
    before = _q(env, "SELECT title, author_sort, last_modified FROM books")
    # The same record again changes nothing, so Rebuild doesn't count it or rewrite the files
    assert helper.fetch_and_apply_metadata(dune) is False
    assert _q(env, "SELECT title, author_sort, last_modified FROM books") == before


def test_a_change_bumps_last_modified_and_drops_unused_authors(env, monkeypatch):
    dune = env.add_book("Dune", author="Unknown")
    before = _q(env, "SELECT last_modified FROM books")
    helper = _setup(monkeypatch, _record(title="Dune", authors=["Frank Herbert"], series="Dune", series_index=None))
    assert helper.fetch_and_apply_metadata(dune) is True
    assert _q(env, "SELECT last_modified FROM books") != before
    assert ("Unknown",) not in _q(env, "SELECT name FROM authors")
    assert _q(env, "SELECT series_index FROM books") == [(1.0,)]


def test_a_new_series_without_an_index_starts_at_one(env, monkeypatch):
    dune = env.add_book("Dune", author="Frank Herbert")
    _sql(env, ("UPDATE books SET series_index=7 WHERE id=?", (dune,)))
    helper = _setup(monkeypatch, _record(title="Dune", authors=["Frank Herbert"], series="Dune Chronicles"))
    assert helper.fetch_and_apply_metadata(dune) is True
    assert _q(env, "SELECT series_index FROM books") == [(1.0,)]


def test_a_cover_download_error_keeps_the_rest(env, monkeypatch):
    import requests
    dune = env.add_book("Dune", author="Frank Herbert")
    helper = _setup(monkeypatch, _record(title="Dune", authors=["Frank Herbert"], description="Spice.",
                                         cover="https://covers.example/x.jpg"))

    def broken(url, path):
        raise requests.exceptions.ChunkedEncodingError("broken stream")
    monkeypatch.setattr(helper.helper, "save_cover_from_url", broken)
    assert helper.fetch_and_apply_metadata(dune) is True
    assert _q(env, "SELECT text FROM comments") == [("Spice.",)]


def test_an_arxiv_id_cited_on_the_first_page_is_not_this_paper(env, monkeypatch):
    paper = env.add_book("A Journal Paper On Graphs", author="Alice Smith", fmt="PDF")
    page = ("A Journal Paper On Graphs\nAlice Smith\nAbstract. We extend the transformer of "
            "Vaswani et al. (arXiv:1706.03762) to graphs.")
    cited = _record(title="Attention Is All You Need", authors=["Ashish Vaswani"],
                    identifiers={"arxiv": "1706.03762"})
    helper = _setup(monkeypatch, by_id=[cited], id_types={"arxiv"}, page=page)
    assert helper.fetch_and_apply_metadata(paper) is False
    assert _q(env, "SELECT title FROM books") == [("A Journal Paper On Graphs",)]


def test_an_isbn_naming_another_book_by_the_same_author_is_not_applied(env, monkeypatch):
    book = env.add_book("Foundation", author="Isaac Asimov")
    _sql(env, ("INSERT INTO identifiers (book, type, val) VALUES (?, 'isbn', '9780553294385')", (book,)))
    other = _record(title="I, Robot", authors=["Isaac Asimov"], identifiers={"isbn": "9780553294385"})
    helper = _setup(monkeypatch, by_id=[other], id_types={"isbn"})
    assert helper.fetch_and_apply_metadata(book) is False
    assert _q(env, "SELECT title FROM books") == [("Foundation",)]


def test_one_failing_book_does_not_stop_the_rebuild(env, monkeypatch):
    from cps import metadata_helper
    from cps.services.worker import STAT_FINISH_SUCCESS
    from cps.tasks import metadata_rebuild
    books = [env.add_book(t, author="Some One") for t in ("One", "Two", "Three")]
    helper = _setup(monkeypatch, _record(title="Two", authors=["Some One"], description="Fine."))
    real_apply = metadata_helper._apply_record

    def apply(cdb, book, record, cover):
        if book.id == books[0]:
            # A save that breaks mid-way, as a database constraint would
            book.title = "Broken"
            cdb.session.flush()
            raise RuntimeError("constraint failed")
        return real_apply(cdb, book, record, cover)
    monkeypatch.setattr(helper, "_apply_record", apply)
    monkeypatch.setattr("cps.duplicate_index.mark_duplicate_index_pending", lambda reason=None: None)
    monkeypatch.setattr(metadata_rebuild, "tidy_library_tags", lambda s: (0, 0))
    task = metadata_rebuild.TaskRebuildMetadata()
    with env.app.test_request_context():
        task.start(None)
    assert task.stat == STAT_FINISH_SUCCESS and task.checked == 3, task.error
    assert task.updated == 1
    assert _q(env, "SELECT title FROM books ORDER BY id") == [("One",), ("Two",), ("Three",)]
    assert _q(env, "SELECT text FROM comments") == [("Fine.",)]


def test_a_request_ending_during_the_cover_download_loses_nothing(env, monkeypatch):
    # A web request ending (its session removed) must not roll the lookup's changes back
    from cps import db
    dune = env.add_book("Dune", author="Unknown")
    helper = _setup(monkeypatch, _record(title="Dune", authors=["Frank Herbert"], description="Spice.",
                                         cover="https://covers.example/x.jpg"))

    def request_elsewhere():
        session = db.CalibreDB.session_factory()
        session.query(db.Books).first()
        db.CalibreDB.session_factory.remove()

    def slow_cover(url, path):
        thread = threading.Thread(target=request_elsewhere)
        thread.start()
        thread.join()
        return False, "no cover"
    monkeypatch.setattr(helper.helper, "save_cover_from_url", slow_cover)
    assert helper.fetch_and_apply_metadata(dune) is True
    assert _q(env, "SELECT b.author_sort, a.name FROM books b JOIN books_authors_link l ON l.book=b.id "
                   "JOIN authors a ON a.id=l.author") == [("Herbert, Frank", "Frank Herbert")]
    assert _q(env, "SELECT text FROM comments") == [("Spice.",)]


def test_an_imported_book_that_changes_moves_its_folder_and_queues_the_file_write(env, monkeypatch, tmp_path):
    # The same follow-up as Rebuild: a paper imported as "1706.03762v7" by "Unknown"
    from cps import metadata_helper
    paper = env.add_book("1706.03762v7", author="Unknown")
    old_dir = env.library_dir / "Unknown" / "1706.03762v7"
    old_dir.mkdir(parents=True)
    (old_dir / "1706.03762v7.epub").write_bytes(b"epub")
    logs = tmp_path / "change_logs"
    logs.mkdir()
    monkeypatch.setattr(metadata_helper, "CHANGE_LOGS_DIR", str(logs))
    found = _record(title="Attention Is All You Need", authors=["Ashish Vaswani"],
                    identifiers={"arxiv": "1706.03762"})
    helper = _setup(monkeypatch, by_id=[found], id_types={"arxiv"},
                    settings={"auto_metadata_fetch_enabled": 1, "auto_metadata_enforcement": 1})
    with env.app.test_request_context():
        assert helper.fetch_and_apply_metadata(paper) is True
    [(path,)] = _q(env, "SELECT path FROM books")
    assert path == "Ashish Vaswani/Attention Is All You Need (%d)" % paper
    assert (env.library_dir / path).is_dir() and not old_dir.exists()
    [log] = list(logs.iterdir())
    assert '"title": "Attention Is All You Need"' in log.read_text()
