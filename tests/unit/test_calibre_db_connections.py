"""metadata.db sessions get a connection each: one thread ending or rolling back its session
must not touch another's unsaved changes (they used to share one SQLite connection, so a
rebuild finishing a book could silently undo a web edit being saved)."""
import sqlite3
import threading

import pytest

from .lily_env import lily_env

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as env:
        yield env


def _title(env, book_id):
    con = sqlite3.connect(env.library_dir / "metadata.db")
    try:
        return con.execute("SELECT title FROM books WHERE id=?", (book_id,)).fetchone()[0]
    finally:
        con.close()


def _in_thread(fn):
    errors = []

    def run():
        try:
            fn()
        except Exception as ex:  # surfaced in the test thread
            errors.append(ex)
    thread = threading.Thread(target=run)
    thread.start()
    return thread, errors


def test_a_session_ending_elsewhere_keeps_an_edit_being_saved(env):
    from cps import db
    book_id = env.add_book("Old title")
    web = db.CalibreDB.session_factory()
    book = web.get(db.Books, book_id)
    book.title = "New title"
    web.flush()  # sent, not yet committed: an edit mid-save

    def rebuild_finishes_a_book():
        session = db.CalibreDB.session_factory()
        session.query(db.Books).first()
        db.CalibreDB.session_factory.remove()  # hands its connection back: a rollback
    thread, errors = _in_thread(rebuild_finishes_a_book)
    thread.join(10)
    assert not errors
    web.commit()
    db.CalibreDB.session_factory.remove()
    assert _title(env, book_id) == "New title"


def test_a_rollback_elsewhere_keeps_an_edit_being_saved(env):
    from cps import db
    book_id, other_id = env.add_book("Old title"), env.add_book("Other")
    web = db.CalibreDB.session_factory()
    web.get(db.Books, book_id).title = "New title"
    web.flush()

    def a_book_fails():
        session = db.CalibreDB.session_factory()
        session.get(db.Books, other_id).title = "Half done"
        session.flush()
        session.rollback()
        db.CalibreDB.session_factory.remove()
    thread, errors = _in_thread(a_book_fails)
    # The other thread waits for this one's write lock, then rolls back only its own change
    web.commit()
    db.CalibreDB.session_factory.remove()
    thread.join(10)
    assert not errors
    assert (_title(env, book_id), _title(env, other_id)) == ("New title", "Other")


def test_a_writer_waits_for_another_instead_of_failing(env):
    from cps import db
    first, second = env.add_book("One"), env.add_book("Two")
    web = db.CalibreDB.session_factory()
    web.get(db.Books, first).title = "One edited"
    web.flush()  # holds the write lock
    started = threading.Event()

    def rebuild_writes():
        session = db.CalibreDB.session_factory()
        session.get(db.Books, second).title = "Two edited"
        started.set()
        session.commit()  # waits on SQLite's busy timeout until the web edit commits
        db.CalibreDB.session_factory.remove()
    thread, errors = _in_thread(rebuild_writes)
    assert started.wait(10)
    web.commit()
    db.CalibreDB.session_factory.remove()
    thread.join(10)
    assert not errors
    assert (_title(env, first), _title(env, second)) == ("One edited", "Two edited")


def test_every_connection_is_set_up_like_the_first(env):
    # Each thread's connection has the library attached and calibre's SQL functions:
    # books triggers call title_sort and uuid4, and search uses an accent-blind lower
    from sqlalchemy import text
    from cps import db
    results = []

    def other_thread():
        session = db.CalibreDB.session_factory()
        book = db.Books("The Hobbit", "", "", None, None, "1.0", None, "Tolkien/The Hobbit", 0,
                        authors=None, tags=None, languages=None)
        session.add(book)
        session.commit()
        results.append(session.execute(text("SELECT sort, uuid FROM books WHERE id=:id"),
                                       {"id": book.id}).one())
        results.append(session.execute(text("SELECT lower('Émile')")).scalar())
        results.append(session.execute(text("SELECT count(*) FROM app_settings.settings")).scalar())
        db.CalibreDB.session_factory.remove()
    thread, errors = _in_thread(other_thread)
    thread.join(10)
    assert not errors, errors
    (sort, uuid), lowered, settings_rows = results
    assert sort == "Hobbit, The" and uuid
    assert lowered == "emile"
    assert settings_rows >= 1
