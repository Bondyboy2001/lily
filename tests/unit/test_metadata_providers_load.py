"""How lookups treat the providers: a busy one is left alone for a while, and Open Library is
asked for a description only for the record that is applied."""
from types import SimpleNamespace

import pytest
import requests

from cps.services.Metadata import CoolOff

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
    google = module.Google()
    assert google.search("Dune Frank Herbert") == []
    assert google.search_identifiers({"isbn": "9780441172719"}) == []
    assert asked == ["Dune Frank Herbert"]
    # Another failure is not a quota: the next search asks again
    other = module.Google()
    monkeypatch.setattr(module.requests, "get", lambda url, **kw: asked.append("again") or _Response(500))
    assert other.search("Dune") == [] and other.search("Dune") == []
    assert asked[1:] == ["again", "again"]


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
    monkeypatch.setattr(module.requests, "get", get)
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


def test_a_provider_without_the_lighter_search_is_searched_as_before(monkeypatch):
    from cps import metadata_helper
    record = SimpleNamespace(title="Dune", authors=["Frank Herbert"])
    provider = SimpleNamespace(__id__="x", __name__="X", identifier_types=frozenset(),
                               search=lambda q, *a: [record])
    monkeypatch.setattr(metadata_helper, "metadata_providers", [provider])
    assert metadata_helper._find_record("Dune", ["Frank Herbert"], {}, "") is record
