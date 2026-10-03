"""The Scholar provider: arXiv ids looked up from the abstract page, titles
searched through DataCite and Semantic Scholar, and outages reported as failures
rather than as no results."""

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


# Trimmed from api.semanticscholar.org/graph/v1/paper/search/match
S2_MATCH = {"data": [{
    "paperId": "846aedd869a00c09b40f1f1f35673cb22bc87490",
    "externalIds": {"DOI": "10.1038/nature16961", "CorpusId": 515925},
    "url": "https://www.semanticscholar.org/paper/846aedd869a00c09b40f1f1f35673cb22bc87490",
    "title": "Mastering the game of Go with deep  neural networks and tree search",
    "venue": "Nature",
    "year": 2016,
    "fieldsOfStudy": ["Computer Science", "Medicine"],
    "publicationDate": "2016-01-27",
    "journal": {"name": "Nature", "pages": "484-489", "volume": "529"},
    "authors": [{"authorId": "145824029", "name": "David Silver"},
                {"authorId": "1885349", "name": "Aja Huang"}],
    "abstract": "The game of Go has  long been viewed as the most challenging.",
    "matchScore": 204.4,
}]}


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
    assert record.identifiers == {"arxiv": "1108.1680", "doi": "10.1214/10-AOAS397"}


def test_arxiv_id_is_read_from_datacite_first(monkeypatch):
    # arxiv.org is often slow; DataCite holds the same record under arXiv's DOI
    calls = []
    monkeypatch.setattr(scholar_module.http_session, "get", _fake_get({
        "https://api.datacite.org/dois/10.48550/arXiv.1108.1680":
            _Response(text=json.dumps({"data": DATACITE_HIT})),
    }, calls))
    records = google_scholar().search_identifiers({"arxiv": "1108.1680v2"})
    assert [r.title[:5] for r in records] == ["Copul"] and len(calls) == 1


def test_arxiv_id_missing_from_datacite_is_read_from_the_abstract_page(monkeypatch):
    # A paper from the last day or two isn't registered yet
    monkeypatch.setattr(scholar_module.http_session, "get", _fake_get({
        "https://api.datacite.org/": _Response(404),
        "https://arxiv.org/abs/1108.1680": _Response(text=ABS_PAGE),
        "https://export.arxiv.org/": requests.Timeout("API hangs"),
    }))
    records = google_scholar().search_identifiers({"arxiv": "1108.1680"})
    assert [r.id for r in records] == ["1108.1680"]


def test_unknown_arxiv_id_is_no_result_not_an_error(monkeypatch):
    monkeypatch.setattr(scholar_module.http_session, "get", _fake_get({
        "https://api.datacite.org/": _Response(404),
        "https://arxiv.org/abs/": _Response(404),
    }))
    assert google_scholar()._search_arxiv("2999.99999") == []


def test_arxiv_outage_is_reported_as_a_failure(monkeypatch):
    monkeypatch.setattr(scholar_module.http_session, "get", _fake_get({
        "https://api.datacite.org/": requests.ConnectionError("down"),
        "https://arxiv.org/abs/": requests.ConnectionError("down"),
        "https://export.arxiv.org/": requests.Timeout("API hangs"),
    }))
    with pytest.raises(requests.RequestException):
        google_scholar().search_identifiers({"arxiv": "1108.1680"})


def _down(q):
    raise requests.Timeout()


def test_text_search_shows_crossref_when_arxiv_is_down(monkeypatch):
    scholar = google_scholar()
    record = scholar._parse_arxiv_abs("1108.1680", ABS_PAGE)
    monkeypatch.setattr(scholar, "_search_arxiv", _down)
    monkeypatch.setattr(scholar, "_search_semantic_scholar", _down)
    monkeypatch.setattr(scholar, "_search_crossref", lambda q, covers=True: [record])
    assert scholar.search("copula graphical models") == [record]


def test_text_search_fails_when_every_source_is_down(monkeypatch):
    scholar = google_scholar()
    monkeypatch.setattr(scholar, "_search_arxiv", _down)
    monkeypatch.setattr(scholar, "_search_semantic_scholar", _down)
    monkeypatch.setattr(scholar, "_search_crossref", _down)
    with pytest.raises(requests.Timeout):
        scholar.search("copula graphical models")


