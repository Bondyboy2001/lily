# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""OPDS catalog feeds (cps/opds.py) rendered through a real Flask test client against a real
app.db + metadata.db (see lily_env.py): root navigation feed, paginated acquisition feeds,
search, auth, and per-user content restrictions."""

import base64
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest

from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit

ATOM = "{http://www.w3.org/2005/Atom}"


@pytest.fixture
def env(tmp_path):
    with lily_env(tmp_path, config_books_per_page=2) as e:
        yield e


def _auth(name, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{name}:{password}".encode()).decode()}


def _admin_headers(env):
    return _auth(env.admin().name, ADMIN_PASSWORD)


def _get_feed(env, path, headers):
    resp = env.app.test_client().get(path, headers=headers)
    assert resp.status_code == 200, resp.data[:500]
    assert resp.headers["Content-Type"].startswith("application/atom+xml")
    root = ET.fromstring(resp.data)  # raises if the feed is not well-formed XML
    assert root.tag == ATOM + "feed"
    return root


def _titles(root):
    return [e.findtext(ATOM + "title") for e in root.findall(ATOM + "entry")]


def _link(root, rel):
    for link in root.findall(ATOM + "link"):
        if link.get("rel") == rel:
            return link.get("href")
    return None


def _add_books(env, n, **kw):
    ids = []
    for i in range(n):
        ids.append(env.add_book(f"Book {i}", timestamp=datetime(2026, 1, 1 + i, tzinfo=timezone.utc), **kw))
    return ids


class TestOpdsAuth:
    def test_requires_credentials(self, env):
        resp = env.app.test_client().get("/opds")
        assert resp.status_code == 401

    def test_wrong_password_rejected(self, env):
        resp = env.app.test_client().get("/opds", headers=_auth(env.admin().name, "nope"))
        assert resp.status_code == 401

    def test_anonymous_browse_allows_guest(self, env):
        from cps import config
        config.config_anonbrowse = 1
        root = _get_feed(env, "/opds", headers={})
        titles = _titles(root)
        assert "Alphabetical Books" in titles
        # Read/unread shelves are never offered to the anonymous guest.
        assert "Read Books" not in titles


class TestRootCatalog:
    def test_root_is_navigation_feed_with_search(self, env):
        root = _get_feed(env, "/opds", _admin_headers(env))
        titles = _titles(root)
        assert titles[0] == "Alphabetical Books"
        assert "Recently added Books" in titles
        assert _link(root, "start") == "/opds"
        assert _link(root, "search") == "/opds/osd"
        for entry in root.findall(ATOM + "entry"):
            href = entry.find(ATOM + "link").get("href")
            assert href.startswith("/opds/")
            assert entry.findtext(ATOM + "id") == href

    def test_hidden_entries_and_custom_order_respected(self, env):
        from cps import ub
        user = env.add_user("picky", password="pw", view_settings={
            "opds": {"root_order": ["authors", "books"], "hidden_entries": ["recent", "categories"]}})
        ub.session.commit()
        titles = _titles(_get_feed(env, "/opds", _auth(user.name, "pw")))
        assert titles[:2] == ["Authors", "Alphabetical Books"]
        assert "Recently added Books" not in titles
        assert "Categories" not in titles

    def test_osd_is_valid_opensearch(self, env):
        resp = env.app.test_client().get("/opds/osd", headers=_admin_headers(env))
        assert resp.status_code == 200
        root = ET.fromstring(resp.data)
        assert root.tag.endswith("OpenSearchDescription")


class TestAcquisitionFeeds:
    def test_new_feed_paginates_newest_first(self, env):
        _add_books(env, 5)
        headers = _admin_headers(env)
        page1 = _get_feed(env, "/opds/new", headers)
        assert _titles(page1) == ["Book 4", "Book 3"]
        assert _link(page1, "next") == "/opds/new?offset=2"
        assert _link(page1, "previous") is None

        page3 = _get_feed(env, "/opds/new?offset=4", headers)
        assert _titles(page3) == ["Book 0"]
        assert _link(page3, "next") is None
        assert _link(page3, "previous") == "/opds/new?offset=2"

    def test_entries_have_acquisition_links(self, env):
        (book_id,) = _add_books(env, 1)
        entry = _get_feed(env, "/opds/new", _admin_headers(env)).find(ATOM + "entry")
        acq = [l for l in entry.findall(ATOM + "link") if l.get("rel") == "http://opds-spec.org/acquisition"]
        assert len(acq) == 1
        assert acq[0].get("href") == f"/opds/download/{book_id}/epub/"
        assert acq[0].get("type") == "application/epub+zip"
        assert entry.find(ATOM + "author").findtext(ATOM + "name") == "Test Author"

    def test_a_book_with_no_author_names_none(self, env):
        # calibre keeps "Unknown" as a stand-in; a reader app is not told it is the author
        env.add_book("Anonymous Notes", author="Unknown")
        entry = _get_feed(env, "/opds/new", _admin_headers(env)).find(ATOM + "entry")
        assert entry.findtext(ATOM + "title") == "Anonymous Notes"
        assert entry.find(ATOM + "author") is None

    def test_feed_hidden_when_sidebar_entry_disabled(self, env):
        from cps import constants
        user = env.add_user("nocat", password="pw",
                            sidebar_view=constants.ADMIN_USER_SIDEBAR & ~constants.SIDEBAR_CATEGORY)
        client = env.app.test_client()
        assert client.get("/opds/category", headers=_auth(user.name, "pw")).status_code == 404
        assert "Categories" not in _titles(_get_feed(env, "/opds", _auth(user.name, "pw")))
        # "Recently added" is deliberately always visible (User.check_visibility).
        assert client.get("/opds/new", headers=_auth(user.name, "pw")).status_code == 200

    def test_letter_feed_all_books_alphabetical(self, env):
        env.add_book("Zeta")
        env.add_book("Alpha")
        env.add_book("Mu")
        root = _get_feed(env, "/opds/books/letter/00", _admin_headers(env))
        assert _titles(root) == ["Alpha", "Mu"]
        assert _link(root, "next") == "/opds/books/letter/00?offset=2"

    def test_denied_tags_hide_books_from_feeds(self, env):
        env.add_book("Visible", tags=["Fiction"])
        env.add_book("Forbidden", tags=["Secret"])
        user = env.add_user("restricted", password="pw", denied_tags="Secret")
        headers = _auth(user.name, "pw")
        assert _titles(_get_feed(env, "/opds/new", headers)) == ["Visible"]
        # The admin (no restrictions) still sees both.
        assert sorted(_titles(_get_feed(env, "/opds/new", _admin_headers(env)))) == ["Forbidden", "Visible"]

    def test_allowed_tags_limit_feeds(self, env):
        env.add_book("Kids Book", tags=["Kids"])
        env.add_book("Adult Book", tags=["Adult"])
        user = env.add_user("kid", password="pw", allowed_tags="Kids")
        assert _titles(_get_feed(env, "/opds/new", _auth(user.name, "pw"))) == ["Kids Book"]


class TestSearch:
    def test_search_matches_title(self, env):
        env.add_book("The Hobbit")
        env.add_book("Dune")
        root = _get_feed(env, "/opds/search?query=hobbit", _admin_headers(env))
        assert _titles(root) == ["The Hobbit"]

    def test_search_matches_author(self, env):
        env.add_book("Foundation", author="Isaac Asimov")
        env.add_book("Dune", author="Frank Herbert")
        root = _get_feed(env, "/opds/search?query=asimov", _admin_headers(env))
        assert _titles(root) == ["Foundation"]

    def test_search_no_results_and_empty_query_are_valid_feeds(self, env):
        env.add_book("Dune")
        assert _titles(_get_feed(env, "/opds/search?query=nomatch", _admin_headers(env))) == []
        assert _titles(_get_feed(env, "/opds/search?query=", _admin_headers(env))) == []

    def test_search_respects_denied_tags(self, env):
        env.add_book("Secret Diary", tags=["Secret"])
        env.add_book("Public Diary", tags=["Public"])
        user = env.add_user("searcher", password="pw", denied_tags="Secret")
        root = _get_feed(env, "/opds/search?query=diary", _auth(user.name, "pw"))
        assert _titles(root) == ["Public Diary"]
