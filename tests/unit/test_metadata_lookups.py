"""What each book's metadata lookup found (cwa.db's metadata_lookups): noted by the lookup,
listed by the library's Metadata filter and the Import & Metadata page, and shown on the book
page. Google Books is asked last."""
import re
from types import SimpleNamespace

import pytest

from .lily_env import lily_env, ADMIN_PASSWORD
from .metadata_fakes import FakeProvider

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "")
        from cps.editbooks import editbook
        from cps.duplicates import duplicates
        from cps.cwa_functions import library_refresh, cwa_settings
        for bp in (editbook, duplicates, library_refresh, cwa_settings):
            if bp.name not in e.app.blueprints:
                e.app.register_blueprint(bp)
        yield e


def _login(env, name=None, password=ADMIN_PASSWORD):
    client = env.app.test_client()
    client.post("/login", data={"username": name or env.admin().name, "password": password})
    return client


def _store():
    from cwa_db import CWA_DB
    return CWA_DB()


def _providers(monkeypatch, *providers):
    from cps import metadata_helper
    monkeypatch.setattr(metadata_helper, "metadata_providers", list(providers))
    monkeypatch.setattr(metadata_helper, "pdf_first_page_text", lambda book: "")
    return metadata_helper


def _record(title, authors):
    return SimpleNamespace(title=title, authors=authors, description="", publisher="", tags=[], series="",
                           series_index=0, publishedDate=None, rating=None, identifiers={}, cover="",
                           source=SimpleNamespace(id="openlibrary", description="Open Library"))


def _down(*a):
    raise RuntimeError("429")


def test_each_lookup_notes_what_it_found(env, monkeypatch):
    dune = env.add_book("Dune", author="Frank Herbert")
    unknown = env.add_book("A Book Nobody Has", author="Jane Roe")
    helper = _providers(monkeypatch, FakeProvider(
        __id__="openlibrary", __name__="OpenLibrary", identifier_types=frozenset(),
        search=lambda q, *a: [_record("Dune", ["Frank Herbert"])] if "Dune" in q else []))
    helper.fetch_and_apply_metadata(dune, force=True)
    helper.fetch_and_apply_metadata(unknown, force=True)
    store = _store()
    assert store.get_metadata_lookup(dune)["status"] == "matched"
    assert store.get_metadata_lookup(dune)["source"] == "Open Library"
    assert store.get_metadata_lookup(unknown)["status"] == "nomatch"
    assert store.metadata_lookup_ids("nomatch") == [unknown]


def test_a_provider_that_did_not_answer_makes_it_failed_not_nomatch(env, monkeypatch):
    book = env.add_book("A Book Nobody Has", author="Jane Roe")
    helper = _providers(
        monkeypatch,
        FakeProvider(__id__="google", __name__="Google", identifier_types=frozenset(), search=_down),
        FakeProvider(__id__="openlibrary", __name__="OpenLibrary", identifier_types=frozenset(),
                     search=lambda q, *a: []))
    unanswered = set()
    assert helper.fetch_and_apply_metadata(book, force=True, unanswered=unanswered) is False
    assert unanswered == {"Google"}
    assert _store().get_metadata_lookup(book)["status"] == "failed"


def test_google_is_asked_last(env, monkeypatch):
    book = env.add_book("Dune", author="Frank Herbert")
    asked = []
    helper = _providers(
        monkeypatch,
        FakeProvider(__id__="google", __name__="Google", identifier_types=frozenset(),
                     search=lambda q, *a: asked.append("google") or []),
        FakeProvider(__id__="openlibrary", __name__="OpenLibrary", identifier_types=frozenset(),
                     search=lambda q, *a: asked.append("openlibrary") or [_record("Dune", ["Frank Herbert"])]))
    helper.fetch_and_apply_metadata(book, force=True)
    # Open Library had it, so Google's quota wasn't spent
    assert asked == ["openlibrary"]


