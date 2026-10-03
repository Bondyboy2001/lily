"""How lookups treat the providers: a busy one is left alone for a while, a failed search is
raised rather than passed off as no results, Open Library is asked for a description only for
the record that is applied, and a subtitle is part of the title."""
from types import SimpleNamespace

import pytest
import requests

from cps.services.Metadata import CoolOff, ProviderBusy, ProviderError

from .metadata_fakes import FakeProvider

pytestmark = pytest.mark.unit


class _Response:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self.payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)


def test_cool_off_lasts_as_long_as_the_service_asks(monkeypatch):
    from cps.services import Metadata as module
    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    pause = CoolOff(seconds=60, longest=600)
    assert not pause.active()
    pause.start()
    now[0] += 59
    assert pause.active()
    now[0] += 2
    assert not pause.active()
    pause.start(_Response(429, headers={"Retry-After": "5"}))
    now[0] += 6
    assert not pause.active()
    pause.start(_Response(429, headers={"Retry-After": "86400"}))
    now[0] += 601
    assert not pause.active()


def test_google_out_of_quota_is_not_asked_again_at_once(monkeypatch):
    from cps.metadata_provider import google as module
    asked = []

    def get(url, **kw):
        asked.append(kw["params"]["q"])
        return _Response(429)
    monkeypatch.setattr(module.requests, "get", get)
    monkeypatch.setattr(module.Google, "_api_key", staticmethod(lambda: "secret-key"))
    google = module.Google()
    # A failure, not "no results": the edit page says the search failed, a rebuild who didn't answer
    with pytest.raises(ProviderError) as failed:
        google.search("Dune Frank Herbert")
    assert "429" in str(failed.value) and "secret-key" not in str(failed.value)
    with pytest.raises(ProviderBusy):
        google.search_identifiers({"isbn": "9780441172719"})
    assert asked == ["Dune Frank Herbert"]
    # Another failure is not a quota: the next search asks again
    other = module.Google()
    monkeypatch.setattr(module.requests, "get", lambda url, **kw: asked.append("again") or _Response(500))
    for _attempt in range(2):
        with pytest.raises(ProviderError):
            other.search("Dune")
    assert asked[1:] == ["again", "again"]


def test_google_gives_the_title_in_full(monkeypatch):
    from cps.metadata_provider import google as module
    items = [{"id": "a1", "volumeInfo": {"title": "Sapiens", "subtitle": "A Brief History of Humankind"}},
             {"id": "b2", "volumeInfo": {"title": "Dune"}}]
    monkeypatch.setattr(module.requests, "get", lambda url, **kw: _Response(payload={"items": items}))
    sapiens, dune = module.Google().search("anything")
    assert (sapiens.title, sapiens.subtitle) == ("Sapiens: A Brief History of Humankind",
                                                 "A Brief History of Humankind")
    assert (dune.title, dune.subtitle) == ("Dune", "")


def _open_library(monkeypatch):
    from cps.metadata_provider import openlibrary as module
    asked = []
    docs = [{"key": "/works/OL1W", "title": "Dune", "author_name": ["Frank Herbert"]},
            {"key": "/works/OL2W", "title": "Dune Messiah", "author_name": ["Frank Herbert"]}]

    def get(url, **kw):
        asked.append(url.rsplit("/", 1)[-1])
        if url.endswith("/search.json"):
            return _Response(payload={"docs": docs})
        return _Response(payload={"description": {"value": "About " + asked[-1]}})
    monkeypatch.setattr("requests.get", get)
    return module.OpenLibrary(), asked


def test_open_library_search_describes_every_result(monkeypatch):
    # The edit page shows each result's description
    provider, asked = _open_library(monkeypatch)
    records = provider.search("Dune Frank Herbert")
    assert [r.description for r in records] == ["About OL1W.json", "About OL2W.json"]
    assert sorted(asked) == ["OL1W.json", "OL2W.json", "search.json"]


def test_a_lookup_asks_open_library_for_one_description(monkeypatch):
    from cps import metadata_helper
    provider, asked = _open_library(monkeypatch)
    monkeypatch.setattr(metadata_helper, "metadata_providers", [provider])
    record = metadata_helper._find_record("Dune", ["Frank Herbert"], {}, "")
    assert (record.title, record.description) == ("Dune", "About OL1W.json")
    assert asked == ["search.json", "OL1W.json"]
    # No match: the search alone
    del asked[:]
    assert metadata_helper._find_record("Emma", ["Jane Austen"], {}, "") is None
    assert asked == ["search.json"]


def test_a_retried_request_waits_once_when_the_service_is_busy(monkeypatch):
    from cps.services.Metadata import get_patiently
    answers, waits = [_Response(429), _Response(200)], []
    monkeypatch.setattr("requests.get", lambda url, **kw: answers.pop(0))
    monkeypatch.setattr("time.sleep", waits.append)
    assert get_patiently("https://example.org", pause=2).status_code == 200 and waits == [2]
    # Still busy the second time: the answer is the caller's to deal with
    answers[:] = [_Response(429), _Response(429)]
    assert get_patiently("https://example.org").status_code == 429 and waits == [2, 1.5]


def test_a_failed_open_library_search_is_raised_but_a_missing_description_is_not(monkeypatch):
    from cps.metadata_provider import openlibrary as module
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr("requests.get", lambda url, **kw: _Response(503))
    with pytest.raises(requests.HTTPError):
        module.OpenLibrary().search("Dune")

    def get(url, **kw):
        if url.endswith("/search.json"):
            return _Response(payload={"docs": [{"key": "/works/OL1W", "title": "Dune"}]})
        return _Response(503)
    monkeypatch.setattr("requests.get", get)
    assert [(r.title, r.description) for r in module.OpenLibrary().search("Dune")] == [("Dune", "")]


def test_hardcover_without_a_token_is_skipped_and_a_failure_is_raised(monkeypatch):
    from cps.metadata_provider import hardcover as module
    monkeypatch.delenv("HARDCOVER_TOKEN", raising=False)
    monkeypatch.setattr(module, "config", SimpleNamespace(config_hardcover_token=None))
    assert module.Hardcover().search("Dune") == []
    monkeypatch.setenv("HARDCOVER_TOKEN", "t0ken")

    def down(url, **kw):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(module.requests, "post", down)
    with pytest.raises(ProviderError):
        module.Hardcover().search("Dune")


def test_a_lookup_names_the_providers_that_did_not_answer(monkeypatch):
    from cps import metadata_helper

    def failing(error):
        def search(*a):
            raise error
        return search
    record = SimpleNamespace(title="Dune", authors=["Frank Herbert"])
    providers = [
        FakeProvider(__id__="google", __name__="Google", identifier_types=frozenset({"isbn"}),
                     search_identifiers=failing(ProviderBusy("out of quota")),
                     search=failing(ProviderBusy("out of quota"))),
        FakeProvider(__id__="openlibrary", __name__="Open Library", identifier_types=frozenset(),
                     search=failing(requests.Timeout())),
        FakeProvider(__id__="x", __name__="Other", identifier_types=frozenset(), search=lambda q, *a: [record]),
    ]
    monkeypatch.setattr(metadata_helper, "metadata_providers", providers)
    unanswered = set()
    found = metadata_helper._find_record("Dune", ["Frank Herbert"], {"isbn": "9780441172719"}, "", unanswered)
    # The book is still looked up with those that do answer
    assert found is record and unanswered == {"Google", "Open Library"}
