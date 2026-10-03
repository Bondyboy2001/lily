"""The arXiv shelf: one shared shelf that a paper is filed on when its metadata is fetched
from arXiv (edit page, imports, Rebuild metadata). It replaced the Papers shelf, which a
one-time pass removes after shelving the arXiv papers already in the library."""
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


def _identify(env, book_id, **ids):
    con = sqlite3.connect(env.library_dir / "metadata.db")
    for kind, val in ids.items():
        con.execute("INSERT INTO identifiers (book, type, val) VALUES (?, ?, ?)", (book_id, kind, val))
    con.commit()
    con.close()


def _shelved(env):
    """{shelf name: (is_public, owner id, [book ids in order])}"""
    from cps import ub
    ub.session.expire_all()
    return {s.name: (s.is_public, s.user_id,
                     [link.book_id for link in s.books.order_by(ub.BookShelf.order)])
            for s in ub.session.query(ub.Shelf)}


def test_filing_makes_one_public_arxiv_shelf_and_adds_each_book_once(env):
    from cps.services import arxiv_shelf
    a, b = env.add_book("A Paper"), env.add_book("B Paper")
    assert arxiv_shelf.file_on_shelf([a]) == 1
    assert arxiv_shelf.file_on_shelf([a, b]) == 1
    assert arxiv_shelf.file_on_shelf([b]) == 0
    assert _shelved(env) == {"arXiv": (1, env.admin().id, [a, b])}


def test_filing_reuses_an_arxiv_shelf_already_there_in_any_case(env):
    from cps import ub
    from cps.services import arxiv_shelf
    ub.session.add(ub.Shelf(name="Arxiv", is_public=0, user_id=env.admin().id))
    ub.session.commit()
    book = env.add_book("A Paper")
    arxiv_shelf.file_on_shelf([book])
    assert _shelved(env) == {"Arxiv": (0, env.admin().id, [book])}


def test_a_public_arxiv_shelf_wins_over_a_private_one(env):
    from cps import ub
    from cps.services import arxiv_shelf
    other = env.add_user("reader")
    ub.session.add_all([ub.Shelf(name="arXiv", is_public=0, user_id=other.id),
                        ub.Shelf(name="arXiv", is_public=1, user_id=env.admin().id)])
    ub.session.commit()
    book = env.add_book("A Paper")
    arxiv_shelf.file_on_shelf([book])
    shelves = {(s.is_public, tuple(link.book_id for link in s.books)) for s in ub.session.query(ub.Shelf)}
    assert shelves == {(0, ()), (1, (book,))}


def _fetch_setup(monkeypatch, record):
    from cps import metadata_helper
    settings = {"auto_metadata_fetch_enabled": 1}
    monkeypatch.setattr(metadata_helper, "CWA_DB", lambda: SimpleNamespace(get_cwa_settings=lambda: settings))
    monkeypatch.setattr(metadata_helper, "pdf_first_page_text", lambda book: "")
    provider = FakeProvider(__id__=record.source.id, __name__="Fake", identifier_types=frozenset(),
                            search_identifiers=lambda ids, *a: [], search=lambda q, *a: [record])
    monkeypatch.setattr(metadata_helper, "metadata_providers", [provider])
    return metadata_helper


def _record(title, source_id, **kw):
    rec = SimpleNamespace(title=title, authors=["Test Author"], description="", tags=[],
                          series="", series_index=0, publishedDate=None, identifiers={},
                          cover="", source=SimpleNamespace(id=source_id, description="arXiv"))
    rec.__dict__.update(kw)
    return rec


def test_an_automatic_fetch_from_arxiv_files_the_paper(env, monkeypatch):
    book = env.add_book("Attention Is All You Need", fmt="PDF")
    helper = _fetch_setup(monkeypatch, _record("Attention Is All You Need", "googlescholar",
                                               identifiers={"ARXIV": "1706.03762"}))
    helper.fetch_and_apply_metadata(book)
    assert _shelved(env) == {"arXiv": (1, env.admin().id, [book])}


def test_a_paper_fetched_from_elsewhere_is_not_filed(env, monkeypatch):
    book = env.add_book("Annals Paper", fmt="PDF")
    helper = _fetch_setup(monkeypatch, _record("Annals Paper", "googlescholar",
                                               identifiers={"doi": "10.1214/aos/1176344136"}))
    assert helper.fetch_and_apply_metadata(book) is True
    assert _shelved(env) == {}


def test_an_automatic_fetch_of_a_book_files_nothing(env, monkeypatch):
    book = env.add_book("A Novel")
    helper = _fetch_setup(monkeypatch, _record("A Novel", "google", identifiers={"isbn": "9780262046305"}))
    helper.fetch_and_apply_metadata(book)
    assert _shelved(env) == {}


def test_a_paper_arxiv_did_not_answer_for_is_not_filed(env, monkeypatch):
    book = env.add_book("2401.00001")
    _identify(env, book, arxiv="2401.00001")
    helper = _fetch_setup(monkeypatch, _record("Something Else Entirely", "googlescholar"))
    assert helper.fetch_and_apply_metadata(book) is False
    assert _shelved(env) == {}


