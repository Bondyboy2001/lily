"""The Logs page lists the latest metadata lookups: each book, what its lookup found and every
field it changed, before and after."""
import json
from types import SimpleNamespace

import pytest

from .lily_env import ADMIN_PASSWORD, lily_env
from .metadata_fakes import recording_provider as _provider

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    (tmp_path / "lib").mkdir()
    with lily_env(tmp_path / "lib") as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "")
        yield env, temp_cwa_db


def _admin(env):
    client = env.app.test_client()
    client.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
    return client


def test_the_log_keeps_only_the_newest_lookups(env, monkeypatch):
    import cwa_db
    __, store = env
    monkeypatch.setattr(cwa_db, "LOOKUP_LOG_KEEP", 3)
    for book in range(1, 6):
        store.log_metadata_lookup(book, f"Book {book}", "nomatch")
    assert [e["book_id"] for e in store.recent_metadata_lookups()] == [5, 4, 3]
    assert store.count_metadata_lookups() == 3


def test_a_lookup_logs_its_book_result_and_changes(env, monkeypatch):
    from cps import metadata_helper
    env, store = env
    dune = SimpleNamespace(title="Dune", authors=["Frank Herbert"], description="<p>Spice.</p>",
                           tags=[], series="", series_index=0, publishedDate="1965-08-01",
                           identifiers={"isbn": "9780441172719"}, cover="",
                           source=SimpleNamespace(description="Open Library"))
    nothing = _provider("openlibrary", by_text=[])
    found = _provider("openlibrary", by_text=[dune])
    monkeypatch.setattr(metadata_helper, "metadata_providers", [found])
    monkeypatch.setattr(metadata_helper, "pdf_first_page_text", lambda book: "Dune\nFrank Herbert")
    monkeypatch.setattr(metadata_helper, "pdf_front_matter_text", lambda book: "")
    book = env.add_book("Dune", author="Unknown")
    assert metadata_helper.fetch_and_apply_metadata(book, force=True) is True

    [entry] = store.recent_metadata_lookups()
    assert (entry["book_id"], entry["title"], entry["status"], entry["source"]) == (book, "Dune", "matched", "Open Library")
    changes = json.loads(entry["changes"])
    assert changes["authors"] == ["Unknown", "Frank Herbert"]
    assert changes["description"] == ["", "Spice."]
    assert changes["pubdate"][1] == "1965-08-01"
    assert changes["identifiers"] == ["", "isbn 9780441172719"]

    # Looked up again with nothing to find: logged too, with no changes
    monkeypatch.setattr(metadata_helper, "metadata_providers", [nothing])
    other = env.add_book("Some Pamphlet", author="Jo Bloggs")
    metadata_helper.fetch_and_apply_metadata(other, force=True)
    newest = store.recent_metadata_lookups()[0]
    assert (newest["title"], newest["status"], json.loads(newest["changes"])) == ("Some Pamphlet", "nomatch", {})


def test_the_logs_page_lists_lookups_with_their_changes(env):
    env, store = env
    book = env.add_book("Dune", author="Frank Herbert")
    store.log_metadata_lookup(book, "Dune", "matched", "Google Books",
                              json.dumps({"title": ["dune", "Dune"], "pubdate": ["", "1965-08-01"]}))
    store.log_metadata_lookup(9999, "Gone Book", "failed")
    html = _admin(env).get("/logs").get_data(as_text=True)
    section = html[html.index('id="section-lookups"'):html.index('id="section-service-logs"')]
    assert "Metadata Lookups" in section
    # Newest first; a deleted book is named but not linked
    assert section.index("Gone Book") < section.index(f'href="/book/{book}"')
    assert 'title="No longer in the library"' in section
    assert '<span class="label label-success">Matched</span>' in section and "Google Books" in section
    assert '<span class="label label-danger">Failed</span>' in section
    assert '<span class="lookup-field">Title</span>' in section and '<span class="lookup-before">dune</span>' in section
    assert '<span class="lookup-field">Published</span>' in section and '<span class="lookup-after">1965-08-01</span>' in section
    assert "Showing the latest 2 of 2 lookups." in section and "Show more" not in section
    # The live log is still there, under its own heading
    assert "Service Logs" in html and 'id="log_output"' in html


def test_the_logs_page_says_when_nothing_was_looked_up(env):
    env, __ = env
    html = _admin(env).get("/logs").get_data(as_text=True)
    assert "No lookups yet." in html