def test_library_metadata_filter_lists_books_by_what_their_lookup_found(env):
    matched = env.add_book("Matched Book")
    failed = env.add_book("Failed Book")
    env.add_book("Unchecked Book")
    store = _store()
    store.save_metadata_lookup(matched, "matched", "Open Library")
    store.save_metadata_lookup(failed, "failed")
    client = _login(env)

    def titles(choice):
        html = client.get("/", query_string={"metadata": choice}).get_data(as_text=True)
        return sorted(set(re.findall(r'<p title="([^"]+)" class="title"', html))), html

    assert titles("failed")[0] == ["Failed Book"]
    assert titles("matched")[0] == ["Matched Book"]
    assert titles("unchecked")[0] == ["Unchecked Book"]
    names, html = titles("failed")
    # The chips let it switch, and the chosen one turns the filter off
    assert 'id="metadata_failed"' in html and 'aria-current="true"' in html
    chosen = re.search(r'<a id="metadata_failed"[^>]*href="([^"]*)"', html)
    assert chosen and "metadata=" not in chosen.group(1)
    # An unknown choice is no filter, and no chips
    names, html = titles("bogus")
    assert len(names) == 3 and 'id="metadata_failed"' not in html


def test_settings_page_counts_lookups(env):
    failed = env.add_book("Failed Book")
    nomatch = env.add_book("Nomatch Book")
    store = _store()
    store.save_metadata_lookup(failed, "failed")
    store.save_metadata_lookup(nomatch, "nomatch")
    store.save_metadata_lookup(9999, "failed")  # deleted since: not counted
    html = _login(env).get("/cwa-settings").get_data(as_text=True)
    row = re.search(r'<a class="lp-row" href="([^"]+)" id="lookups_failed".*?</a>', html, flags=re.S)
    assert row and "metadata=failed" in row.group(1) and '<span class="lp-value">1</span>' in row.group(0)
    assert 'id="lookups_nomatch"' in html


def test_settings_page_hides_failed_row_when_nothing_failed(env):
    html = _login(env).get("/cwa-settings").get_data(as_text=True)
    assert 'id="lookups_failed"' not in html


def test_retry_looks_up_only_those_books_and_keeps_a_rebuilds_progress(env, monkeypatch):
    from cps import metadata_helper
    from cps.tasks.metadata_rebuild import TaskRebuildMetadata, saved_progress
    ids = [env.add_book(t) for t in ("One", "Two", "Three")]
    _store().save_rebuild_progress(ids[1], 1, 0, 0, 3)
    looked_up = []
    monkeypatch.setattr(metadata_helper, "fetch_and_apply_metadata",
                        lambda book_id, force=False, unanswered=None: looked_up.append(book_id) or False)
    monkeypatch.setattr(TaskRebuildMetadata, "_tidy_authors", lambda self, cdb: pytest.fail("no tidy on a retry"))
    task = TaskRebuildMetadata(book_ids=[ids[2], ids[0], 9999])
    with env.app.test_request_context():
        task.start(None)
    assert looked_up == [ids[0], ids[2]] and task.total == 2
    assert saved_progress()["next_book_id"] == ids[1]


def test_book_page_shows_the_lookup_to_editors(env):
    book = env.add_book("Dune")
    _store().save_metadata_lookup(book, "matched", "Open Library")
    html = _login(env).get(f"/book/{book}").get_data(as_text=True)
    fact = re.search(r'<div class="book-metadata-lookup">.*?</div>', html, flags=re.S)
    assert fact and ">From Open Library</dd>" in fact.group(0) and 'title="Looked up ' in fact.group(0)
    dot = re.search(r'<span class="lily-fetched" id="book-fetched-dot"[^>]*>', html)
    assert dot and 'aria-label="Metadata fetched from Open Library on ' in dot.group(0)
    env.add_user("reader", password="pw")
    html = _login(env, "reader", "pw").get(f"/book/{book}").get_data(as_text=True)
    assert "book-metadata-lookup" not in html and "book-fetched-dot" not in html


def test_book_page_shows_no_dot_until_a_lookup_matches(env):
    book = env.add_book("Dune")
    assert "book-fetched-dot" not in _login(env).get(f"/book/{book}").get_data(as_text=True)
    _store().save_metadata_lookup(book, "nomatch")
    assert "book-fetched-dot" not in _login(env).get(f"/book/{book}").get_data(as_text=True)