def test_a_shelf_failure_never_fails_the_fetch(env, monkeypatch):
    from cps.services import arxiv_shelf
    book = env.add_book("A Paper", fmt="PDF")
    helper = _fetch_setup(monkeypatch, _record("A Paper", "googlescholar",
                                               identifiers={"arxiv": "2401.00001"}))

    def broken(ids):
        raise RuntimeError("app.db is locked")
    monkeypatch.setattr(arxiv_shelf, "file_on_shelf", broken)
    assert helper.fetch_and_apply_metadata(book) is True


def test_the_arxiv_shelf_takes_over_from_the_papers_shelf_once(env, tmp_path):
    from cps import ub
    from cps.services import arxiv_shelf
    arxiv, euclid, novel = (env.add_book(t) for t in ("Arxiv", "Euclid", "Novel"))
    _identify(env, arxiv, arxiv="2401.00001")
    _identify(env, euclid, doi="10.1214/aos/1176344136")
    _identify(env, novel, isbn="9780262046305")
    # What the Papers shelf held: every paper, arXiv's or not
    papers = ub.Shelf(name="Papers", is_public=1, user_id=env.admin().id)
    reading = ub.Shelf(name="To Read", is_public=0, user_id=env.admin().id)
    ub.session.add_all([papers, reading])
    ub.session.flush()
    for order, book_id in enumerate((arxiv, euclid), 1):
        papers.books.append(ub.BookShelf(shelf=papers.id, book_id=book_id, order=order))
    reading.books.append(ub.BookShelf(shelf=reading.id, book_id=novel, order=1))
    ub.session.commit()

    marker = tmp_path / "arxiv_shelf_v1"
    assert arxiv_shelf.replace_papers_shelf_once(str(marker)) == 1
    assert _shelved(env) == {"arXiv": (1, env.admin().id, [arxiv]), "To Read": (0, env.admin().id, [novel])}
    assert ub.session.query(ub.BookShelf).count() == 2
    assert marker.exists()

    # Taken off by hand, it stays off, and a Papers shelf made since is the user's own
    ub.session.query(ub.BookShelf).filter(ub.BookShelf.book_id == arxiv).delete()
    ub.session.add(ub.Shelf(name="Papers", is_public=0, user_id=env.admin().id))
    ub.session.commit()
    assert arxiv_shelf.replace_papers_shelf_once(str(marker)) == 0
    assert _shelved(env) == {"arXiv": (1, env.admin().id, []), "To Read": (0, env.admin().id, [novel]),
                             "Papers": (0, env.admin().id, [])}


def test_with_no_arxiv_papers_the_papers_shelf_just_goes(env, tmp_path):
    from cps import ub
    from cps.services import arxiv_shelf
    book = env.add_book("Euclid")
    papers = ub.Shelf(name="Papers", is_public=1, user_id=env.admin().id)
    ub.session.add(papers)
    ub.session.flush()
    papers.books.append(ub.BookShelf(shelf=papers.id, book_id=book, order=1))
    ub.session.commit()
    assert arxiv_shelf.replace_papers_shelf_once(str(tmp_path / "arxiv_shelf_v1")) == 0
    assert _shelved(env) == {}


def test_the_startup_pass_leaves_the_apps_library_session_as_it_was(env, tmp_path):
    """It runs at startup on the thread that serves requests. Borrowing that thread's session
    turned off its expire-on-commit, and the next save of a book just saved read the book as
    it was before, then failed adding an identifier it already had."""
    from cps import calibre_db
    from cps.services import arxiv_shelf
    # As at startup: the app's session is the one its thread is handed
    calibre_db.session = calibre_db.session_factory()
    calibre_db.session.expire_on_commit = True
    _identify(env, env.add_book("Arxiv"), arxiv="2401.00001")
    assert arxiv_shelf.replace_papers_shelf_once(str(tmp_path / "arxiv_shelf_v1")) == 1
    assert calibre_db.session.expire_on_commit is True


def test_the_edit_pages_arxiv_chip_uses_the_shared_shelf(env):
    from cps.cw_login import login_user
    from cps import editbooks, ub
    book = env.add_book("A Paper")
    reader = env.add_user("reader")
    ub.session.add(ub.Shelf(name="arXiv", is_public=1, user_id=env.admin().id))
    ub.session.commit()
    with env.app.test_request_context():
        login_user(reader)
        editbooks._update_shelves(book, {"shelves_present": "1", "shelves": '["arXiv"]'})
    # The reader can't edit the admin's public shelf, and gets no private arXiv shelf of their own.
    assert _shelved(env) == {"arXiv": (1, env.admin().id, [])}

    with env.app.test_request_context():
        login_user(env.admin())
        editbooks._update_shelves(book, {"shelves_present": "1", "shelves": '["arXiv"]'})
    assert _shelved(env) == {"arXiv": (1, env.admin().id, [book])}


def test_the_edit_pages_arxiv_chip_makes_the_shared_shelf(env):
    from cps.cw_login import login_user
    from cps import editbooks
    book = env.add_book("A Paper")
    with env.app.test_request_context():
        login_user(env.admin())
        editbooks._update_shelves(book, {"shelves_present": "1", "shelves": '["arxiv"]'})
    assert _shelved(env) == {"arXiv": (1, env.admin().id, [book])}


def test_fetch_metadata_adds_the_arxiv_chip_for_a_result_with_an_arxiv_id():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2] / "cps/static/js/get_meta.js").read_text(encoding="utf-8")
    assert "book.identifiers.arxiv" in js and 'names.push("arXiv")' in js
