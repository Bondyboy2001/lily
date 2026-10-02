"""The Scholar provider: arXiv ids looked up from the abstract page, and outages
reported as failures rather than as no results."""

import pytest
import requests

from cps.metadata_provider import scholar as scholar_module
from cps.metadata_provider.scholar import google_scholar
from cps.services.identifiers import ARXIV_ID_RE

ABS_PAGE = """<html><head>
<meta name="citation_title" content="Copula Gaussian graphical models and their
  application to modeling functional disability data" />
<meta name="citation_author" content="Dobra, Adrian" />
<meta name="citation_author" content="Lenkoski, Alex" />
<meta name="citation_doi" content="10.1214/10-AOAS397" />
<meta name="citation_date" content="2011/08/08" />
<meta name="citation_arxiv_id" content="1108.1680" />
<meta name="citation_abstract" content="We propose a  Bayesian approach &amp; more." />
</head><body><table><tr>
<td class="tablecell subjects">
  <span class="primary-subject">Applications (stat.AP)</span>; Methodology (stat.ME)</td>
</tr></table></body></html>"""


class _Response:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text
        self.content = text.encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def _fake_get(routes):
    """requests.get answering by URL prefix; a route that's an exception is raised."""
    def get(url, **kwargs):
        for prefix, answer in routes.items():
            if url.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise AssertionError("unexpected request to " + url)
    return get


def test_arxiv_id_is_read_from_the_abstract_page():
    record = google_scholar()._parse_arxiv_abs("1108.1680", ABS_PAGE)
    assert record.title == ("Copula Gaussian graphical models and their application "
                            "to modeling functional disability data")
    assert record.authors == ["Adrian Dobra", "Alex Lenkoski"]
    assert record.description == "We propose a Bayesian approach & more."
    assert record.publishedDate == "2011-08-08"
    assert record.tags == ["stat.AP", "stat.ME"]
    assert record.identifiers == {"arxiv": "1108.1680", "doi": "10.1214/10-AOAS397"}


def test_arxiv_id_lookup_does_not_wait_on_the_api(monkeypatch):
    monkeypatch.setattr(scholar_module.requests, "get", _fake_get({
        "https://arxiv.org/abs/1108.1680": _Response(text=ABS_PAGE),
        "https://export.arxiv.org/": requests.Timeout("API hangs"),
    }))
    records = google_scholar().search_identifiers({"arxiv": "1108.1680"})
    assert [r.id for r in records] == ["1108.1680"]


def test_unknown_arxiv_id_is_no_result_not_an_error(monkeypatch):
    monkeypatch.setattr(scholar_module.requests, "get", _fake_get({
        "https://arxiv.org/abs/": _Response(404),
    }))
    assert google_scholar()._search_arxiv("2999.99999") == []


def test_arxiv_outage_is_reported_as_a_failure(monkeypatch):
    monkeypatch.setattr(scholar_module.requests, "get", _fake_get({
        "https://arxiv.org/abs/": requests.ConnectionError("down"),
        "https://export.arxiv.org/": requests.Timeout("API hangs"),
    }))
    with pytest.raises(requests.RequestException):
        google_scholar().search_identifiers({"arxiv": "1108.1680"})


def test_text_search_shows_crossref_when_arxiv_is_down(monkeypatch):
    scholar = google_scholar()
    record = scholar._parse_arxiv_abs("1108.1680", ABS_PAGE)
    monkeypatch.setattr(scholar, "_search_arxiv", lambda q: (_ for _ in ()).throw(requests.Timeout()))
    monkeypatch.setattr(scholar, "_search_crossref", lambda q: [record])
    assert scholar.search("copula graphical models") == [record]


def test_text_search_fails_when_every_source_is_down(monkeypatch):
    scholar = google_scholar()
    monkeypatch.setattr(scholar, "_search_arxiv", lambda q: (_ for _ in ()).throw(requests.Timeout()))
    monkeypatch.setattr(scholar, "_search_crossref", lambda q: (_ for _ in ()).throw(requests.Timeout()))
    with pytest.raises(requests.Timeout):
        scholar.search("copula graphical models")


def test_versioned_id_keeps_the_bare_id(monkeypatch):
    monkeypatch.setattr(scholar_module.requests, "get", _fake_get({
        "https://arxiv.org/abs/1108.1680v1": _Response(text=ABS_PAGE),
    }))
    records = google_scholar()._fetch_arxiv_abs(ARXIV_ID_RE.search("1108.1680v1"))
    assert records[0].id == "1108.1680"