@pytest.mark.parametrize("before", ["failed", "nomatch"])
def test_applying_a_fetch_metadata_result_marks_the_book_matched(env, before):
    book_id = env.add_book("Dune", author="Frank Herbert")
    (env.library_dir / "Frank Herbert" / "Dune").mkdir(parents=True, exist_ok=True)
    store = _store()
    store.save_metadata_lookup(book_id, before)
    client = _login(env)
    # A plain save leaves the lookup alone
    client.post(f"/admin/book/{book_id}", data={"title": "Dune", "authors": "Frank Herbert"})
    assert _store().get_metadata_lookup(book_id)["status"] == before
    client.post(f"/admin/book/{book_id}", data={"title": "Dune", "authors": "Frank Herbert",
                                                "metadata_source": "Open Library"})
    lookup = _store().get_metadata_lookup(book_id)
    assert lookup["status"] == "matched" and lookup["source"] == "Open Library"


def test_fetch_metadata_apply_names_its_provider_for_the_save():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "cps"
    assert 'set("metadata_source", ' in (root / "static/js/get_meta.js").read_text(encoding="utf-8")
    assert 'name="metadata_source"' in (root / "templates/book_edit.html").read_text(encoding="utf-8")


def test_grid_cards_of_matched_books_carry_the_green_check_for_editors(env):
    matched = env.add_book("Dune")
    env.add_book("Emma")
    _store().save_metadata_lookup(matched, "matched", "Open Library")
    html = _login(env).get("/").get_data(as_text=True)
    assert html.count('class="lily-fetched"') == 1
    card = html[html.index('data-book-id="%d"' % matched):]
    assert card.index('class="lily-fetched"') < card.index("</a>")
    env.add_user("reader", password="pw")
    assert "lily-fetched" not in _login(env, "reader", "pw").get("/").get_data(as_text=True)


def test_applying_a_result_on_the_book_page_changes_only_what_it_fills(env):
    import html as html_lib
    import sqlite3
    book = env.add_book("Dune", tags=("sf",))
    # The save checks the book's folder, as the editor's does
    (env.library_dir / "Test Author" / "Dune").mkdir(parents=True)
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.create_function("title_sort", 1, lambda t: t)
    con.create_function("uuid4", 0, lambda: "")
    con.execute("INSERT INTO identifiers (book, type, val) VALUES (?, 'isbn', '9780441013593')", (book,))
    con.execute("UPDATE books SET pubdate = '1965-08-01 00:00:00+00:00' WHERE id = ?", (book,))
    con.commit()
    con.close()
    c = _login(env)
    page = c.get(f"/book/{book}").get_data(as_text=True)
    form = re.search(r'<form action="([^"]+)" method="post" id="book_edit_frm" hidden>(.*?)</form>', page, flags=re.S)
    assert form
    # What get_meta.js sends: the enabled fields, plus the ones a result filled (here the publisher)
    data = {}
    for tag in re.findall(r"<input [^>]*>", form.group(2)):
        name = html_lib.unescape(re.search(r'name="([^"]+)"', tag).group(1))
        value = html_lib.unescape(re.search(r'value=(?:"([^"]*)"|\'([^\']*)\')', tag).group(1) or "")
        if " disabled" not in tag:
            data[name] = value
    assert "tags" not in data and "comments" not in data and "shelves_present" not in data
    data.update(publisher="Chilton Books", metadata_source="Open Library")
    resp = c.post(html_lib.unescape(form.group(1)), data=data)
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/book/{book}")
    con = sqlite3.connect(env.library_dir / "metadata.db")
    try:
        assert con.execute("SELECT p.name FROM publishers p JOIN books_publishers_link l ON l.publisher = p.id "
                           "WHERE l.book = ?", (book,)).fetchall() == [("Chilton Books",)]
        assert con.execute("SELECT t.name FROM tags t JOIN books_tags_link l ON l.tag = t.id WHERE l.book = ?",
                           (book,)).fetchall() == [("sf",)]
        assert con.execute("SELECT type, val FROM identifiers WHERE book = ?", (book,)).fetchall() == [
            ("isbn", "9780441013593")]
        assert con.execute("SELECT pubdate FROM books WHERE id = ?", (book,)).fetchone()[0].startswith("1965-08-01")
        assert con.execute("SELECT a.name FROM authors a JOIN books_authors_link l ON l.author = a.id "
                           "WHERE l.book = ?", (book,)).fetchall() == [("Test Author",)]
    finally:
        con.close()
    assert _store().get_metadata_lookup(book)["status"] == "matched"
