# SPDX-License-Identifier: GPL-3.0-or-later
"""Library grid filter chips (cps/list_filters.py), the admin "Get started" card
(cps/setup_checklist.py) and the sidebar shelf counts, rendered through the real pages."""

import re

import pytest

from .lily_env import lily_env, ADMIN_PASSWORD


@pytest.fixture
def env(tmp_path):
    with lily_env(tmp_path) as e:
        # The throwaway app has no CSRFProtect; the templates only need the token helper.
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "")
        # layout.html links to every section, so register the blueprints lily_env leaves out.
        from cps.editbooks import editbook
        from cps.duplicates import duplicates
        from cps.cwa_functions import library_refresh, cwa_settings
        for bp in (editbook, duplicates, library_refresh, cwa_settings):
            if bp.name not in e.app.blueprints:
                e.app.register_blueprint(bp)
        yield e


def _login(env, name=None, password=ADMIN_PASSWORD):
    client = env.app.test_client()
    resp = client.post("/login", data={"username": name or env.admin().name, "password": password})
    assert resp.status_code in (200, 302), resp.data[:300]
    return client


def _titles(html):
    return sorted(set(re.findall(r'<p title="([^"]+)" class="title"', html)))


def _get(client, url, **query):
    resp = client.get(url, query_string=query)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:500]
    return resp.get_data(as_text=True)


@pytest.mark.unit
class TestFilterChips:
    def _library(self, env):
        env.add_book("Epub English", fmt="EPUB", tags=("Fantasy",), lang="eng")
        env.add_book("Pdf German", fmt="PDF", tags=("History",), lang="deu")
        env.add_book("Epub German", fmt="EPUB", tags=("History",), lang="deu")

    def test_no_filter_shows_everything_without_a_chip_bar(self, env):
        self._library(env)
        html = _get(_login(env), "/")
        assert _titles(html) == ["Epub English", "Epub German", "Pdf German"]
        assert 'class="lily-filter-chips"' not in html

    def test_format_and_tag_filters_combine(self, env):
        self._library(env)
        client = _login(env)
        assert _titles(_get(client, "/", format="pdf")) == ["Pdf German"]
        # There is no language filter: ?lang= is ignored
        assert len(_titles(_get(client, "/", lang="deu"))) == 3
        from cps import calibre_db, db
        history = calibre_db.session.query(db.Tags).filter(db.Tags.name == "History").one()
        assert _titles(_get(client, "/", tag=history.id, format="EPUB")) == ["Epub German"]

    def test_read_status_filters(self, env):
        self._library(env)
        client = _login(env)
        from cps import ub, calibre_db, db
        ids = {b.title: b.id for b in calibre_db.session.query(db.Books).all()}
        admin = env.admin()
        for title, status in (("Epub English", ub.ReadBook.STATUS_FINISHED),
                              ("Pdf German", ub.ReadBook.STATUS_IN_PROGRESS)):
            ub.session.add(ub.ReadBook(user_id=admin.id, book_id=ids[title], read_status=status))
        ub.session.commit()
        assert _titles(_get(client, "/", status="read")) == ["Epub English"]
        assert _titles(_get(client, "/", status="reading")) == ["Pdf German"]
        assert _titles(_get(client, "/", status="unread")) == ["Epub German"]

    def test_grid_cards_show_finished_books_and_a_ribbon_per_format(self, env):
        self._library(env)
        client = _login(env)
        from cps import ub, calibre_db, db
        ids = {b.title: b.id for b in calibre_db.session.query(db.Books).all()}
        admin = env.admin()
        for title, status in (("Epub English", ub.ReadBook.STATUS_FINISHED),
                              ("Pdf German", ub.ReadBook.STATUS_IN_PROGRESS)):
            ub.session.add(ub.ReadBook(user_id=admin.id, book_id=ids[title], read_status=status))
        ub.session.commit()
        html = _get(client, "/")
        cards = {title: html[html.index('data-book-id="%d"' % book_id) - 400:html.index('<p title="%s"' % title)]
                 for title, book_id in ids.items()}
        # The row is (book, read status): only the finished book carries the read class and the eye.
        assert "lily-book is-read" in cards["Epub English"] and "badge read" in cards["Epub English"]
        for title in ("Pdf German", "Epub German"):
            assert "is-read" not in cards[title] and "badge read" not in cards[title], title
        assert '<span class="lily-ribbon lily-ribbon-epub">' in cards["Epub English"]
        assert 'aria-label="PDF"' in cards["Pdf German"] and "lily-ribbon-pdf" in cards["Pdf German"]

    def test_there_is_no_last_read_sort(self, env):
        env.add_book("Book One")
        client = _login(env)
        assert "Last read" not in _get(client, "/newest/stored/")
        assert "Last read" not in _get(client, "/inprogress/stored/")

    def test_there_is_no_series_page_or_series_sort(self, env):
        env.add_book("Book One")
        client = _login(env)
        assert client.get("/series").status_code == 404
        assert "Series order" not in _get(client, "/newest/stored/")

    def test_filters_are_kept_in_sort_links_and_empty_result_offers_a_way_out(self, env):
        self._library(env)
        html = _get(_login(env), "/", format="MOBI")
        assert "No Books Match These Filters" in html
        assert "Clear filters" in html
        assert "format=MOBI" in html  # the sort menu links keep the filter

    def test_author_page_filters(self, env):
        env.add_book("A1", author="Ann", fmt="EPUB")
        env.add_book("A2", author="Ann", fmt="PDF")
        client = _login(env)
        from cps import calibre_db, db
        ann = calibre_db.session.query(db.Authors).filter(db.Authors.name == "Ann").one()
        assert _titles(_get(client, f"/author/stored/{ann.id}", format="PDF")) == ["A2"]
        # An author page with no match renders an empty state instead of bouncing home.
        assert "No Books Match These Filters" in _get(client, f"/author/stored/{ann.id}", format="CBZ")


