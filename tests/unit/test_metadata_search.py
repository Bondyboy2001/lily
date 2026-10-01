"""Fetch Metadata: what the search box recognises as an identifier, who gets asked,
and arXiv lookups."""

import pytest

from cps.services.identifiers import arxiv_id_from_doi, parse_identifier

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("query, expected", [
    ("978-0-441-17271-9", {"isbn": "9780441172719"}),
    ("10.1038/nature14539", {"doi": "10.1038/nature14539"}),
    ("https://doi.org/10.1038/nature14539", {"doi": "10.1038/nature14539"}),
    ("10.48550/arXiv.1706.03762", {"doi": "10.48550/arXiv.1706.03762"}),
    ("1706.03762", {"arxiv": "1706.03762"}),
    ("arXiv:1706.03762v7", {"arxiv": "1706.03762"}),
    ("https://arxiv.org/abs/1706.03762", {"arxiv": "1706.03762"}),
    ("https://arxiv.org/pdf/1706.03762v2.pdf", {"arxiv": "1706.03762"}),
    ("hep-th/9901001", {"arxiv": "hep-th/9901001"}),
    ("math.AG/0309136", {"arxiv": "math.AG/0309136"}),
    ("hardcover-id:12345", {"hardcover-id": "12345"}),
])
def test_identifier_typed_into_the_search_box_is_recognised(query, expected):
    assert parse_identifier(query) == expected


@pytest.mark.parametrize("query", ["Attention Is All You Need", "Dune Frank Herbert", "", "1984"])
def test_ordinary_text_is_not_an_identifier(query):
    assert parse_identifier(query) == {}


def test_arxiv_doi_names_the_arxiv_id():
    assert arxiv_id_from_doi("10.48550/ARXIV.1706.03762") == "1706.03762"
    assert arxiv_id_from_doi("10.1038/nature14539") == ""


def test_books_identifiers_are_normalised():
    from cps.search_metadata import _form_identifiers
    assert _form_identifiers('{"ISBN": "978-0-441-17271-9", "doi": " 10.1/x ", "asin": ""}') == {
        "isbn": "9780441172719", "doi": "10.1/x"}
    assert _form_identifiers("not json") == {}


def test_typed_identifier_goes_to_the_providers_that_handle_its_type():
    from cps.search_metadata import _providers_to_ask, cl
    ask = {c.__id__ for c in _providers_to_ask({"doi": "10.48550/arXiv.1706.03762"}, cl)}
    assert ask == {"googlescholar"}
    ask = {c.__id__ for c in _providers_to_ask({"isbn": "9780441172719"}, cl)}
    assert ask == {"google", "openlibrary"}


def _scholar_calls(monkeypatch, identifiers):
    from cps.metadata_provider.scholar import google_scholar
    scholar = google_scholar()
    calls = []
    monkeypatch.setattr(scholar, "_search_arxiv", lambda q: calls.append(("arxiv", q)) or [])
    monkeypatch.setattr(scholar, "_search_crossref", lambda q: calls.append(("crossref", q)) or [])
    scholar.search_identifiers(identifiers)
    return calls


def test_arxiv_doi_is_looked_up_on_arxiv_not_crossref(monkeypatch):
    assert _scholar_calls(monkeypatch, {"doi": "10.48550/arXiv.1706.03762"}) == [("arxiv", "1706.03762")]


def test_journal_doi_still_goes_to_crossref(monkeypatch):
    assert _scholar_calls(monkeypatch, {"doi": "10.1038/nature14539"}) == [("crossref", "10.1038/nature14539")]


def test_arxiv_entry_without_journal_doi_gets_arxivs_doi():
    from xml.etree import ElementTree
    from cps.metadata_provider.scholar import google_scholar
    entry = ElementTree.fromstring(
        '<entry xmlns="http://www.w3.org/2005/Atom">'
        "<id>http://arxiv.org/abs/hep-th/9901001v1</id><title>A paper</title>"
        "<author><name>A. Author</name></author></entry>")
    record = google_scholar()._parse_arxiv_entry(entry)
    assert record.identifiers == {"arxiv": "hep-th/9901001", "doi": "10.48550/arXiv.hep-th/9901001"}


def test_a_text_search_asks_every_enabled_provider():
    from cps.search_metadata import _providers_to_ask, cl
    assert _providers_to_ask({}, cl) == list(cl)
