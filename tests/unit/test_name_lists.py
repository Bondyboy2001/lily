# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The author and series lists at library scale: sorted by Calibre's sort key, cut by letter on
the server (?letter=A), searchable (?q=), and the series grid built from plain columns."""

import re
import sqlite3

import pytest

from .lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, monkeypatch):
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(e.app)
        yield e


def _login(env):
    client = env.app.test_client()
    assert client.post("/login", data={"username": env.admin().name,
                                       "password": ADMIN_PASSWORD}).status_code in (200, 302)
    return client


def _sql(env, statement, args=()):
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.create_function("title_sort", 1, lambda t: t)
    try:
        con.execute(statement, args)
        con.commit()
    finally:
        con.close()


def _author(env, name, sort):
    env.add_book(f"A book by {name}", author=name)
    _sql(env, "UPDATE authors SET sort=? WHERE name=?", (sort, name))


def _series(env, title, series, index):
    book_id = env.add_book(title)
    _sql(env, "INSERT OR IGNORE INTO series (name, sort) VALUES (?, ?)", (series, series))
    _sql(env, "INSERT INTO books_series_link (book, series) SELECT ?, id FROM series WHERE name=?", (book_id, series))
    _sql(env, "UPDATE books SET series_index=? WHERE id=?", (index, book_id))
    return book_id


def _rows(html):
    return re.findall(r'class="row lily-list-row"[^>]*data-name="([^"]*)"', html)


def _get(client, url):
    resp = client.get(url)
    assert resp.status_code == 200, resp.data[:300]
    return resp.get_data(as_text=True)


@pytest.fixture
def authors(env):
    for name, sort in (("Jane Austen", "Austen, Jane"), ("Isaac Asimov", "Asimov, Isaac"),
                       ("Margaret Atwood", "atwood, Margaret"), ("Charles Dickens", "Dickens, Charles"),
                       ("Anne Brontë", "Brontë, Anne")):
        _author(env, name, sort)
    return env


def test_authors_sort_by_surname_ignoring_case(authors):
    html = _get(_login(authors), "/author")
    assert _rows(html) == ["Isaac Asimov", "Margaret Atwood", "Jane Austen", "Anne Brontë", "Charles Dickens"]


def test_letter_filter_uses_the_surname_on_the_server(authors):
    client = _login(authors)
    assert _rows(_get(client, "/author?letter=a")) == ["Isaac Asimov", "Margaret Atwood", "Jane Austen"]
    html = _get(client, "/author?letter=D")
    assert _rows(html) == ["Charles Dickens"]
    assert 'href="/author?letter=D" aria-current="true"' in html
    assert 'class="char"' not in html  # letters are links, not client-side filters


def test_long_lists_open_on_one_letter_without_all(authors, monkeypatch):
    from cps import web_lists
    monkeypatch.setattr(web_lists, "LETTER_ALL_MAX", 3)
    client = _login(authors)
    html = _get(client, "/author")
    assert _rows(html) == ["Isaac Asimov", "Margaret Atwood", "Jane Austen"]  # first letter, A
    assert "?letter=all" not in html
    assert _rows(_get(client, "/author?letter=all")) == _rows(html)  # "all" is refused, A again


def test_search_box_finds_names_across_letters(authors):
    client = _login(authors)
    html = _get(client, "/author?q=ar&letter=D")
    assert _rows(html) == ["Margaret Atwood", "Charles Dickens"]
    assert "No names match" in _get(client, "/author?q=zzz")
    assert _rows(_get(client, "/author?q=100%25")) == []  # LIKE wildcards are literal


def test_descending_direction_is_saved_per_user(authors):
    client = _login(authors)
    client.post("/ajax/view", json={"author": {"dir": "desc"}})
    assert _rows(_get(client, "/author?letter=A")) == ["Jane Austen", "Margaret Atwood", "Isaac Asimov"]


def test_series_grid_shows_each_series_once_with_its_first_book_as_cover(env):
    second = _series(env, "Second", "Discworld", 2.0)
    first = _series(env, "First", "Discworld", 1.0)
    _series(env, "Only", "Alpha Saga", 1.0)
    client = _login(env)
    client.post("/ajax/view", json={"series": {"series_view": "grid"}})
    html = _get(client, "/series")
    titles = re.findall(r'<p title="([^"]*)" class="title">', html)
    assert titles == ["Alpha Saga", "Discworld"]
    card = html[html.index('data-name="Discworld"'):]
    assert f"/cover/{first}/" in card and f"/cover/{second}/" not in card
    assert re.search(r'lily-cover-count"[^>]*>2<', card)
    assert "isotope" not in html.lower()
    assert _get(client, "/series?letter=D").count('class="book lily-book"') == 1


def test_series_list_view_is_cut_by_letter_too(env):
    _series(env, "One", "Alpha Saga", 1.0)
    _series(env, "Two", "Beta Saga", 1.0)
    client = _login(env)
    client.post("/ajax/view", json={"series": {"series_view": "list"}})
    assert _rows(_get(client, "/series?letter=B")) == ["Beta Saga"]
