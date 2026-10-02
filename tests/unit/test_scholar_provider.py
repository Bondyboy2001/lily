"""The Scholar provider: arXiv ids looked up from the abstract page, titles
searched through DataCite, and outages reported as failures rather than as no
results."""

import json

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


# Trimmed from api.datacite.org/dois?client-id=arxiv.content
DATACITE_HIT = {
    "id": "10.48550/arxiv.1108.1680",
    "attributes": {
        "doi": "10.48550/arxiv.1108.1680",
        "identifiers": [{"identifier": "1108.1680", "identifierType": "arXiv"}],
        "creators": [
            {"name": "Dobra, Adrian", "nameType": "Personal",
             "givenName": "Adrian", "familyName": "Dobra"},
            {"name": "Lenkoski, Alex", "nameType": "Personal",
             "givenName": "Alex", "familyName": "Lenkoski"},
        ],
        "titles": [{"title": "Copula Gaussian graphical models and their\n  application "
                             "to modeling functional disability data"}],
        "publisher": "arXiv",
        "publicationYear": 2011,
        "subjects": [
            {"subject": "Applications (stat.AP)", "subjectScheme": "arXiv"},
            {"subject": "Methodology (stat.ME)", "subjectScheme": "arXiv"},
            {"subject": "FOS: Mathematics",
             "subjectScheme": "Fields of Science and Technology (FOS)"},
        ],
        "dates": [
            {"date": "2011-08-11T12:00:00Z", "dateType": "Updated"},
            {"date": "2011-08-08T19:45:12Z", "dateType": "Submitted", "dateInformation": "v1"},
            {"date": "2011", "dateType": "Issued"},
        ],
        "relatedIdentifiers": [{"relationType": "IsVersionOf",
                                "relatedIdentifier": "10.1214/10-aoas397",
                                "relatedIdentifierType": "DOI"}],
        "descriptions": [
            {"description": "We propose a  Bayesian approach & more.",
             "descriptionType": "Abstract"},
            {"description": "Published in the Annals of Applied Statistics",
             "descriptionType": "Other"},
        ],
    },
}


class _Response:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text
        self.content = text.encode()

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def _fake_get(routes, calls=None):
    """requests.get answering by URL prefix; a route that's an exception is raised.
    Each request's url and params are appended to `calls` when given."""
    def get(url, **kwargs):
        if calls is not None:
            calls.append((url, kwargs.get("params")))
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


def test_title_is_searched_in_datacite_not_the_arxiv_api(monkeypatch):
    # The arXiv API rate-limits and times out, so a typed title found nothing
    calls = []
    monkeypatch.setattr(scholar_module.requests, "get", _fake_get({
        "https://api.datacite.org/dois": _Response(text=json.dumps({"data": [DATACITE_HIT]})),
    }, calls))
    records = google_scholar()._search_arxiv("Copula Gaussian graphical models")
    assert [r.id for r in records] == ["1108.1680"]
    (url, params), = calls
    assert params["client-id"] == "arxiv.content"
    assert params["sort"] == "relevance"


def test_datacite_query_prefers_the_exact_title():
    query = google_scholar._datacite_query('Adam: A "Method"  for  Optimization')
    assert query == ('titles.title:"Adam: A \\"Method\\" for Optimization"^5'
                     ' OR titles.title:(adam AND a AND method AND for AND optimization)')


def test_datacite_record_reads_like_the_abstract_page():
    record = google_scholar()._parse_datacite_hit(DATACITE_HIT)
    assert record.id == "1108.1680"
    assert record.title == ("Copula Gaussian graphical models and their application "
                            "to modeling functional disability data")
    assert record.authors == ["Adrian Dobra", "Alex Lenkoski"]
    assert record.description == "We propose a Bayesian approach & more."
    assert record.publishedDate == "2011-08-08"
    assert record.publisher == "arXiv"
    assert record.url == "https://arxiv.org/abs/1108.1680"
    assert record.tags == ["stat.AP", "stat.ME"]
    assert record.identifiers == {"arxiv": "1108.1680", "doi": "10.1214/10-aoas397"}


def test_datacite_record_without_a_journal_keeps_the_arxiv_doi():
    hit = json.loads(json.dumps(DATACITE_HIT))
    hit["attributes"]["relatedIdentifiers"] = []
    record = google_scholar()._parse_datacite_hit(hit)
    assert record.identifiers["doi"] == "10.48550/arXiv.1108.1680"
