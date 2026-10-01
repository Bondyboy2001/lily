"""Table sort parameters are mapped to columns, never pasted into SQL."""

import pytest

from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


@pytest.fixture
def admin(tmp_path):
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        for title in ("Banana", "Apple", "Cherry"):
            env.add_book(title)
        env.add_user("zed")
        env.add_user("amy")
        c = env.app.test_client()
        c.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
        yield c


def _titles(resp):
    assert resp.status_code == 200
    return [row["title"] for row in resp.get_json()["rows"]]


def test_book_table_sorts_by_title_both_ways(admin):
    assert _titles(admin.get("/ajax/listbooks?sort=title&order=asc")) == ["Apple", "Banana", "Cherry"]
    assert _titles(admin.get("/ajax/listbooks?sort=title&order=desc")) == ["Cherry", "Banana", "Apple"]


@pytest.mark.parametrize("order", ["asc, (SELECT 1)", "desc; DROP TABLE books", "asc--"])
def test_book_table_ignores_injected_order(admin, order):
    assert sorted(_titles(admin.get("/ajax/listbooks", query_string={"sort": "title", "order": order}))) == \
        ["Apple", "Banana", "Cherry"]


def test_user_table_sorts_and_ignores_injected_order(admin):
    names = [u["name"] for u in admin.get("/ajax/listusers?sort=name&order=desc").get_json()["rows"]]
    assert names == sorted(names, reverse=True)
    resp = admin.get("/ajax/listusers", query_string={"sort": "name", "order": "asc, (SELECT password FROM user)"})
    assert resp.status_code == 200