@pytest.mark.unit
class TestSetupChecklist:
    def test_uploads_are_no_step(self, env):
        # Uploads are always on: settings has no switch for them, so the checklist never asks
        html = _get(_login(env), "/")
        assert "Uploads enabled" not in html

    def test_default_password_is_detected(self, env):
        from cps import constants, ub
        from werkzeug.security import generate_password_hash
        admin = env.admin()
        admin.password = generate_password_hash(constants.LEGACY_DEFAULT_PASSWORD)
        ub.session.commit()
        html = _get(_login(env, password=constants.LEGACY_DEFAULT_PASSWORD), "/")
        assert "The admin account still accepts the default password." in html

    def test_changed_password_counts_as_done(self, env):
        html = _get(_login(env), "/")
        assert "The admin account still accepts the default password." not in html

    def test_regular_users_never_see_it(self, env):
        from cps import constants
        env.add_user("reader", password="pw", role=constants.ROLE_DOWNLOAD)
        html = _get(_login(env, name="reader", password="pw"), "/")
        assert 'id="lily-setup"' not in html


@pytest.mark.unit
def test_sidebar_shelf_counts(env):
    from cps import ub
    admin = env.admin()
    book = env.add_book("Shelved")
    full, empty = ub.Shelf(name="Full", user_id=admin.id), ub.Shelf(name="Empty", user_id=admin.id)
    ub.session.add_all([full, empty])
    ub.session.commit()
    full.books.append(ub.BookShelf(book_id=book, order=1))
    ub.session.commit()
    html = _get(_login(env), "/")
    assert 'title="Full (1)"' in html and 'title="Empty (0)"' in html


@pytest.fixture
def fetched_env(tmp_path, temp_cwa_db):
    # cwa.db is made first, so the library's connections attach it
    (tmp_path / "lib").mkdir()
    with lily_env(tmp_path / "lib") as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "")
        from cps.editbooks import editbook
        from cps.duplicates import duplicates
        from cps.cwa_functions import library_refresh, cwa_settings
        for bp in (editbook, duplicates, library_refresh, cwa_settings):
            if bp.name not in e.app.blueprints:
                e.app.register_blueprint(bp)
        yield e, temp_cwa_db


def test_last_fetched_sort_puts_the_latest_lookup_first_and_unlooked_books_last(fetched_env):
    env, store = fetched_env
    ids = {title: env.add_book(title) for title in ("Never Looked Up", "Looked Up Long Ago", "Looked Up Today")}
    for title, when in (("Looked Up Long Ago", "2026-01-01T09:00:00+00:00"),
                        ("Looked Up Today", "2026-10-03T09:00:00+00:00")):
        store.cur.execute("INSERT INTO metadata_lookups (book_id, status, source, checked_at) VALUES (?, 'matched', '', ?)",
                          (ids[title], when))
    store.con.commit()
    client = _login(env)

    def order(html):
        grid = html[html.index('class="lily-list-toolbar"'):]
        return re.findall(r'<p title="([^"]+)" class="title"', grid)

    assert order(_get(client, "/newest/fetchnew/")) == ["Looked Up Today", "Looked Up Long Ago", "Never Looked Up"]
    assert order(_get(client, "/newest/fetchold/")) == ["Looked Up Long Ago", "Looked Up Today", "Never Looked Up"]
    assert "Last fetched" in _get(client, "/newest/stored/")
