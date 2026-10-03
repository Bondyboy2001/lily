"""A book's edition: added by hand in the editor ("Add edition"), stored in cwa.db's
book_editions, and shown on the book page's cover plate as "9th ed."."""
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


def _edition(book_id):
    from cwa_db import CWA_DB
    return CWA_DB().get_book_edition(book_id)


def _save(env, client, book_id, **fields):
    # The save moves the book's folder to calibre's "Title (id)" form, so it has to exist
    (env.library_dir / "Robert Hogg" / "Probability").mkdir(parents=True, exist_ok=True)
    data = {"title": "Probability", "authors": "Robert Hogg", **fields}
    return client.post(f"/admin/book/{book_id}", follow_redirects=True, data=data).get_data(as_text=True)


@pytest.mark.parametrize("number, text", [
    (1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (9, "9th"),
    (11, "11th"), (12, "12th"), (13, "13th"), (21, "21st"), (22, "22nd"), (101, "101st"), (111, "111th"),
])
def test_ordinal(number, text):
    from cps.jinjia import ordinal
    assert ordinal(number) == text


@pytest.mark.parametrize("typed, parsed", [
    ("6", 6), (" 9 ", 9), ("6th", 6), ("2ND", 2), ("", None), ("  ", None),
])
def test_parse_edition(typed, parsed):
    from cps.editbooks import parse_edition
    assert parse_edition(typed) == parsed


@pytest.mark.parametrize("typed", ["0", "1000", "-2", "2.5", "sixth", "6e"])
def test_parse_edition_refuses_what_is_not_an_edition(typed):
    from cps.editbooks import parse_edition, _INVALID
    assert parse_edition(typed) is _INVALID


def test_the_editor_saves_changes_and_clears_the_edition(env):
    book = env.add_book("Probability", author="Robert Hogg")
    client = _login(env)
    _save(env, client, book, edition="9")
    assert _edition(book) == 9
    _save(env, client, book, edition="10")
    assert _edition(book) == 10
    # A save without the field (the book page's Fetch Apply) leaves it as it is
    _save(env, client, book)
    assert _edition(book) == 10
    _save(env, client, book, edition="")
    assert _edition(book) is None


def test_a_bad_edition_is_refused_and_the_old_one_kept(env):
    book = env.add_book("Probability", author="Robert Hogg")
    client = _login(env)
    _save(env, client, book, edition="4")
    page = _save(env, client, book, edition="sixth")
    assert "is not an edition" in page
    assert _edition(book) == 4


def test_the_editor_offers_add_edition_until_the_book_has_one(env):
    book = env.add_book("Probability", author="Robert Hogg")
    client = _login(env)
    html = client.get(f"/admin/book/{book}").get_data(as_text=True)
    # No edition: the field waits hidden behind an "Add edition" button, like Add tag
    assert '<div class="form-group" id="edition-field" hidden>' in html
    assert re.search(r'<button type="button" class="btn btn-default btn-sm" id="edition-add">', html)
    assert 'name="edition" id="edition" value=""' in html
    _save(env, client, book, edition="9")
    html = client.get(f"/admin/book/{book}").get_data(as_text=True)
    assert '<div class="form-group" id="edition-field">' in html
    assert 'id="edition-add" hidden>' in html
    assert 'name="edition" id="edition" value="9"' in html


def test_the_book_page_shows_the_edition_in_the_stage_corner_to_everyone(env):
    book = env.add_book("Probability", author="Robert Hogg")
    assert "book-edition" not in _login(env).get(f"/book/{book}").get_data(as_text=True)
    from cwa_db import CWA_DB
    CWA_DB().set_book_edition(book, 9)
    env.add_user("reader", password="pw")
    for client in (_login(env), _login(env, "reader", "pw")):
        html = client.get(f"/book/{book}").get_data(as_text=True)
        stage = html[html.index('<div class="book-detail-main">'):html.index('<div class="book-detail-cover">')]
        assert re.search(r'<span class="book-edition" id="book-edition" title="9th edition">9th ed\.', stage)
        plate = html[html.index('<div class="book-detail-cover">'):html.index('<div class="book-detail-head">')]
        assert "book-edition" not in plate


def test_the_fetched_mark_stays_on_the_plate_without_the_edition(env):
    book = env.add_book("Probability", author="Robert Hogg")
    from cwa_db import CWA_DB
    CWA_DB().set_book_edition(book, 2)
    CWA_DB().save_metadata_lookup(book, "matched", "Open Library")
    html = _login(env).get(f"/book/{book}").get_data(as_text=True)
    marks = html[html.index('<span class="lily-cover-marks">'):]
    marks = marks[:marks.index("</div>")]
    assert 'id="book-fetched-dot"' in marks and "book-edition" not in marks


@pytest.mark.parametrize("title, split", [
    ("Probability and Statistical Inference (9th Edition)", ("Probability and Statistical Inference", 9)),
    ("Calculus (Ninth edition)", ("Calculus", 9)),
    ("Algorithms [2nd ed.]", ("Algorithms", 2)),
    ("Topology (3rd edn)", ("Topology", 3)),
    ("Analysis (Edition 4)", ("Analysis", 4)),
    ("Linear Algebra (10th Edition) (Pearson)", ("Linear Algebra (Pearson)", 10)),
    ("Dune", ("Dune", None)),
    ("Dune (Special Edition)", ("Dune (Special Edition)", None)),
    ("(9th Edition)", ("(9th Edition)", None)),  # nothing would be left of the title
])
def test_split_edition(title, split):
    from cps.edition import split_edition
    assert split_edition(title) == split


def test_saving_a_title_with_its_edition_moves_the_edition_to_its_field(env):
    book = env.add_book("Probability", author="Robert Hogg")
    client = _login(env)
    # Applied from Fetch Metadata on the book page: no Edition field in the form
    _save(env, client, book, title="Probability (9th Edition)")
    from cps import calibre_db, db
    assert _edition(book) == 9
    assert calibre_db.session.get(db.Books, book).title == "Probability"
    # A number typed in the field wins over the title's
    _save(env, client, book, title="Probability (2nd Edition)", edition="3")
    assert _edition(book) == 3


def test_the_editor_and_get_meta_split_editions_alike():
    from pathlib import Path
    js = (Path(__file__).parents[2] / "cps/static/js/get_meta.js").read_text(encoding="utf-8")
    from cps.edition import _WORDS
    assert 'var EDITION_WORDS = ["' + '", "'.join(_WORDS) in js.replace("\n    ", " ")
    assert "var split = splitEdition(book.title" in js and "edition-field" not in js
