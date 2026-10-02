"""Fetch metadata for new books: a paper is looked up by the arXiv id or DOI in its
title or on its PDF's first page, since a PDF often imports under its file name, and
a book by its own ISBN; a result found that way must still be this book."""
import sqlite3
from types import SimpleNamespace

import pytest

from .lily_env import lily_env
from .metadata_fakes import FakeProvider

pytestmark = pytest.mark.unit

ARXIV_PAGE = """Attention Is All You Need
Ashish Vaswani Noam Shazeer
arXiv:1706.03762v7  [cs.CL]  2 Aug 2023"""


@pytest.mark.parametrize("title, page, expected", [
    ("1706.03762v7", "", {"arxiv": "1706.03762"}),
    ("arXiv:hep-th/9901001v1  1 Jan 1999", "", {"arxiv": "hep-th/9901001"}),
    ("Untitled", ARXIV_PAGE, {"arxiv": "1706.03762"}),
    ("Copula models", "Ann. Appl. Stat. doi: 10.1214/10-AOAS397.\narXiv:1108.1680v1  [stat.AP]  8 Aug 2011",
     {"arxiv": "1108.1680", "doi": "10.1214/10-AOAS397"}),
    # Another paper cited in the abstract: no version and date, so not this paper's stamp
    ("Graphs", "We extend the transformer of Vaswani et al. (arXiv:1706.03762) to graphs", {}),
    # arXiv's own DOI names the arXiv id
    ("Untitled", "https://doi.org/10.48550/arXiv.2601.22106", {"doi": "10.48550/arXiv.2601.22106",
                                                                "arxiv": "2601.22106"}),
    ("A journal paper", "Nature 529, 484 (2016) https://doi.org/10.1038/nature16961)",
     {"doi": "10.1038/nature16961"}),
    ("Dune", "In the week before their departure to Arrakis", {}),
    ("Report 2019.12345 annual", "", {}),
])
def test_paper_identifiers_are_found_in_the_title_and_first_page(title, page, expected):
    from cps.metadata_helper import find_paper_identifiers
    assert find_paper_identifiers(title, page) == expected


def test_the_books_own_paper_identifiers_come_first():
    from cps.metadata_helper import find_paper_identifiers
    assert find_paper_identifiers("1706.03762", "", {"doi": "10.1/own", "isbn": "123"}) == {
        "arxiv": "1706.03762", "doi": "10.1/own"}


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as env:
        yield env


def _setup(monkeypatch, providers, page=""):
    from cps import metadata_helper
    applied = []
    monkeypatch.setattr(metadata_helper, "metadata_providers", providers)
    settings = {"auto_metadata_fetch_enabled": 1}
    monkeypatch.setattr(metadata_helper, "CWA_DB", lambda: SimpleNamespace(get_cwa_settings=lambda: settings))
    monkeypatch.setattr(metadata_helper, "pdf_first_page_text", lambda book: page)
    monkeypatch.setattr(metadata_helper, "_apply_record",
                        lambda cdb, book, record, cover: applied.append(record.title) or True)
    return metadata_helper, applied


def _provider(pid, id_types, by_id=(), by_text=(), calls=None):
    calls = calls if calls is not None else []
    return FakeProvider(
        __id__=pid, __name__=pid, identifier_types=frozenset(id_types),
        search_identifiers=lambda ids, *a: calls.append((pid, "ids", ids)) or list(by_id),
        search=lambda q, *a: calls.append((pid, "text", q)) or list(by_text))


