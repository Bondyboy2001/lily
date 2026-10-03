"""The toast that says books arrived: /ajax/recent-imports and the importer's date added."""

from pathlib import Path

import pytest

from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path):
    with lily_env(tmp_path) as e:
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(e.app)
        yield e


def test_recent_imports_names_the_books_added_since_the_last_answer(env):
    client = env.app.test_client()
    assert client.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD}).status_code in (200, 302)
    env.add_book("Already Here")
    first = client.get("/ajax/recent-imports").get_json()
    assert set(first) == {"last"} and first["last"] >= 1

    book_id = env.add_book("Birdsong Notes")
    one = client.get("/ajax/recent-imports", query_string={"after": first["last"]}).get_json()
    assert one["count"] == 1 and one["book_id"] == book_id and "Birdsong Notes" in one["message"]

    env.add_book("Second")
    env.add_book("Third")
    two = client.get("/ajax/recent-imports", query_string={"after": one["last"]}).get_json()
    assert two["count"] == 2 and two["book_id"] is None and "2 books" in two["message"]
    assert "count" not in client.get("/ajax/recent-imports", query_string={"after": two["last"]}).get_json()


def test_the_importer_writes_date_added_in_utc():
    # datetime.now() is local time; labelled +00:00 it put every import an hour ahead in summer
    source = (Path(__file__).resolve().parents[2] / "scripts/ingest_processor.py").read_text()
    assert 'datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S+00:00")' in source
    assert 'datetime.now().strftime("%Y-%m-%d %H:%M:%S+00:00")' not in source


def test_earlier_copy_needs_the_same_title_and_the_same_authors(env):
    from cps.recent_imports import earlier_copy
    first = env.add_book("Dune", author="Frank Herbert")
    other_author = env.add_book("Dune", author="Brian Herbert")
    again = env.add_book("DUNE", author="Frank Herbert")
    with env.app.test_request_context():
        assert earlier_copy(again) == first
        assert earlier_copy(first) == again
        assert earlier_copy(other_author) is None


def test_unreadable_formats_names_a_file_no_reader_can_open(env):
    import zipfile
    from cps.recent_imports import unreadable_formats
    good = env.add_book("Good Book", author="A", fmt="EPUB")
    broken = env.add_book("broken", author="A", fmt="PDF")
    missing = env.add_book("Gone", author="A", fmt="DJVU")
    (env.library_dir / "A" / "Good Book").mkdir(parents=True)
    with zipfile.ZipFile(env.library_dir / "A" / "Good Book" / "Good Book.epub", "w") as epub:
        epub.writestr("mimetype", "application/epub+zip")
        epub.writestr("META-INF/container.xml", "<container/>")
    (env.library_dir / "A" / "broken").mkdir(parents=True)
    (env.library_dir / "A" / "broken" / "broken.pdf").write_bytes(b"%PDF-1.4 broken")
    with env.app.test_request_context():
        assert unreadable_formats(good) == []
        assert unreadable_formats(broken) == ["PDF"]
        assert unreadable_formats(missing) == ["DJVU"]
