# SPDX-License-Identifier: GPL-3.0-or-later
"""One book on or off one shelf (shelf.set_book_on_shelf): the book page's Shelves menu and the
remove button on shelf pages."""

import re

import pytest

from .lily_env import lily_env, ADMIN_PASSWORD


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


def _login(env):
    client = env.app.test_client()
    client.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
    return client


def _shelf(env, name, **kw):
    from cps import ub
    shelf = ub.Shelf(name=name, user_id=kw.pop("user_id", env.admin().id), **kw)
    ub.session.add(shelf)
    ub.session.commit()
    return shelf.id


def _on(env, shelf_id, book_id):
    from cps import ub
    ub.session.expire_all()
    return ub.session.query(ub.BookShelf).filter_by(shelf=shelf_id, book_id=book_id).count()


@pytest.mark.unit
class TestSetBookOnShelf:
    def test_on_and_off_and_idempotent(self, env):
        book = env.add_book("Shelvable")
        shelf = _shelf(env, "Favourites")
        c = _login(env)
        url = f"/shelf/{shelf}/book/{book}"
        for _ in range(2):
            resp = c.post(url, json={"on": True})
            assert resp.status_code == 200 and resp.get_json() == {"on": True, "count": 1}
        assert _on(env, shelf, book) == 1
        for _ in range(2):
            assert c.post(url, json={"on": False}).get_json() == {"on": False, "count": 0}
        assert _on(env, shelf, book) == 0

    def test_refusals(self, env):
        book = env.add_book("Shelvable")
        shelf = _shelf(env, "Mine")
        someone_elses = _shelf(env, "Theirs", user_id=env.admin().id + 99)
        c = _login(env)
        assert c.post(f"/shelf/{shelf}/book/{book}", json={"on": "yes"}).status_code == 400
        assert c.post(f"/shelf/{shelf}/book/{book}", data="x").status_code == 400
        assert c.post(f"/shelf/9999/book/{book}", json={"on": True}).status_code == 404
        assert c.post(f"/shelf/{shelf}/book/9999", json={"on": True}).status_code == 404
        assert c.post(f"/shelf/{someone_elses}/book/{book}", json={"on": True}).status_code == 403
        assert _on(env, someone_elses, book) == 0
        signed_out = env.app.test_client()
        assert signed_out.post(f"/shelf/{shelf}/book/{book}", json={"on": True}).status_code in (302, 401)

    def test_book_page_menu_and_live_shelves_row(self, env):
        from cps import ub
        book = env.add_book("Shelvable")
        on, off = _shelf(env, "Reading Group"), _shelf(env, "Holiday")
        ub.session.get(ub.Shelf, on).books.append(ub.BookShelf(book_id=book, order=1))
        ub.session.commit()
        html = _login(env).get(f"/book/{book}").get_data(as_text=True)
        menu = html[html.index('class="dropdown book-shelves-menu"'):]
        menu = menu[:menu.index("</ul>")]
        items = re.findall(r'role="menuitemcheckbox"\s+aria-checked="(true|false)" data-shelf-id="(\d+)"', menu)
        assert ("true", str(on)) in items and ("false", str(off)) in items
        assert "/shelf/create" in menu and "js/shelves.js" in html
        row = html[html.index('id="book-shelves-row"'):]
        row = row[:row.index("</div>")]
        assert re.search(r'data-shelf-id="%d">Reading Group' % on, row)
        assert re.search(r'data-shelf-id="%d" hidden>Holiday' % off, row)

    def test_shelf_page_cards_offer_removal(self, env):
        from cps import ub
        book = env.add_book("Shelvable")
        shelf = _shelf(env, "Favourites")
        ub.session.get(ub.Shelf, shelf).books.append(ub.BookShelf(book_id=book, order=1))
        ub.session.commit()
        html = _login(env).get(f"/shelf/{shelf}").get_data(as_text=True)
        assert 'aria-label="Remove Shelvable from Favourites"' in html
        assert f'data-url="/shelf/{shelf}/book/{book}"' in html and "js/shelves.js" in html
