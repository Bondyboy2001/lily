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
