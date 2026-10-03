"""Settings → Metadata → Redo PDF covers: the task, its route and its status."""
import sqlite3

import pytest

from .lily_env import lily_env, ADMIN_PASSWORD


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        yield env


def _login(env, name=None, password=ADMIN_PASSWORD):
    c = env.app.test_client()
    c.post("/login", data={"username": name or env.admin().name, "password": password})
    return c


def _pdf_and_epub(env, title):
    """A book with both formats: its PDF is redone."""
    book_id = env.add_book(title, fmt="EPUB")
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.execute("INSERT INTO data (book, format, uncompressed_size, name) VALUES (?, 'PDF', 1, ?)",
                (book_id, title))
    con.commit()
    con.close()
    return book_id


def _run_covers(env, monkeypatch, task, fix=None):
    """Runs the given Redo PDF covers task with a stand-in fix_cover; the calls it made."""
    from cps import helper, pdf_cover
    monkeypatch.setattr(pdf_cover, "available", lambda: True)
    monkeypatch.setattr("cps.metadata_helper.fetch_and_apply_metadata",
                        lambda *a, **kw: pytest.fail("covers must not be looked up"))
    monkeypatch.setattr(helper, "replace_cover_thumbnail_cache", lambda book_id: None)
    calls = []

    def fake_fix(pdf_path, cover_path, has_cover, replace=False):
        calls.append(pdf_path)
        return fix(pdf_path, cover_path, has_cover, replace) if fix else True
    monkeypatch.setattr(pdf_cover, "fix_cover", fake_fix)
    with env.app.test_request_context():
        task.start(None)
        task.message = str(task.message)
    return calls


@pytest.mark.unit
def test_redo_covers_touches_only_pdf_books(env, monkeypatch):
    from cwa_db import CWA_DB
    from cps.services.worker import STAT_FINISH_SUCCESS
    from cps.tasks.pdf_covers import TaskRedoPdfCovers
    paper = env.add_book("Paper", fmt="PDF")
    novel = env.add_book("Novel", fmt="EPUB")
    both = _pdf_and_epub(env, "Both")
    con = sqlite3.connect(env.library_dir / "metadata.db")
    untouched = {i: lm for i, lm in con.execute("SELECT id, last_modified FROM books")}
    con.close()

    task = TaskRedoPdfCovers()
    calls = _run_covers(env, monkeypatch, task,
                        fix=lambda pdf_path, cover_path, has_cover, replace: "Paper" in pdf_path)
    assert task.stat == STAT_FINISH_SUCCESS and task.covers == 1 and task.total == 2
    assert sorted(calls) == sorted(
        [str(env.library_dir / "Test Author" / "Paper" / "Paper.pdf"),
         str(env.library_dir / "Test Author" / "Both" / "Both.pdf")])
    assert task.message == "Done: 2 PDFs checked, 1 covers redone"
    con = sqlite3.connect(env.library_dir / "metadata.db")
    bumped = {i: lm for i, lm in con.execute("SELECT id, last_modified FROM books")}
    con.close()
    # The changed cover bumped its book; the other two books are untouched
    assert bumped[paper] != untouched[paper]
    assert bumped[novel] == untouched[novel] and bumped[both] == untouched[both]
    assert CWA_DB().get_rebuild_progress() is None


@pytest.mark.unit
def test_a_redone_cover_keeps_the_books_lookup_fresh(env, monkeypatch):
    from cwa_db import CWA_DB
    from cps.tasks.pdf_covers import TaskRedoPdfCovers
    paper = env.add_book("Paper", fmt="PDF")
    failed = env.add_book("Failed", fmt="PDF")
    store = CWA_DB()
    store.save_metadata_lookup(paper, "matched", "Open Library")
    store.save_metadata_lookup(failed, "failed")
    before_failed = store.get_metadata_lookup(failed)["checked_at"]
    _run_covers(env, monkeypatch, TaskRedoPdfCovers())

    record = store.get_metadata_lookup(paper)
    assert record["status"] == "matched" and record["source"] == "Open Library"
    # A failed lookup is not made to look fresh
    assert store.get_metadata_lookup(failed)["checked_at"] == before_failed

    # A normal rebuild now skips the redone book as up to date and still retries the failed one
    from cps import metadata_helper
    from cps.tasks.metadata_rebuild import TaskRebuildMetadata
    looked_up = []
    monkeypatch.setattr(metadata_helper, "fetch_and_apply_metadata",
                        lambda book_id, force=False, unanswered=None: looked_up.append(book_id) or False)
    monkeypatch.setattr("cps.duplicate_index.mark_duplicate_index_pending", lambda reason=None: None)
    rebuild = TaskRebuildMetadata(workers=1)
    with env.app.test_request_context():
        rebuild.start(None)
    assert looked_up == [failed] and rebuild.skipped == 1


