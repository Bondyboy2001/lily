"""My Reading page: yearly aggregation and the per-user route."""

from datetime import datetime

import pytest

from cps.reading_stats import summarise
from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


def _item(month, title="T", authors=("A",), tags=()):
    return {"when": datetime(2026, month, 5), "title": title, "authors": list(authors), "tags": list(tags)}


def test_summarise_counts_months_authors_and_tags():
    s = summarise([_item(1, "a", ("X",), ("Fic",)), _item(1, "b", ("X", "Y"), ("Fic", "Sci")),
                   _item(3, "c", ("Y",))], 2026)
    assert s["total"] == 3 and s["months"][0] == 2 and s["months"][2] == 1
    assert s["busiest_month"] == 1 and s["max_month"] == 2
    assert s["top_authors"][0] == ("X", 2) and dict(s["top_authors"])["Y"] == 2
    assert s["top_tags"][0] == ("Fic", 2)
    assert s["recent"][0] == "c"


def test_summarise_empty_year():
    s = summarise([], 2026)
    assert s["total"] == 0 and s["busiest_month"] is None and s["months"] == [0] * 12


@pytest.fixture
def client(tmp_path):
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(env.app)
        c = env.app.test_client()
        c.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
        yield env, c


def _mark(env, user, book_id, when, status=None):
    ub = env.ub
    row = ub.ReadBook(user_id=user.id, book_id=book_id, last_modified=when,
                      read_status=ub.ReadBook.STATUS_FINISHED if status is None else status)
    ub.session.add(row)
    ub.session.commit()


def test_page_counts_only_this_users_finished_books_in_the_year(client):
    env, c = client
    me, other = env.admin(), env.add_user("reader2")
    b1 = env.add_book("Mine One", author="Ann", tags=("Fiction",))
    b2 = env.add_book("Mine Two", author="Ann")
    b3 = env.add_book("Not Finished")
    b4 = env.add_book("Last Year")
    b5 = env.add_book("Theirs")
    _mark(env, me, b1, datetime(2026, 2, 3))
    _mark(env, me, b2, datetime(2026, 2, 9))
    _mark(env, me, b3, datetime(2026, 2, 9), status=env.ub.ReadBook.STATUS_IN_PROGRESS)
    _mark(env, me, b4, datetime(2025, 12, 31))
    _mark(env, other, b5, datetime(2026, 2, 9))

    html = c.get("/reading?year=2026").get_data(as_text=True)
    assert 'id="reading-total">2<' in html
    assert "Ann" in html and "Fiction" in html
    assert "Not Finished" not in html and "Theirs" not in html and "Last Year" not in html
    assert 'id="reading-total">1<' in c.get("/reading?year=2025").get_data(as_text=True)


def test_page_handles_no_reading_and_bad_year(client):
    _, c = client
    assert c.get("/reading").status_code == 200
    assert c.get("/reading?year=99999").status_code == 200