def test_pdf_named_by_its_arxiv_id_gets_the_papers_metadata(env, monkeypatch):
    calls = []
    paper = SimpleNamespace(title="Attention Is All You Need", authors=["Ashish Vaswani"],
                            identifiers={"arxiv": "1706.03762"})
    helper, applied = _setup(monkeypatch, [
        _provider("google", {"isbn"}, calls=calls),
        _provider("googlescholar", {"doi", "arxiv"}, by_id=[paper], calls=calls),
    ])
    book_id = env.add_book("1706.03762v7", author="Unknown", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is True
    # Found by its id, so its title needn't resemble the file name
    assert applied == ["Attention Is All You Need"]
    assert calls == [("googlescholar", "ids", {"arxiv": "1706.03762"})]


def test_arxiv_stamp_on_the_first_page_is_looked_up(env, monkeypatch):
    calls = []
    paper = SimpleNamespace(title="Attention Is All You Need", authors=[],
                            identifiers={"arxiv": "1706.03762", "doi": "10.48550/arXiv.1706.03762"})
    helper, applied = _setup(monkeypatch, [
        _provider("googlescholar", {"doi", "arxiv"}, by_id=[paper], calls=calls),
    ], page=ARXIV_PAGE)
    book_id = env.add_book("Microsoft Word - final.docx", author="Unknown", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is True
    assert calls[0] == ("googlescholar", "ids", {"arxiv": "1706.03762"})


def test_unknown_id_falls_back_to_the_title_search(env, monkeypatch):
    calls = []
    helper, applied = _setup(monkeypatch, [
        _provider("googlescholar", {"doi", "arxiv"}, calls=calls),
    ])
    book_id = env.add_book("1706.03762v7", author="Unknown", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is False
    assert [c[1] for c in calls] == ["ids", "text"]


def test_a_book_without_paper_ids_is_searched_by_title_as_before(env, monkeypatch):
    calls = []
    dune = SimpleNamespace(title="Dune", authors=["Frank Herbert"])
    helper, applied = _setup(monkeypatch, [
        _provider("google", {"isbn"}, by_text=[dune], calls=calls),
    ])
    book_id = env.add_book("Dune", author="Frank Herbert")
    assert helper.fetch_and_apply_metadata(book_id) is True
    assert calls == [("google", "text", "Dune Frank Herbert")] and applied == ["Dune"]


def _add_identifier(env, book_id, kind, value):
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.execute("INSERT INTO identifiers (book, type, val) VALUES (?, ?, ?)", (book_id, kind, value))
    con.commit()
    con.close()


def test_a_book_is_looked_up_by_its_own_isbn_first(env, monkeypatch):
    calls = []
    edition = SimpleNamespace(title="Dune: Deluxe Edition", authors=["Frank Herbert"], identifiers={})
    helper, applied = _setup(monkeypatch, [
        _provider("google", {"isbn"}, by_id=[edition], calls=calls),
    ])
    book_id = env.add_book("Dune", author="Frank Herbert")
    _add_identifier(env, book_id, "isbn", "978-0-441-17271-9")
    assert helper.fetch_and_apply_metadata(book_id) is True
    # Another edition's title is fine when the ISBN and author agree
    assert calls == [("google", "ids", {"isbn": "9780441172719"})]
    assert applied == ["Dune: Deluxe Edition"]


def test_an_isbn_naming_another_book_is_not_applied(env, monkeypatch):
    calls = []
    wrong = SimpleNamespace(title="Gardening for Beginners", authors=["Ann Other"], identifiers={})
    helper, applied = _setup(monkeypatch, [
        _provider("google", {"isbn"}, by_id=[wrong], calls=calls),
    ])
    book_id = env.add_book("Dune", author="Frank Herbert")
    _add_identifier(env, book_id, "isbn", "9780000000002")
    assert helper.fetch_and_apply_metadata(book_id) is False
    assert applied == [] and [c[1] for c in calls] == ["ids", "text"]


def test_a_doi_on_the_first_page_needs_the_papers_title_there_too(env, monkeypatch):
    # A first page can cite another paper's DOI
    page = "Mastering the game of Go with deep\nneural networks and tree search\ndoi:10.1038/nature16961"
    cited = SimpleNamespace(title="Some cited dataset", authors=[], identifiers={"doi": "10.1038/nature16961"})
    paper = SimpleNamespace(title="Mastering the Game of Go with Deep Neural Networks and Tree Search",
                            authors=[], identifiers={"doi": "10.1038/nature16961"})
    for found, expected in ((cited, []), (paper, [paper.title])):
        helper, applied = _setup(monkeypatch, [
            _provider("googlescholar", {"doi", "arxiv"}, by_id=[found]),
        ], page=page)
        book_id = env.add_book("untitled", author="Unknown", fmt="PDF")
        helper.fetch_and_apply_metadata(book_id)
        assert applied == expected
