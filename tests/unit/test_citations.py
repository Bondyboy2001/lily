"""Book page: arXiv and DOI links, and the citation count from OpenAlex."""

import pytest

from cps import db
from cps.services import citations

pytestmark = pytest.mark.unit


def ids(**values):
    return [db.Identifiers(val, key, 1) for key, val in values.items()]


def test_arxiv_identifier_links_to_its_abstract_page():
    arxiv = ids(arxiv="2601.22106")[0]
    assert arxiv.format_type() == "arXiv"
    assert repr(arxiv) == "https://arxiv.org/abs/2601.22106"


@pytest.mark.parametrize("given, expected", [
    ({"arxiv": "2601.22106"}, ("10.48550/arXiv.2601.22106", "2601.22106")),
    ({"arxiv": "1706.03762", "doi": "10.65215/x"}, ("10.65215/x", "1706.03762")),
    ({"doi": "10.48550/arXiv.1706.03762"}, ("10.48550/arXiv.1706.03762", "1706.03762")),
    ({"doi": "10.1038/nature14539"}, ("10.1038/nature14539", "")),
    ({"isbn": "9780441172719"}, ("", "")),
])
def test_paper_ids(given, expected):
    assert citations.paper_ids(ids(**given)) == expected


def test_book_without_paper_ids_never_asks_openalex(monkeypatch):
    monkeypatch.setattr(citations, "_lookup", lambda *a: pytest.fail("looked up"))
    assert citations.citation_count(ids(isbn="9780441172719")) is None


def test_count_is_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(citations, "_cache", {})
    monkeypatch.setattr(citations, "_lookup", lambda doi, arxiv: calls.append(doi) or {"count": 3, "url": "u"})
    for _ in range(2):
        assert citations.citation_count(ids(arxiv="2601.22106")) == {"count": 3, "url": "u"}
    assert calls == ["10.48550/arXiv.2601.22106"]


def test_unreachable_openalex_gives_no_count(monkeypatch):
    def fail(*a):
        raise citations.requests.ConnectionError("down")
    monkeypatch.setattr(citations, "_cache", {})
    monkeypatch.setattr(citations, "_lookup", fail)
    assert citations.citation_count(ids(arxiv="2601.22106")) is None