def test_versioned_id_keeps_the_bare_id(monkeypatch):
    monkeypatch.setattr(scholar_module.http_session, "get", _fake_get({
        "https://arxiv.org/abs/1108.1680v1": _Response(text=ABS_PAGE),
    }))
    records = google_scholar()._fetch_arxiv_abs(ARXIV_ID_RE.search("1108.1680v1"))
    assert records[0].id == "1108.1680"


def test_title_is_searched_in_datacite_not_the_arxiv_api(monkeypatch):
    # The arXiv API rate-limits and times out, so a typed title found nothing
    calls = []
    monkeypatch.setattr(scholar_module.http_session, "get", _fake_get({
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
    assert record.url == "https://arxiv.org/abs/1108.1680"
    assert record.identifiers == {"arxiv": "1108.1680", "doi": "10.1214/10-aoas397"}


def test_datacite_record_without_a_journal_keeps_the_arxiv_doi():
    hit = json.loads(json.dumps(DATACITE_HIT))
    hit["attributes"]["relatedIdentifiers"] = []
    record = google_scholar()._parse_datacite_hit(hit)
    assert record.identifiers["doi"] == "10.48550/arXiv.1108.1680"


def test_text_search_lists_arxiv_then_semantic_scholar_then_crossref(monkeypatch):
    from types import SimpleNamespace
    scholar = google_scholar()

    def found(*titles):
        return lambda q, covers=True: [SimpleNamespace(title=t) for t in titles]
    monkeypatch.setattr(scholar, "_search_arxiv", found("A paper"))
    monkeypatch.setattr(scholar, "_search_semantic_scholar", found("A Paper", "Journal paper"))
    monkeypatch.setattr(scholar, "_search_crossref", found("Journal Paper", "Near miss"))
    assert [r.title for r in scholar.search("a paper")] == ["A paper", "Journal paper", "Near miss"]


def test_semantic_scholar_match_reads_as_a_record(monkeypatch):
    calls = []
    monkeypatch.setattr(scholar_module.http_session, "get", _fake_get({
        "https://api.semanticscholar.org/": _Response(text=json.dumps(S2_MATCH)),
    }, calls))
    record, = google_scholar()._search_semantic_scholar("Mastering the game of Go")
    assert calls[0][1]["query"] == "Mastering the game of Go"
    assert record.title == "Mastering the game of Go with deep neural networks and tree search"
    assert record.authors == ["David Silver", "Aja Huang"]
    assert record.description == "The game of Go has long been viewed as the most challenging."
    assert record.publishedDate == "2016-01-27"
    assert record.identifiers == {"doi": "10.1038/nature16961"}
    assert record.source.description == "Semantic Scholar"


def test_semantic_scholar_arxiv_paper_gets_arxivs_doi():
    hit = dict(S2_MATCH["data"][0], externalIds={"ArXiv": "1706.03762"}, journal=None, venue="")
    record = google_scholar()._parse_semantic_scholar(hit)
    assert record.identifiers == {"arxiv": "1706.03762", "doi": "10.48550/arXiv.1706.03762"}


def test_semantic_scholar_without_a_match_is_no_result(monkeypatch):
    monkeypatch.setattr(scholar_module.http_session, "get", _fake_get({
        "https://api.semanticscholar.org/": _Response(404, '{"error":"Title match not found"}'),
    }))
    assert google_scholar()._search_semantic_scholar("qwzx plorp") == []


def test_semantic_scholar_is_retried_once_when_busy(monkeypatch):
    answers = [_Response(429), _Response(text=json.dumps(S2_MATCH))]
    monkeypatch.setattr(scholar_module.http_session, "get", lambda url, **kw: answers.pop(0))
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert len(google_scholar()._search_semantic_scholar("Mastering the game of Go")) == 1


def test_semantic_scholar_sends_the_api_key_when_set(monkeypatch):
    sent = []

    def get(url, **kwargs):
        sent.append(kwargs["headers"])
        return _Response(404)
    monkeypatch.setattr(scholar_module.http_session, "get", get)
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "k123")
    google_scholar()._search_semantic_scholar("anything")
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY")
    google_scholar()._search_semantic_scholar("anything")
    assert sent[0]["x-api-key"] == "k123" and "x-api-key" not in sent[1]


def test_crossref_is_retried_once_when_busy(monkeypatch):
    answers = [_Response(429), _Response(text=json.dumps({"message": {"items": []}}))]
    monkeypatch.setattr(scholar_module.http_session, "get", lambda url, **kw: answers.pop(0))
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert google_scholar()._search_crossref("anything") == [] and answers == []


def test_crossref_gets_the_contact_address_when_set(monkeypatch):
    # Crossref serves requests naming a contact from its less crowded "polite" pool
    sent = []

    def get(url, **kwargs):
        sent.append(kwargs["params"])
        return _Response(text=json.dumps({"message": {"items": []}}))
    monkeypatch.setattr(scholar_module.http_session, "get", get)
    monkeypatch.setenv("CROSSREF_MAILTO", "me@example.org")
    google_scholar()._search_crossref("anything")
    monkeypatch.delenv("CROSSREF_MAILTO")
    google_scholar()._search_crossref("anything")
    assert sent[0]["mailto"] == "me@example.org" and "mailto" not in sent[1]


def test_semantic_scholar_still_busy_is_left_out_for_a_while(monkeypatch):
    # A rebuild asks once a book: without the pause each waited for two refusals
    asked = []

    def get(url, **kw):
        asked.append(url)
        return _Response(429)
    monkeypatch.setattr(scholar_module.http_session, "get", get)
    monkeypatch.setattr("time.sleep", lambda s: None)
    scholar = google_scholar()
    with pytest.raises(requests.HTTPError):
        scholar._search_semantic_scholar("Mastering the game of Go")
    assert len(asked) == 2
    assert scholar._search_semantic_scholar("Attention Is All You Need") == []
    assert len(asked) == 2


def _crossref(*items):
    return _Response(text=json.dumps({"message": {"items": list(items)}}))


def _openlibrary(cover_id=None):
    return _Response(text=json.dumps({"covers": [cover_id]} if cover_id else {}))


def test_crossref_book_gets_its_cover_from_open_library_by_isbn(monkeypatch):
    calls = []
    monkeypatch.setattr(scholar_module.http_session, "get", _fake_get({
        "https://api.crossref.org/": _crossref(
            {"DOI": "10.1142/3727", "title": ["Nobel Lectures in Physics 1922 - 1941"], "type": "monograph",
             "ISBN": ["978-981-02-3402-7"]},
            {"DOI": "10.1016/b978-1-4831-9745-6.50001-5", "title": ["Nobel Lectures"], "type": "book-chapter",
             "ISBN": ["9781483197456"]},
            {"DOI": "10.1016/0029-5582(65)90736-4", "title": ["An article"], "type": "journal-article"}),
        "https://openlibrary.org/isbn/": _openlibrary(5254938),
    }, calls))
    book, chapter, article = google_scholar()._search_crossref("nobel lectures")
    assert book.cover == "https://covers.openlibrary.org/b/id/5254938-L.jpg"
    assert book.identifiers == {"doi": "10.1142/3727", "isbn": "9789810234027"}
    # A chapter shows its book's cover, but the book's ISBN isn't the chapter's own
    assert chapter.cover and "isbn" not in chapter.identifiers
    # An article has no ISBN, and no cover to look for
    assert article.cover == ""
    assert sorted(url for url, __ in calls if "openlibrary" in url) == [
        "https://openlibrary.org/isbn/9781483197456.json", "https://openlibrary.org/isbn/9789810234027.json"]


def test_a_lookup_fetches_only_the_applied_records_cover(monkeypatch):
    # search_titles (imports, Rebuild metadata) leaves covers out; complete() fetches the one
    # of the record applied, so a search no longer waits on a cover per result
    calls = []
    monkeypatch.setattr(scholar_module.http_session, "get", _fake_get({
        "https://api.crossref.org/": _crossref(
            {"DOI": "10.1142/3727", "title": ["Nobel Lectures in Physics 1922 - 1941"], "type": "monograph",
             "ISBN": ["978-981-02-3402-7"]},
            {"DOI": "10.1016/b978-1-4831-9745-6.50001-5", "title": ["Nobel Lectures"], "type": "book-chapter",
             "ISBN": ["9781483197456"]}),
        "https://openlibrary.org/isbn/": _openlibrary(5254938),
    }, calls))
    scholar = google_scholar()
    book, chapter = scholar._search_crossref("nobel lectures", covers=False)
    assert not [url for url, __ in calls if "openlibrary" in url]
    assert scholar.complete(book).cover == "https://covers.openlibrary.org/b/id/5254938-L.jpg"
    assert [url for url, __ in calls if "openlibrary" in url] == ["https://openlibrary.org/isbn/9789810234027.json"]
    # Completing again asks nothing more
    scholar.complete(book)
    assert len([url for url, __ in calls if "openlibrary" in url]) == 1


def test_crossref_book_falls_back_to_google_books_cover(monkeypatch):
    from types import SimpleNamespace
    import cps.search_metadata as search_metadata
    google = SimpleNamespace(__id__="google", search_identifiers=lambda ids, *a: [
        SimpleNamespace(cover="https://books.google.com/cover?id=x" if ids == {"isbn": "9789810234027"} else "")])
    monkeypatch.setattr(search_metadata, "cl", [google])
    monkeypatch.setattr(scholar_module.http_session, "get", _fake_get({
        "https://api.crossref.org/": _crossref(
            {"DOI": "10.1142/3727", "title": ["Nobel Lectures"], "type": "monograph", "ISBN": ["9789810234027"]}),
        "https://openlibrary.org/isbn/": _openlibrary(None),
    }))
    assert google_scholar()._search_crossref("nobel")[0].cover == "https://books.google.com/cover?id=x"


def test_a_cover_lookup_failing_still_shows_the_result(monkeypatch):
    import cps.search_metadata as search_metadata
    monkeypatch.setattr(search_metadata, "cl", [])
    monkeypatch.setattr(scholar_module.http_session, "get", _fake_get({
        "https://api.crossref.org/": _crossref(
            {"DOI": "10.1142/3727", "title": ["Nobel Lectures"], "type": "monograph", "ISBN": ["9789810234027"]}),
        "https://openlibrary.org/isbn/": requests.ConnectionError("down"),
    }))
    (record,) = google_scholar()._search_crossref("nobel")
    assert record.title == "Nobel Lectures" and record.cover == ""


def test_with_google_books_out_of_quota_its_cover_is_asked_for_by_isbn(monkeypatch):
    # The API refuses once the day's quota is used; the cover's own address has no quota
    from types import SimpleNamespace
    import cps.search_metadata as search_metadata

    def refused(ids, *a):
        raise requests.HTTPError("429")
    monkeypatch.setattr(search_metadata, "cl", [SimpleNamespace(__id__="google", search_identifiers=refused)])
    crossref = _crossref({"DOI": "10.1201/9781439894323", "title": ["Understanding Real Analysis"],
                          "type": "book", "ISBN": ["9781439894323"]})
    cover = _Response(text="a cover's bytes")
    monkeypatch.setattr(scholar_module.http_session, "get", _fake_get({
        "https://api.crossref.org/": crossref, "https://openlibrary.org/isbn/": _Response(404),
        "https://books.google.com/books/content": cover}))
    (record,) = google_scholar()._search_crossref("understanding real analysis")
    assert record.cover == ("https://books.google.com/books/content?vid=ISBN9781439894323"
                            "&printsec=frontcover&img=1&zoom=3")

    # For a book it has no cover of, Google serves its "image not available" picture
    import hashlib
    monkeypatch.setattr(scholar_module.google_scholar, "GOOGLE_NO_COVER_MD5", hashlib.md5(cover.content).hexdigest())
    (record,) = google_scholar()._search_crossref("understanding real analysis")
    assert record.cover == ""