@pytest.mark.unit
def test_redo_covers_stops_after_the_book_under_way(env, monkeypatch):
    from cps.services.worker import STAT_ENDED
    from cps.tasks.pdf_covers import TaskRedoPdfCovers
    for title in ("One", "Two", "Three"):
        env.add_book(title, fmt="PDF")
    task = TaskRedoPdfCovers(workers=1)
    calls = _run_covers(env, monkeypatch, task,
                        fix=lambda *a: task.stop() or False)
    assert task.stat == STAT_ENDED and len(calls) == 1
    assert task.message == "Stopped: 1 of 3 PDFs checked, 0 covers redone"


@pytest.mark.unit
def test_redo_covers_fails_without_imagemagick(env, monkeypatch):
    from cps import pdf_cover
    from cps.services.worker import STAT_FAIL
    from cps.tasks.pdf_covers import TaskRedoPdfCovers
    env.add_book("Paper", fmt="PDF")
    monkeypatch.setattr(pdf_cover, "available", lambda: False)
    called = []
    monkeypatch.setattr(pdf_cover, "fix_cover", lambda *a, **kw: called.append(1))
    task = TaskRedoPdfCovers()
    with env.app.test_request_context():
        task.start(None)
    assert task.stat == STAT_FAIL and not called
    assert str(task.error) == "PDF covers can’t be made here: ImageMagick is missing"


@pytest.mark.unit
def test_settings_page_offers_redo_pdf_covers(env):
    html = _login(env).get("/cwa-settings").get_data(as_text=True)
    assert 'id="redo_pdf_covers"' in html and 'data-url="/cwa-settings/redo-pdf-covers"' in html
    assert 'id="redo_pdf_covers_stop"' in html


@pytest.mark.unit
def test_redo_pdf_covers_route_queues_the_task(env, monkeypatch):
    from cps.services.worker import WorkerThread
    from cps.tasks.pdf_covers import TaskRedoPdfCovers
    queued = []
    monkeypatch.setattr(WorkerThread, "add_parallel", classmethod(lambda cls, user, task: queued.append(task)))
    monkeypatch.setattr(WorkerThread, "tasks", property(lambda self: []))
    c = _login(env)
    data = c.post("/cwa-settings/redo-pdf-covers").get_json()
    assert data["success"] and data["task_id"] == str(queued[0].id)
    assert isinstance(queued[0], TaskRedoPdfCovers)

    # While a rebuild is under way, nothing is queued beside it
    from cps.tasks.metadata_rebuild import TaskRebuildMetadata
    running = [(1, "admin", None, TaskRebuildMetadata(), False)]
    running[0][3].stat = 2  # STAT_STARTED
    monkeypatch.setattr(WorkerThread, "tasks", property(lambda self: running))
    assert c.post("/cwa-settings/redo-pdf-covers").get_json() == {"success": True, "running": True}
    assert len(queued) == 1

    env.add_user("reader", password="pw")
    resp = _login(env, "reader", "pw").post("/cwa-settings/redo-pdf-covers")
    assert resp.status_code in (302, 403) and len(queued) == 1


@pytest.mark.unit
def test_the_status_line_says_which_kind_of_run_it_is(env, monkeypatch):
    from cps.services.worker import WorkerThread
    from cps.tasks.metadata_rebuild import TaskRebuildMetadata
    from cps.tasks.pdf_covers import TaskRedoPdfCovers
    c = _login(env)
    status = "/cwa-settings/rebuild-metadata/status"
    for task, kind in ((TaskRebuildMetadata(), "metadata"), (TaskRedoPdfCovers(), "covers")):
        task.stat = 2  # STAT_STARTED
        monkeypatch.setattr(WorkerThread, "tasks", property(lambda self, t=task: [(1, "admin", None, t, False)]))
        data = c.get(status).get_json()
        assert data["kind"] == kind and data["state"] == "running"


