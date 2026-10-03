"""A book's volume: added by hand in the editor ("Add volume"), stored in cwa.db's
book_volumes, and shown in the book page's stage corner as "Vol. 3"."""
import re

import pytest

from .lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "")
        yield e


def _login(env, name=None, password=ADMIN_PASSWORD):
    client = env.app.test_client()
    client.post("/login", data={"username": name or env.admin().name, "password": password})
    return client


def _volume(book_id):
    from cwa_db import CWA_DB
    return CWA_DB().get_book_volume(book_id)


def _save(env, client, book_id, **fields):
    # The save moves the book's folder to calibre's "Title (id)" form, so it has to exist
    (env.library_dir / "Robert Hogg" / "Probability").mkdir(parents=True, exist_ok=True)
    data = {"title": "Probability", "authors": "Robert Hogg", **fields}
    return client.post(f"/admin/book/{book_id}", follow_redirects=True, data=data).get_data(as_text=True)


@pytest.mark.parametrize("typed, parsed", [
    ("3", 3), (" 12 ", 12), ("Vol. 3", 3), ("vol 2", 2), ("Volume 7", 7), ("", None), ("  ", None),
])
def test_parse_volume(typed, parsed):
    from cps.editbooks import parse_volume
    assert parse_volume(typed) == parsed


@pytest.mark.parametrize("typed", ["0", "1000", "-2", "2.5", "three", "3rd"])
def test_parse_volume_refuses_what_is_not_a_volume(typed):
    from cps.editbooks import parse_volume, _INVALID
    assert parse_volume(typed) is _INVALID


def test_the_editor_saves_changes_and_clears_the_volume(env):
    book = env.add_book("Probability", author="Robert Hogg")
    client = _login(env)
    _save(env, client, book, volume="2")
    assert _volume(book) == 2
    # A save without the field (the book page's Fetch Apply) leaves it as it is
    _save(env, client, book)
    assert _volume(book) == 2
    page = _save(env, client, book, volume="two")
    assert "is not a volume" in page and _volume(book) == 2
    _save(env, client, book, volume="")
    assert _volume(book) is None


def test_the_editor_hides_the_field_behind_add_volume_until_there_is_one(env):
    book = env.add_book("Probability", author="Robert Hogg")
    client = _login(env)
    html = client.get(f"/admin/book/{book}").get_data(as_text=True)
    assert '<div class="row" id="volume-field" hidden>' in html
    # "Add volume" sits beside "Add edition" under the title field
    assert re.search(r'id="edition-add">.*?</button>\s*<button[^>]*id="volume-add">', html, re.S)
    _save(env, client, book, volume="3")
    html = client.get(f"/admin/book/{book}").get_data(as_text=True)
    assert '<div class="row" id="volume-field">' in html
    assert 'id="volume-add" hidden>' in html
    assert 'name="volume" id="volume" value="3"' in html


def test_the_book_page_shows_the_volume_in_the_stage_corner(env):
    book = env.add_book("Probability", author="Robert Hogg")
    assert "book-corner" not in _login(env).get(f"/book/{book}").get_data(as_text=True)
    from cwa_db import CWA_DB
    CWA_DB().set_book_volume(book, 3)
    html = _login(env).get(f"/book/{book}").get_data(as_text=True)
    stage = html[html.index('<div class="book-detail-main">'):html.index('<div class="book-detail-cover">')]
    assert re.search(r'<span class="book-volume" id="book-volume" title="Volume 3">Vol\. 3', stage)
    assert "book-edition" not in stage and " · " not in stage
    CWA_DB().set_book_edition(book, 9)
    html = _login(env).get(f"/book/{book}").get_data(as_text=True)
    stage = html[html.index('<div class="book-detail-main">'):html.index('<div class="book-detail-cover">')]
    assert re.search(r'9th ed\..*?</span></span><span aria-hidden="true"> · </span><span class="book-volume"', stage)
