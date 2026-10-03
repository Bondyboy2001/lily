"""Simple search lists the best match first (Relevance), whatever order the books arrived in."""

from datetime import datetime, timedelta, timezone

import pytest

from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path):
    with lily_env(tmp_path) as e:
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(e.app)
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        yield e


def test_a_new_search_lists_the_best_match_first(env):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Added best match first, so Date added (newest first) would list them the other way round
    books = [("Time Machine", "Ann"), ("Time Machines Explained", "Ann"), ("The Time Machine", "Ann"),
             ("Machine of Time", "Ann"), ("Engines", "Time Machine Society")]
    for day, (title, author) in enumerate(books):
        env.add_book(title, author=author, timestamp=start + timedelta(days=day))
    client = env.app.test_client()
    client.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})

    response = client.get("/search", query_string={"query": "time machine"})
    assert response.status_code == 302 and "/search/relevance" in response.headers["Location"]
    html = client.get(response.headers["Location"]).get_data(as_text=True)
    positions = [html.index(f'title="{title}"') for title, __ in books]
    assert positions == sorted(positions)
    # Relevance is chosen, and it has no direction to flip
    assert "Relevance" in html and 'id="lily-sort-dir-toggle"' not in html

    # Picking Date added still works, newest first
    html = client.get(response.headers["Location"].replace("/relevance", "/new")).get_data(as_text=True)
    positions = [html.index(f'title="{title}"') for title, __ in books]
    assert positions == sorted(positions, reverse=True)
