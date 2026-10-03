# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Top bar search suggestions (/get_book_titles_json) must offer the books a search would
find, and never anything the user is not allowed to see."""

import json

import pytest

from .lily_env import lily_env, ADMIN_PASSWORD


@pytest.fixture
def env(tmp_path):
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        yield e


def _login(env):
    client = env.app.test_client()
    resp = client.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
    assert resp.status_code in (200, 302), resp.data[:300]
    return client


def _suggest(client, term):
    resp = client.get("/get_book_titles_json", query_string={"q": term})
    assert resp.status_code == 200, resp.data[:300]
    return json.loads(resp.get_data(as_text=True))


@pytest.mark.unit
class TestBookTitleSuggestions:
    def test_matches_title_and_carries_the_author(self, env):
        env.add_book("Information-geometry-driven graph sequential growth", author="Harry T. Bond")
        env.add_book("Something Else Entirely", author="Someone Else")
        client = _login(env)
        [item] = _suggest(client, "geometry")
        assert item["name"] == "Information-geometry-driven graph sequential growth"
        assert item["author"] == "Harry T. Bond"

    def test_carries_a_small_cover_url_for_the_thumbnail(self, env):
        book_id = env.add_book("Cover Book", author="An Author")
        client = _login(env)
        [item] = _suggest(client, "Cover")
        # The small thumbnail, with the cache-busting stamp the library grid uses.
        assert item["cover"].startswith(f"/cover/{book_id}/sm?c=")
        assert client.get(item["cover"]).status_code == 200

    def test_carries_the_book_page_url_for_direct_navigation(self, env, temp_cwa_db):
        book_id = env.add_book("Direct Book", author="An Author")
        client = _login(env)
        [item] = _suggest(client, "Direct")
        assert item["id"] == book_id
        assert item["url"] == f"/book/{book_id}"
        assert client.get(item["url"]).status_code in (200, 302)

    def test_same_title_different_authors_have_distinct_urls(self, env):
        first = env.add_book("Same Title", author="Author One")
        second = env.add_book("Same Title", author="Author Two")
        client = _login(env)
        items = _suggest(client, "Same Title")
        assert len(items) == 2
        by_id = {item["id"]: item for item in items}
        assert by_id[first]["author"] == "Author One"
        assert by_id[second]["author"] == "Author Two"
        assert by_id[first]["url"] != by_id[second]["url"]

    def test_matches_author(self, env):
        env.add_book("Paper One", author="Bertrand Gauthier")
        env.add_book("Unrelated", author="Nobody")
        client = _login(env)
        assert [item["name"] for item in _suggest(client, "Gauthier")] == ["Paper One"]

    def test_short_or_empty_query_returns_nothing(self, env):
        env.add_book("A Book", author="An Author")
        client = _login(env)
        assert _suggest(client, "") == []
        assert _suggest(client, "a") == []

    def test_no_match_returns_empty_list(self, env):
        env.add_book("A Book", author="An Author")
        client = _login(env)
        assert _suggest(client, "zzzz") == []

    def test_requires_login_when_anonymous_browsing_is_off(self, env):
        env.add_book("A Book", author="An Author")
        resp = env.app.test_client().get("/get_book_titles_json", query_string={"q": "Book"})
        assert resp.status_code == 401

    def test_every_word_must_match_title_or_author(self, env):
        env.add_book("Dune", author="Frank Herbert")
        env.add_book("Dune Atlas", author="Someone Else")
        client = _login(env)
        assert [item["name"] for item in _suggest(client, "dune herbert")] == ["Dune"]
        assert [item["name"] for item in _suggest(client, "herbert, frank")] == ["Dune"]

    def test_folds_accents_and_matches_wildcards_literally(self, env):
        env.add_book("Café Society", author="Zoë Ångström")
        env.add_book("Plain Title", author="Ann Lee")
        client = _login(env)
        assert [item["name"] for item in _suggest(client, "cafe angstrom")] == ["Café Society"]
        assert _suggest(client, "%%") == [] and _suggest(client, "__") == []
