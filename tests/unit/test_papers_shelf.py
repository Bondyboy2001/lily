"""The Papers shelf: one shared shelf that papers are filed on when their metadata is
fetched (edit page, imports, Rebuild metadata), and once for the papers already there."""
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


@pytest.mark.parametrize("ids, source, paper", [
    ({"arxiv": "2401.00001"}, None, True),
    ({"doi": "10.1214/aos/1176344136"}, None, True),           # a Project Euclid DOI
    ({"doi": "10.1007/978-3-030-1", "isbn": "9783030000000"}, None, False),
    ({"isbn": "9780262046305"}, None, False),
    ({}, None, False),
    ({"ARXIV": "2401.00001"}, None, True),
    ({"doi": ""}, None, False),
    ({}, "googlescholar", True),
    ({"isbn": "9780262046305"}, "google", False),
])
def test_what_counts_as_a_paper(ids, source, paper):
    from cps.services.papers_shelf import is_paper
    assert is_paper(ids, source) is paper


def test_filing_makes_one_public_papers_shelf_and_adds_each_book_once(env):
    from cps.services import papers_shelf
    a, b = env.add_book("A Paper"), env.add_book("B Paper")
    assert papers_shelf.file_papers([a]) == 1
    assert papers_shelf.file_papers([a, b]) == 1
    assert papers_shelf.file_papers([b]) == 0
    assert _shelved(env) == {"Papers": (1, env.admin().id, [a, b])}


def test_filing_reuses_a_papers_shelf_already_there_in_any_case(env):
    from cps import ub
    from cps.services import papers_shelf
    ub.session.add(ub.Shelf(name="papers", is_public=0, user_id=env.admin().id))
    ub.session.commit()
    book = env.add_book("A Paper")
    papers_shelf.file_papers([book])
    assert _shelved(env) == {"papers": (0, env.admin().id, [book])}


def test_a_public_papers_shelf_wins_over_a_private_one(env):
    from cps import ub
    from cps.services import papers_shelf
    other = env.add_user("reader")
    ub.session.add_all([ub.Shelf(name="Papers", is_public=0, user_id=other.id),
                        ub.Shelf(name="Papers", is_public=1, user_id=env.admin().id)])
    ub.session.commit()
    book = env.add_book("A Paper")
    papers_shelf.file_papers([book])
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
    rec = SimpleNamespace(title=title, authors=["Test Author"], description="", publisher="", tags=[],
                          series="", series_index=0, publishedDate=None, rating=None, identifiers={},
                          cover="", source=SimpleNamespace(id=source_id, description="Crossref"))
    rec.__dict__.update(kw)
    return rec


def test_an_automatic_fetch_from_scholar_files_the_paper(env, monkeypatch):
    book = env.add_book("Annals Paper")
    helper = _fetch_setup(monkeypatch, _record("Annals Paper", "googlescholar",
                                               identifiers={"doi": "10.1214/aos/1176344136"}))
    helper.fetch_and_apply_metadata(book)
    assert _shelved(env)["Papers"][2] == [book]


def test_an_automatic_fetch_of_a_book_files_nothing(env, monkeypatch):
    book = env.add_book("A Novel")
    helper = _fetch_setup(monkeypatch, _record("A Novel", "google", identifiers={"isbn": "9780262046305"}))
    helper.fetch_and_apply_metadata(book)
    assert _shelved(env) == {}


def test_a_paper_already_identified_is_filed_even_with_no_match(env, monkeypatch):
    book = env.add_book("2401.00001")
    _identify(env, book, arxiv="2401.00001")
    helper = _fetch_setup(monkeypatch, _record("Something Else Entirely", "googlescholar"))
    assert helper.fetch_and_apply_metadata(book) is False
    assert _shelved(env)["Papers"][2] == [book]


def test_a_shelf_failure_never_fails_the_fetch(env, monkeypatch):
    from cps.services import papers_shelf
    book = env.add_book("Annals Paper")
    helper = _fetch_setup(monkeypatch, _record("Annals Paper", "googlescholar", publisher="IMS"))

    def broken(ids):
        raise RuntimeError("app.db is locked")
    monkeypatch.setattr(papers_shelf, "file_papers", broken)
    assert helper.fetch_and_apply_metadata(book) is True


def test_the_backfill_files_the_papers_already_there_once(env, tmp_path):
    from cps.services import papers_shelf
    arxiv, euclid, textbook, novel = (env.add_book(t) for t in ("Arxiv", "Euclid", "Textbook", "Novel"))
    _identify(env, arxiv, arxiv="2401.00001")
    _identify(env, euclid, doi="10.1214/aos/1176344136")
    _identify(env, textbook, doi="10.1007/978-3-030-1", isbn="9783030000000")
    _identify(env, novel, isbn="9780262046305")
    marker = tmp_path / "papers_shelf_v1"
    assert papers_shelf.backfill_once(str(marker)) == 2
    assert _shelved(env)["Papers"][2] == [arxiv, euclid]
    assert marker.exists()

    # Taken off by hand, it stays off: the backfill has run.
    from cps import ub
    ub.session.query(ub.BookShelf).filter(ub.BookShelf.book_id == arxiv).delete()
    ub.session.commit()
    assert papers_shelf.backfill_once(str(marker)) == 0
    assert _shelved(env)["Papers"][2] == [euclid]


def test_the_edit_pages_papers_chip_uses_the_shared_shelf(env):
    from cps.cw_login import login_user
    from cps import editbooks, ub
    book = env.add_book("A Paper")
    reader = env.add_user("reader")
    ub.session.add(ub.Shelf(name="Papers", is_public=1, user_id=env.admin().id))
    ub.session.commit()
    with env.app.test_request_context():
        login_user(reader)
        editbooks._update_shelves(book, {"shelves_present": "1", "shelves": '["Papers"]'})
    # The reader can't edit the admin's public shelf, and gets no private Papers shelf of their own.
    assert _shelved(env) == {"Papers": (1, env.admin().id, [])}

    with env.app.test_request_context():
        login_user(env.admin())
        editbooks._update_shelves(book, {"shelves_present": "1", "shelves": '["Papers"]'})
    assert _shelved(env) == {"Papers": (1, env.admin().id, [book])}


def test_the_edit_pages_papers_chip_makes_the_shared_shelf(env):
    from cps.cw_login import login_user
    from cps import editbooks
    book = env.add_book("A Paper")
    with env.app.test_request_context():
        login_user(env.admin())
        editbooks._update_shelves(book, {"shelves_present": "1", "shelves": '["Papers"]'})
    assert _shelved(env) == {"Papers": (1, env.admin().id, [book])}
