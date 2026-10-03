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
    newest = store.recent_metadata_lookups()
    assert [e["book_id"] for e in newest] == [5, 4, 3]
    # Only those after a given id: what the page asks for while it is open
    assert [e["book_id"] for e in store.recent_metadata_lookups(after=newest[1]["id"])] == [5]


def test_a_lookup_logs_its_book_result_and_changes(env, monkeypatch):
    from cps import metadata_helper
    env, store = env
    dune = SimpleNamespace(title="Dune", authors=["Frank Herbert"],
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
    assert "description" not in changes
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
    panel = html[html.index('id="logs_metadata"'):]
    # Newest first, at the top; a deleted book is named but not linked
    assert panel.index("Gone Book") < panel.index(f'href="/book/{book}"')
    assert 'title="No longer in the library"' in panel
    assert '<span class="lookup-result">matched on Google Books</span>' in panel
    assert '<p class="logs-line lookup is-failed"' in panel and '<span class="lookup-result">failed</span>' in panel
    assert '<span class="lookup-field">Title</span> <span class="lookup-before">dune</span>' in panel
    assert '<span class="lookup-field">Published</span>' in panel and '<span class="lookup-after">1965-08-01</span>' in panel
    # One day heading for the two, and no count or "Show more"
    assert panel.count('class="logs-source"') == 1
    assert "Show more" not in html and "Showing the latest" not in html
    assert 'id="lookup_empty" hidden' in panel
    # The app log is the other view, behind its own pill
    assert 'id="logs_tab_app"' in html and 'id="log_output"' in html


def test_new_lookups_arrive_after_the_newest_one_shown(env):
    env, store = env
    store.log_metadata_lookup(1, "First", "nomatch")
    client = _admin(env)
    html = client.get("/logs").get_data(as_text=True)
    first = int(html.split('data-id="')[1].split('"')[0])
    reply = client.get(f"/logs/lookups?after={first}").get_json()
    assert reply == {"success": True, "html": ""}
    store.log_metadata_lookup(2, "Second <b>", "manual")
    reply = client.get(f"/logs/lookups?after={first}")
    assert reply.headers["Cache-Control"] == "no-store"
    row = reply.get_json()["html"]
    assert "Second &lt;b&gt;" in row and "First" not in row
    assert '<span class="lookup-result">filled in by hand</span>' in row
    # The new rows carry their day's heading; the page drops the one it already had
    assert row.count('class="logs-source"') == 1
    # A bogus id lists from the start rather than failing
    assert "First" in client.get("/logs/lookups?after=x").get_json()["html"]


def test_the_logs_page_says_when_nothing_was_looked_up(env):
    env, __ = env
    html = _admin(env).get("/logs").get_data(as_text=True)
    assert '<p class="logs-empty" id="lookup_empty">No lookups yet.</p>' in html


def test_lookup_times_are_local_like_the_app_log(env, monkeypatch):
    import time
    env, store = env
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    try:
        store.cur.execute("INSERT INTO metadata_lookup_log (book_id, title, status, checked_at) VALUES (?, ?, ?, ?)",
                          (1, "Late Night", "nomatch", "2026-10-03T19:29:20+00:00"))
        store.con.commit()
        panel = _admin(env).get("/logs").get_data(as_text=True)
        panel = panel[panel.index('id="logs_metadata"'):]
        # 19:29 UTC is 04:29 the next morning in Tokyo
        assert '<p class="logs-source" data-day="2026-10-04">4 October</p>' in panel
        assert ">04:29</time>" in panel
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()