@pytest.mark.unit
def test_redo_covers_does_several_books_at_once(env, monkeypatch):
    import threading
    from cps.services.worker import STAT_FINISH_SUCCESS
    from cps.tasks.pdf_covers import TaskRedoPdfCovers, default_workers
    for title in ("One", "Two", "Three", "Four", "Five"):
        env.add_book(title, fmt="PDF")
    # Three books meet at the barrier, so the run only gets through if they render together
    barrier = threading.Barrier(3, timeout=5)

    def fix(pdf_path, *a):
        if any(t in pdf_path for t in ("One", "Two", "Three")):
            barrier.wait()
        return "Four" in pdf_path
    task = TaskRedoPdfCovers(workers=3)
    calls = _run_covers(env, monkeypatch, task, fix=fix)
    assert task.stat == STAT_FINISH_SUCCESS and len(calls) == 5 and task.covers == 1
    assert task.message == "Done: 5 PDFs checked, 1 covers redone"
    # Renders wait on the disk, so more run than there are cores
    assert 2 <= default_workers() <= 8


@pytest.mark.unit
def test_a_redo_worker_opens_cwa_db_once_not_once_a_book(env, monkeypatch):
    from cps.tasks import pdf_covers
    monkeypatch.setattr(pdf_covers, "_local", __import__("threading").local())
    opened = []
    real = pdf_covers.CWA_DB
    monkeypatch.setattr(pdf_covers, "CWA_DB", lambda: opened.append(1) or real())
    ids = [env.add_book(t, fmt="PDF") for t in ("One", "Two", "Three")]
    asked = []
    monkeypatch.setattr(pdf_covers.pdf_cover, "_hand_cover", lambda book_id, store=None: asked.append(store) or False)
    monkeypatch.setattr(pdf_covers.pdf_cover, "fix_cover", lambda *a: False)
    for book_id in ids:
        pdf_covers._redo_one(book_id)
    assert len(opened) == 1 and len(asked) == 3 and all(s is asked[0] and s is not None for s in asked)


@pytest.mark.unit
def test_redo_covers_records_in_batches_and_drops_only_the_changed_books_thumbnails(env, monkeypatch):
    from cps import constants, ub
    from cps.tasks import pdf_covers
    from cps.tasks.pdf_covers import TaskRedoPdfCovers
    # A batch of two, so five changed covers take three commits
    monkeypatch.setattr(pdf_covers, "RECORD_BATCH", 2)
    changed = [env.add_book(f"Paper {i}", fmt="PDF") for i in range(5)]
    kept = env.add_book("Kept", fmt="PDF")
    for book_id in changed + [kept]:
        ub.session.add(ub.Thumbnail(entity_id=book_id, type=constants.THUMBNAIL_TYPE_COVER,
                                    filename=f"t{book_id}.jpg"))
    ub.session.commit()
    commits = []
    real_record = TaskRedoPdfCovers._record_covers
    monkeypatch.setattr(TaskRedoPdfCovers, "_record_covers",
                        lambda self, cdb: commits.append(len(self._changed)) or real_record(self, cdb))
    task = TaskRedoPdfCovers(workers=1)
    _run_covers(env, monkeypatch, task, fix=lambda pdf_path, *a: "Paper" in pdf_path)
    assert task.covers == 5 and task.message == "Done: 6 PDFs checked, 5 covers redone"
    assert [n for n in commits if n] == [2, 2, 1]
    left = {t.entity_id for t in ub.session.query(ub.Thumbnail).all()}
    assert left == {kept}


@pytest.mark.unit
def test_a_dropped_cwa_db_closes_its_connection(temp_cwa_db):
    # Most callers make one per use; left open, a long run used up the process's files
    import sqlite3
    from cwa_db import CWA_DB
    store = CWA_DB()
    con = store.con
    del store
    with pytest.raises(sqlite3.ProgrammingError):
        con.execute("SELECT 1")


@pytest.mark.unit
def test_a_background_tasks_app_db_session_keeps_no_connection_pool():
    from sqlalchemy.pool import NullPool
    from cps import ub
    session = ub.get_new_session_instance()
    try:
        assert isinstance(session.get_bind().pool, NullPool)
    finally:
        session.remove()
