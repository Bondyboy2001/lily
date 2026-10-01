"""Metadata suggestion queue: review actions and the lookup task."""

import json
import sqlite3

import pytest

from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path):
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(e.app)
        from cps.metadata_queue import suggestions
        if suggestions.name not in e.app.blueprints:
            e.app.register_blueprint(suggestions)
        yield e


@pytest.fixture
def admin(env):
    c = env.app.test_client()
    c.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
    return c


def _suggest(env, book_id, title, score=0.9, fill=None, provider="openlibrary"):
    ub = env.ub
    row = ub.MetadataSuggestion(
        book_id=book_id, book_title=title, book_authors="Test Author", provider=provider,
        record_title=title, record_authors="Test Author", score=score,
        fill=json.dumps(fill or {"description": "A new description.", "identifiers": {"openlibrary": "OL1W"}}),
        created_at="2026-10-01T00:00:00")
    ub.session.add(row)
    ub.session.commit()
    return row.id


def _book_state(env, book_id):
    con = sqlite3.connect(env.library_dir / "metadata.db")
    try:
        comment = con.execute("SELECT text FROM comments WHERE book=?", (book_id,)).fetchone()
        idents = dict(con.execute("SELECT type, val FROM identifiers WHERE book=?", (book_id,)).fetchall())
    finally:
        con.close()
    return (comment[0] if comment else None), idents


def _status(env, sid):
    env.ub.session.expire_all()
    return env.ub.session.query(env.ub.MetadataSuggestion).get(sid).status


def test_accept_fills_description_and_identifiers(env, admin):
    book = env.add_book("Plain Book")
    sid = _suggest(env, book, "Plain Book")
    assert "Plain Book" in admin.get("/admin/metadata/suggestions").get_data(as_text=True)
    assert admin.post(f"/admin/metadata/suggestions/{sid}/accept").status_code == 302
    assert _book_state(env, book) == ("A new description.", {"openlibrary": "OL1W"})
    assert _status(env, sid) == "accepted"


def test_accept_never_overwrites_what_the_book_already_has(env, admin):
    book = env.add_book("Described")
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.execute("INSERT INTO comments (book, text) VALUES (?, 'Mine')", (book,))
    con.execute("INSERT INTO identifiers (book, type, val) VALUES (?, 'openlibrary', 'KEEP')", (book,))
    con.commit()
    con.close()
    sid = _suggest(env, book, "Described")
    admin.post(f"/admin/metadata/suggestions/{sid}/accept")
    assert _book_state(env, book) == ("Mine", {"openlibrary": "KEEP"})
    assert _status(env, sid) == "rejected"  # nothing left to add


def test_reject_changes_nothing_on_the_book(env, admin):
    book = env.add_book("Rejected")
    sid = _suggest(env, book, "Rejected")
    admin.post(f"/admin/metadata/suggestions/{sid}/reject")
    assert _book_state(env, book) == (None, {}) and _status(env, sid) == "rejected"
    assert admin.post(f"/admin/metadata/suggestions/{sid}/accept").status_code == 302  # already handled: no-op
    assert _book_state(env, book) == (None, {})


def test_bulk_accept_only_takes_high_confidence_and_reject_all_clears(env, admin):
    hi, lo = env.add_book("High"), env.add_book("Low")
    s_hi, s_lo = _suggest(env, hi, "High", score=0.9), _suggest(env, lo, "Low", score=0.7)
    admin.post("/admin/metadata/suggestions/bulk/accept_high")
    assert _status(env, s_hi) == "accepted" and _status(env, s_lo) == "pending"
    assert _book_state(env, lo) == (None, {})
    admin.post("/admin/metadata/suggestions/bulk/reject_all")
    assert _status(env, s_lo) == "rejected"


def test_review_actions_are_admin_only(env):
    env.add_user("plain", password="pw")
    book = env.add_book("Guarded")
    sid = _suggest(env, book, "Guarded")
    c = env.app.test_client()
    c.post("/login", data={"username": "plain", "password": "pw"})
    for method, path in (("get", "/admin/metadata/suggestions"),
                         ("post", f"/admin/metadata/suggestions/{sid}/accept"),
                         ("post", "/admin/metadata/suggestions/bulk/accept_high"),
                         ("post", "/admin/metadata/suggestions/run")):
        resp = getattr(c, method)(path)
        assert resp.status_code in (401, 403) or (resp.status_code == 302 and "/admin/metadata" not in resp.headers["Location"]), path
    assert _status(env, sid) == "pending" and _book_state(env, book) == (None, {})


# ---------------------------------------------------------------- the lookup task

class _FakeProvider:
    __id__ = "fake"
    __name__ = "Fake"

    def __init__(self, records):
        self.records = records

    def search(self, query, generic_cover="", locale="en"):
        return self.records


def _record(title, authors, description="Desc.", identifiers=None):
    from cps.services.Metadata import MetaRecord, MetaSourceInfo
    return MetaRecord(id="1", title=title, authors=authors, url="https://example.org/1",
                      source=MetaSourceInfo(id="fake", description="Fake", link="https://example.org"),
                      description=description, identifiers=identifiers or {"fake": "1"})


def test_task_queues_matches_marks_misses_and_does_not_repeat_itself(env, monkeypatch):
    from cps.services.worker import STAT_FINISH_SUCCESS
    from cps.tasks import suggest_metadata as task_mod
    monkeypatch.setattr(task_mod, "REQUEST_DELAY_SECONDS", 0)
    hit = env.add_book("Good Omens", author="Neil Gaiman")
    miss = env.add_book("Obscure Pamphlet", author="Nobody")
    provider = _FakeProvider([_record("Good Omens", ["Neil Gaiman"])])
    monkeypatch.setattr(task_mod, "usable_providers", lambda: [provider])

    t = task_mod.TaskSuggestMetadata(batch_size=10)
    t.start(None)
    assert t.stat == STAT_FINISH_SUCCESS, t.error
    ub = env.ub
    ub.session.expire_all()
    rows = {r.book_id: r for r in ub.session.query(ub.MetadataSuggestion).all()}
    assert rows[hit].status == "pending" and rows[hit].score >= 0.85
    assert json.loads(rows[hit].fill)["identifiers"] == {"fake": "1"}
    assert rows[miss].status == "no_match"  # "Obscure Pamphlet" is not "Good Omens"

    before = ub.session.query(ub.MetadataSuggestion).count()
    again = task_mod.TaskSuggestMetadata(batch_size=10)
    again.start(None)
    assert again.stat == STAT_FINISH_SUCCESS and ub.session.query(ub.MetadataSuggestion).count() == before


def test_task_skips_books_that_already_have_a_description(env, monkeypatch):
    from cps.tasks import suggest_metadata as task_mod
    monkeypatch.setattr(task_mod, "REQUEST_DELAY_SECONDS", 0)
    book = env.add_book("Described", author="Ann")
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.execute("INSERT INTO comments (book, text) VALUES (?, 'Already here')", (book,))
    con.commit()
    con.close()
    monkeypatch.setattr(task_mod, "usable_providers", lambda: [_FakeProvider([_record("Described", ["Ann"])])])
    task_mod.TaskSuggestMetadata(batch_size=10).start(None)
    assert env.ub.session.query(env.ub.MetadataSuggestion).count() == 0


def test_task_fails_cleanly_with_no_providers(env, monkeypatch):
    from cps.services.worker import STAT_FAIL
    from cps.tasks import suggest_metadata as task_mod
    monkeypatch.setattr(task_mod, "usable_providers", lambda: [])
    t = task_mod.TaskSuggestMetadata()
    t.start(None)
    assert t.stat == STAT_FAIL
