"""Stand-ins for the lookup tests: a metadata provider, and metadata_helper wired to use it."""
from types import SimpleNamespace

from cps.services.Metadata import Metadata


class FakeProvider(Metadata):
    """A provider answering as the test says: FakeProvider(__id__="google", __name__="Google",
    identifier_types=frozenset(), search=lambda query, *a: [record]). What it isn't given
    behaves as the base class does."""

    def __init__(self, **attrs):
        self.__dict__.update(attrs)


def recording_provider(pid, id_types=("isbn",), by_id=(), by_text=(), calls=None):
    """A provider that answers an identifier lookup with by_id and a title search with by_text,
    noting each request in `calls` as (provider, "ids" or "text", what was asked)."""
    calls = calls if calls is not None else []
    return FakeProvider(
        __id__=pid, __name__=pid, identifier_types=frozenset(id_types),
        search_identifiers=lambda ids, *a: calls.append((pid, "ids", ids)) or list(by_id),
        search=lambda q, *a: calls.append((pid, "text", q)) or list(by_text))


def lookup_setup(monkeypatch, providers, first="", front=""):
    """metadata_helper asking only `providers`, for a book whose PDF's first page reads `first`
    and its next pages `front`. Returns it with the list the titles it applies are added to."""
    from cps import metadata_helper
    applied = []
    monkeypatch.setattr(metadata_helper, "metadata_providers", providers)
    settings = {"auto_metadata_fetch_enabled": 1}
    monkeypatch.setattr(metadata_helper, "CWA_DB", lambda: SimpleNamespace(get_cwa_settings=lambda: settings))
    monkeypatch.setattr(metadata_helper, "pdf_first_page_text", lambda book: first)
    monkeypatch.setattr(metadata_helper, "pdf_front_matter_text", lambda book: front)
    monkeypatch.setattr(metadata_helper, "_apply_record",
                        lambda cdb, book, record, cover, **kw: applied.append(record.title) or True)
    return metadata_helper, applied
