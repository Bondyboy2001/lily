"""Book descriptions are HTML from Calibre, uploads and metadata providers: they are cleaned
when shown and when stored."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit

EVIL = '<p onclick="steal()">Nice <b>book</b></p><script>alert(1)</script><img src=x onerror=alert(2)>'


def _assert_clean(html):
    assert "<b>book</b>" in html
    # Unknown tags may survive as escaped text (&lt;img ...&gt;), which is harmless
    for bad in ("<script", "<img", "onclick="):
        assert bad not in html, bad


def test_sanitize_html_filter_keeps_formatting_and_drops_scripts():
    from cps.jinjia import sanitize_html
    out = sanitize_html(EVIL)
    _assert_clean(str(out))
    assert hasattr(out, "__html__")  # Markup: not escaped a second time
    assert sanitize_html(None) == "" and sanitize_html("") == ""


def test_templates_no_longer_trust_descriptions():
    from tests.unit.lily_env import REPO
    for name in ("detail.html", "listenmp3.html"):
        text = (REPO / "cps" / "templates" / name).read_text()
        assert "comments[0].text|safe" not in text, name
        # flatten_breaks sanitises too (test below)
        assert "comments[0].text|flatten_breaks" in text, name
    assert "column.value|safe" not in (REPO / "cps" / "templates" / "detail.html").read_text()


def test_flatten_breaks_sanitises_before_marking_safe():
    from cps.jinjia import flatten_breaks_filter
    out = flatten_breaks_filter(EVIL)
    _assert_clean(str(out))
    assert hasattr(out, "__html__")
    assert str(flatten_breaks_filter("<p>One</p><p>Two</p>")) == "<p>One Two</p>"
    assert str(flatten_breaks_filter(None)) == ""


@pytest.fixture
def env(tmp_path):
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(e.app)
        yield e


def test_detail_page_shows_a_cleaned_description(env, monkeypatch):
    from cps import web
    monkeypatch.setattr(web, "CWA_DB", lambda: SimpleNamespace(cwa_settings={}))
    book = env.add_book("Tainted")
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.execute("INSERT INTO comments (book, text) VALUES (?, ?)", (book, EVIL))
    con.commit()
    con.close()
    c = env.app.test_client()
    c.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
    html = c.get(f"/book/{book}").get_data(as_text=True)
    section = html[html.index("book-detail-description"):]
    section = section[:section.index("</section>")]
    _assert_clean(section)


def test_accepted_suggestion_stores_a_cleaned_description(env):
    from cps import metadata_queue
    book = env.add_book("Suggested")
    ub = env.ub
    row = ub.MetadataSuggestion(book_id=book, book_title="Suggested", book_authors="A", provider="openlibrary",
                                record_title="Suggested", record_authors="A", score=0.9,
                                fill=json.dumps({"description": EVIL}), created_at="2026-10-01T00:00:00")
    ub.session.add(row)
    ub.session.commit()
    with env.app.app_context():
        metadata_queue.apply_suggestion(row)
    con = sqlite3.connect(env.library_dir / "metadata.db")
    stored = con.execute("SELECT text FROM comments WHERE book=?", (book,)).fetchone()[0]
    con.close()
    _assert_clean(stored)


def test_auto_fetched_description_is_stored_cleaned(monkeypatch):
    from cps import metadata_helper

    class _Settings:
        def get_cwa_settings(self):
            return {key: False for key in ("auto_metadata_update_title", "auto_metadata_update_authors",
                                           "auto_metadata_update_publisher", "auto_metadata_update_tags",
                                           "auto_metadata_update_series", "auto_metadata_update_published_date",
                                           "auto_metadata_update_rating", "auto_metadata_update_identifiers",
                                           "auto_metadata_update_cover")}

    monkeypatch.setattr(metadata_helper, "CWA_DB", _Settings)
    comment = SimpleNamespace(text="")
    book = SimpleNamespace(id=1, title="T", comments=[comment])
    metadata = SimpleNamespace(title=None, authors=[], description=EVIL, publisher=None)
    session = SimpleNamespace(commit=lambda: None, add=lambda obj: None)
    assert metadata_helper._apply_metadata_to_book(book, metadata, SimpleNamespace(session=session))
    _assert_clean(comment.text)
