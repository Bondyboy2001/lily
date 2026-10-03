"""Import & Metadata → Rebuild metadata: the task, its route and the forced lookup."""
from types import SimpleNamespace

import pytest

from .lily_env import lily_env, ADMIN_PASSWORD
from .metadata_fakes import FakeProvider


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        yield env


def _login(env, name=None, password=ADMIN_PASSWORD):
    c = env.app.test_client()
    c.post("/login", data={"username": name or env.admin().name, "password": password})
    return c


@pytest.mark.unit
def test_rebuild_task_looks_up_every_book(env, monkeypatch):
    from cps import metadata_helper
    from cps.services.worker import STAT_FINISH_SUCCESS
    from cps.tasks.metadata_rebuild import TaskRebuildMetadata
    ids = [env.add_book(t) for t in ("One", "Two", "Three")]
    calls = []

    def fake_fetch(book_id, force=False, unanswered=None):
        calls.append((book_id, force))
        return book_id == ids[1]
    monkeypatch.setattr(metadata_helper, "fetch_and_apply_metadata", fake_fetch)
    monkeypatch.setattr("cps.duplicate_index.mark_duplicate_index_pending", lambda reason=None: None)

    task = TaskRebuildMetadata()
    with env.app.test_request_context():
        task.start(None)
        assert task.stat == STAT_FINISH_SUCCESS and task.progress == 1
        assert sorted(calls) == [(i, True) for i in ids]
        assert (task.checked, task.updated) == (3, 1)
        assert str(task.message) == "Done: 3 books checked, 1 updated"


@pytest.mark.unit
def test_rebuild_looks_several_books_up_at_once(env, monkeypatch):
    import threading
    from cps import metadata_helper
    from cps.tasks.metadata_rebuild import TaskRebuildMetadata
    for title in ("One", "Two", "Three", "Four"):
        env.add_book(title)
    # Each lookup waits for a second one to be under way, which a book-by-book rebuild never has
    overlap = threading.Barrier(2, timeout=5)

    def fake_fetch(book_id, force=False, unanswered=None):
        overlap.wait()
        return False
    monkeypatch.setattr(metadata_helper, "fetch_and_apply_metadata", fake_fetch)

    task = TaskRebuildMetadata(workers=2)
    with env.app.test_request_context():
        task.start(None)
    assert task.checked == 4, task.error


@pytest.mark.unit
def test_rebuild_route_queues_one_task_for_admins(env, monkeypatch):
    from cps.services.worker import WorkerThread
    queued = []
    monkeypatch.setattr(WorkerThread, "add_parallel", classmethod(lambda cls, user, task: queued.append(task)))
    c = _login(env)
    data = c.post("/cwa-settings/rebuild-metadata").get_json()
    assert data["success"] and data["task_id"] == str(queued[0].id)
    assert type(queued[0]).__name__ == "TaskRebuildMetadata"

    running = [(1, "admin", None, queued[0], False)]
    monkeypatch.setattr(WorkerThread, "tasks", property(lambda self: running))
    assert c.post("/cwa-settings/rebuild-metadata").get_json() == {"success": True, "running": True}
    assert len(queued) == 1

    status = "/cwa-settings/rebuild-metadata/status"
    assert c.get(status).get_json() == {"state": "running", "message": "Waiting to start…", "resume": "",
                                        "kind": "metadata"}

    from cps.services.worker import STAT_ENDED, STAT_STARTED, STAT_STOPPING
    queued[0].stat = STAT_STARTED
    assert c.post("/cwa-settings/rebuild-metadata/stop").get_json() == {"success": True, "stopped": 1}
    assert queued[0].stat == STAT_STOPPING
    # Still on its last book: no second rebuild beside it until it has finished, and the
    # page keeps following it rather than offering Rebuild again
    assert c.post("/cwa-settings/rebuild-metadata").get_json() == {"success": True, "running": True}
    assert c.get(status).get_json()["state"] == "stopping"
    queued[0].stat = STAT_ENDED  # as when its run() returns
    assert c.get(status).get_json()["state"] == "stopped"
    assert c.post("/cwa-settings/rebuild-metadata").get_json()["task_id"] == str(queued[1].id)
    queued.pop()

    env.add_user("reader", password="pw")
    resp = _login(env, "reader", "pw").post("/cwa-settings/rebuild-metadata")
    assert resp.status_code in (302, 403) and len(queued) == 1


@pytest.mark.unit
def test_forced_lookup_runs_with_auto_fetch_off_and_skips_unknown_author(env, monkeypatch):
    from cps import metadata_helper
    queries = []
    provider = FakeProvider(__id__="google", __name__="Google", identifier_types=frozenset(),
                            search=lambda q, *a: queries.append(q) or [])
    monkeypatch.setattr(metadata_helper, "metadata_providers", [provider])
    monkeypatch.setattr(metadata_helper, "pdf_first_page_text", lambda book: "")
    settings = {"auto_metadata_fetch_enabled": 0}
    monkeypatch.setattr(metadata_helper, "CWA_DB", lambda: SimpleNamespace(get_cwa_settings=lambda: settings))
    book_id = env.add_book("Abstract Algebra", author="Unknown")

    assert metadata_helper.fetch_and_apply_metadata(book_id) is False
    assert queries == []  # the new-books switch still governs imports
    metadata_helper.fetch_and_apply_metadata(book_id, force=True)
    assert queries == ["Abstract Algebra"]


@pytest.mark.unit
def test_settings_page_offers_the_rebuild(env):
    html = _login(env).get("/cwa-settings").get_data(as_text=True)
    assert 'id="rebuild_metadata"' in html and 'data-url="/cwa-settings/rebuild-metadata"' in html
    assert 'id="rebuildMetadataModal"' in html and "js/lily-metadata-rebuild.js" in html
    assert 'data-status-url="/cwa-settings/rebuild-metadata/status"' in html
    # The dialog can offer to carry on a stopped rebuild; there is no Start again
    assert "rebuild_metadata_restart" not in html and 'data-continue-label="Continue"' in html


@pytest.mark.unit
def test_status_tells_how_the_last_rebuild_ended(env, monkeypatch):
    from cps.services.worker import WorkerThread
    from cps.tasks.metadata_rebuild import TaskRebuildMetadata
    c = _login(env)
    status = "/cwa-settings/rebuild-metadata/status"
    monkeypatch.setattr(WorkerThread, "tasks", property(lambda self: []))
    assert c.get(status).get_json() == {"state": "idle", "message": "", "resume": ""}

    env.add_book("One")
    monkeypatch.setattr("cps.metadata_helper.fetch_and_apply_metadata", lambda book_id, force=False, unanswered=None: False)
    task = TaskRebuildMetadata(workers=1)
    monkeypatch.setattr(WorkerThread, "tasks", property(lambda self: [(1, "admin", None, task, False)]))
    with env.app.test_request_context():
        task.start(None)
    assert c.get(status).get_json() == {"state": "done", "message": "Done: 1 books checked, 0 updated",
                                        "resume": "", "kind": "metadata"}

    task._handleError("disk full")
    assert c.get(status).get_json() == {"state": "failed", "message": "The rebuild failed: disk full",
                                        "resume": "", "kind": "metadata"}
    env.add_user("reader", password="pw")
    assert _login(env, "reader", "pw").get(status).status_code in (302, 403)


def _provider_returning(**found):
    record = SimpleNamespace(title="", authors=[], tags=[], series="",
                             series_index=0, publishedDate=None, identifiers={}, cover=None,
                             source=SimpleNamespace(description="Google Books"))
    record.__dict__.update(found)
    return FakeProvider(__id__="google", __name__="Google", identifier_types=frozenset(),
                           search=lambda q, *a: [record])


@pytest.mark.unit
def test_rebuilt_book_moves_folder_sorts_author_and_queues_the_file_write(env, monkeypatch, tmp_path):
    from cps import metadata_helper
    from cps.tasks import metadata_rebuild
    settings = {"auto_metadata_fetch_enabled": 0, "auto_metadata_enforcement": 1}
    monkeypatch.setattr(metadata_helper, "CWA_DB", lambda: SimpleNamespace(get_cwa_settings=lambda: settings))
    monkeypatch.setattr(metadata_helper, "metadata_providers",
                        [_provider_returning(title="Abstract Algebra", authors=["Alexander Paulin"])])
    # Its title page names the author it lacks
    monkeypatch.setattr(metadata_helper, "pdf_first_page_text", lambda book: "Abstract Algebra\nAlexander Paulin")
    logs = tmp_path / "change_logs"
    logs.mkdir()
    monkeypatch.setattr(metadata_helper, "CHANGE_LOGS_DIR", str(logs))
    monkeypatch.setattr("cps.duplicate_index.mark_duplicate_index_pending", lambda reason=None: None)
    book_id = env.add_book("Abstract Algebra", author="Unknown")
    old_dir = env.library_dir / "Unknown" / "Abstract Algebra"
    old_dir.mkdir(parents=True)
    (old_dir / "Abstract Algebra.epub").write_bytes(b"epub")

    task = metadata_rebuild.TaskRebuildMetadata()
    with env.app.test_request_context():
        task.start(None)
    assert task.updated == 1, task.error

    import sqlite3
    con = sqlite3.connect(env.library_dir / "metadata.db")
    path, author_sort = con.execute("SELECT path, author_sort FROM books WHERE id=?", (book_id,)).fetchone()
    con.close()
    assert path == "Alexander Paulin/Abstract Algebra (%d)" % book_id
    assert author_sort == "Paulin, Alexander"
    assert (env.library_dir / path).is_dir() and not old_dir.exists()
    [log] = list(logs.iterdir())
    assert log.name.endswith("-%d.json" % book_id)
    assert '"authors": "Alexander Paulin"' in log.read_text()


@pytest.mark.unit
def test_parallel_task_runs_beside_the_queue_and_is_listed():
    import threading
    from cps.services.worker import CalibreTask, WorkerThread, STAT_FINISH_SUCCESS
    release = threading.Event()

    class Slow(CalibreTask):
        name = "Slow"
        is_cancellable = False

        def run(self, worker_thread):
            release.wait(5)
            self._handleSuccess()

    task = Slow("slow")
    thread = WorkerThread.add_parallel("admin", task)
    assert any(t is task for __, __, __, t, __ in WorkerThread.get_instance().tasks)
    release.set()
    thread.join(5)
    assert task.stat == STAT_FINISH_SUCCESS


@pytest.mark.unit
def test_stop_finishes_the_books_under_way_and_reports_it(env, monkeypatch):
    import threading
    from cps import metadata_helper
    from cps.services.worker import STAT_ENDED
    from cps.tasks.metadata_rebuild import TaskRebuildMetadata
    for title in ("One", "Two", "Three"):
        env.add_book(title)
    started, release = threading.Event(), threading.Event()

    def fake_fetch(book_id, force=False, unanswered=None):
        started.set()
        release.wait(5)
        return False
    monkeypatch.setattr(metadata_helper, "fetch_and_apply_metadata", fake_fetch)

    task = TaskRebuildMetadata(workers=1)

    def run():
        with env.app.test_request_context():
            task.start(None)
    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(5)
    task.stop()
    # At work until the book under way is done: not yet ended, nor to be cleared from the list
    assert task.state == "stopping" and not task.dead
    release.set()
    thread.join(5)
    assert task.state == "stopped" and task.stat == STAT_ENDED and task.dead
    assert str(task.message) == "Stopped: 1 of 3 books checked, 0 updated"


@pytest.mark.unit
def test_one_book_failing_does_not_end_the_run(env, monkeypatch):
    from cps import metadata_helper
    from cps.services.worker import STAT_FINISH_SUCCESS
    from cps.tasks.metadata_rebuild import TaskRebuildMetadata
    ids = [env.add_book(t) for t in ("One", "Two", "Three")]

    def fake_fetch(book_id, force=False, unanswered=None):
        if book_id == ids[0]:
            raise RuntimeError("disk full")
        return True
    monkeypatch.setattr(metadata_helper, "fetch_and_apply_metadata", fake_fetch)
    monkeypatch.setattr("cps.duplicate_index.mark_duplicate_index_pending", lambda reason=None: None)

    task = TaskRebuildMetadata(workers=1)
    with env.app.test_request_context():
        task.start(None)
    assert task.stat == STAT_FINISH_SUCCESS, task.error
    assert (task.checked, task.updated) == (3, 2)


def _run_rebuild(env, monkeypatch, stop_after=None, fail=None, **options):
    """Runs a rebuild with a stand-in lookup; returns (task, the book ids looked up). With
    stop_after, Stop is pressed while that book is being looked up."""
    from cps import metadata_helper
    from cps.tasks.metadata_rebuild import TaskRebuildMetadata
    task = TaskRebuildMetadata(workers=1, **options)
    looked_up = []
    task.overwrote = []

    def fake_fetch(book_id, force=False, unanswered=None, overwrite=False):
        looked_up.append(book_id)
        task.overwrote.append(overwrite)
        if book_id == stop_after:
            task.stop()
        if fail and book_id in fail:
            unanswered.update(fail[book_id])
        return True
    monkeypatch.setattr(metadata_helper, "fetch_and_apply_metadata", fake_fetch)
    monkeypatch.setattr("cps.duplicate_index.mark_duplicate_index_pending", lambda reason=None: None)
    with env.app.test_request_context():
        task.start(None)
        task.message = str(task.message)
    return task, looked_up


@pytest.mark.unit
def test_a_stopped_rebuild_is_carried_on_from_where_it_got_to(env, monkeypatch):
    from cps.tasks.metadata_rebuild import saved_progress
    ids = [env.add_book(t) for t in ("One", "Two", "Three", "Four")]
    task, looked_up = _run_rebuild(env, monkeypatch, stop_after=ids[1])
    assert looked_up == ids[:2] and task.message == "Stopped: 2 of 4 books checked, 2 updated"
    assert saved_progress() == {"next_book_id": ids[2], "checked": 2, "updated": 2, "covers": 0, "total": 4,
                                "full": False, "done": []}

    status = _login(env).get("/cwa-settings/rebuild-metadata/status").get_json()
    assert status == {"state": "idle", "message": "The last rebuild stopped after 2 of 4 books.",
                      "resume": "The last rebuild stopped after 2 of 4 books."}

    # A book imported meanwhile is checked too; the counts go on from the first run's
    ids.append(env.add_book("Five"))
    task, looked_up = _run_rebuild(env, monkeypatch, resume=True)
    assert looked_up == ids[2:] and task.message == "Done: 5 books checked, 5 updated"
    assert saved_progress() is None


@pytest.mark.unit
def test_start_again_ignores_the_saved_progress(env, monkeypatch):
    from cps.tasks.metadata_rebuild import saved_progress
    ids = [env.add_book(t) for t in ("One", "Two", "Three")]
    _run_rebuild(env, monkeypatch, stop_after=ids[0])
    assert saved_progress()["next_book_id"] == ids[1]
    task, looked_up = _run_rebuild(env, monkeypatch)
    assert looked_up == ids and task.message == "Done: 3 books checked, 3 updated"
    assert saved_progress() is None
    # Nothing to carry on: Continue is a full run
    assert _run_rebuild(env, monkeypatch, resume=True)[1] == ids


@pytest.mark.unit
def test_the_start_route_passes_on_continue(env, monkeypatch):
    from cps.services.worker import WorkerThread
    queued = []
    monkeypatch.setattr(WorkerThread, "add_parallel", classmethod(lambda cls, user, task: queued.append(task)))
    monkeypatch.setattr(WorkerThread, "tasks", property(lambda self: []))
    c = _login(env)
    c.post("/cwa-settings/rebuild-metadata", data={"resume": "1"})
    c.post("/cwa-settings/rebuild-metadata")
    assert [task.resume for task in queued] == [True, False]


@pytest.mark.unit
def test_the_status_line_names_providers_that_did_not_answer(env, monkeypatch):
    ids = [env.add_book(t) for t in ("One", "Two", "Three")]
    task, __ = _run_rebuild(env, monkeypatch, fail={ids[0]: {"Google"}, ids[2]: {"Google", "Open Library"}})
    assert task.unanswered == {"Google": 2, "Open Library": 1}
    assert task.message == "Done: 3 books checked, 3 updated. No answer from Google (2), Open Library (1)"


@pytest.mark.unit
def test_rebuild_route_looks_up_only_the_books_ticked_in_the_table(env, monkeypatch):
    from cps.services.worker import WorkerThread
    queued = []
    monkeypatch.setattr(WorkerThread, "add_parallel", classmethod(lambda cls, user, task: queued.append(task)))
    monkeypatch.setattr(WorkerThread, "tasks", property(lambda self: []))
    c = _login(env)
    assert c.post("/cwa-settings/rebuild-metadata", data={"book_ids": " ,x"}).get_json() == \
        {"success": True, "none": True}
    assert not queued
    c.post("/cwa-settings/rebuild-metadata", data={"book_ids": "9,3,x,5"})
    assert queued[0].book_ids == [3, 5, 9] and queued[0].name == "Look up selected books"


@pytest.mark.unit
def test_retry_failed_looks_up_only_the_failed_books(env, monkeypatch):
    from cwa_db import CWA_DB
    from cps.services.worker import WorkerThread
    queued = []
    monkeypatch.setattr(WorkerThread, "add_parallel", classmethod(lambda cls, user, task: queued.append(task)))
    monkeypatch.setattr(WorkerThread, "tasks", property(lambda self: []))
    c = _login(env)
    assert c.post("/cwa-settings/rebuild-metadata", data={"failed": "1"}).get_json() == {"success": True, "none": True}
    one, two, three = (env.add_book(t) for t in ("One", "Two", "Three"))
    store = CWA_DB()
    store.save_metadata_lookup(three, "failed")
    store.save_metadata_lookup(one, "failed")
    store.save_metadata_lookup(two, "nomatch")
    c.post("/cwa-settings/rebuild-metadata", data={"failed": "1"})
    assert queued[0].book_ids == [one, three] and queued[0].name == "Retry failed lookups"
    html = c.get("/cwa-settings").get_data(as_text=True)
    assert 'id="retry_failed"' in html


@pytest.mark.unit
def test_the_rebuild_confirmation_only_asks(env):
    for title in ("One", "Two"):
        env.add_book(title)
    html = _login(env).get("/cwa-settings").get_data(as_text=True)
    modal = html[html.index('id="rebuildMetadataModal"'):]
    # Just the question and its buttons: no explanation or time estimate
    assert 'id="rebuildMetadataWhat"' not in modal and 'id="rebuildMetadataTime"' not in modal
    assert "rebuild_metadata_full" in modal
    assert 'id="retry_failed"' not in html


@pytest.mark.unit
def test_a_rebuild_skips_books_up_to_date_and_a_full_one_does_not(env, monkeypatch):
    import sqlite3
    from cwa_db import CWA_DB
    matched, nomatch, failed, edited, unchecked, manual = (env.add_book(t) for t in ("A", "B", "C", "D", "E", "F"))
    store = CWA_DB()
    for book, status in ((matched, "matched"), (nomatch, "nomatch"), (failed, "failed"), (edited, "matched"),
                         (manual, "manual")):
        store.save_metadata_lookup(book, status)
    store.save_cover_check(matched, "http://cover", "1:2")
    # Edited after its lookup
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.create_function("title_sort", 1, lambda t: t)  # calibre's triggers call it
    con.execute("UPDATE books SET last_modified='2999-01-01 00:00:00+00:00' WHERE id=?", (edited,))
    con.commit()
    con.close()
    task, looked_up = _run_rebuild(env, monkeypatch)
    assert looked_up == [failed, edited, unchecked] and task.skipped == 3
    assert task.message == "Done: 3 books checked, 3 updated, 3 up to date skipped"
    task, looked_up = _run_rebuild(env, monkeypatch, full=True)
    assert looked_up == [matched, nomatch, failed, edited, unchecked, manual] and task.skipped == 0
    # What earlier lookups found is forgotten (the stand-in lookup notes nothing)
    assert store.get_metadata_lookup(matched) is None and store.get_cover_check(matched) is None


@pytest.mark.unit
def test_the_full_rebuild_button_is_offered_and_sent(env, monkeypatch):
    from cps.services.worker import WorkerThread
    queued = []
    monkeypatch.setattr(WorkerThread, "add_parallel", classmethod(lambda cls, user, task: queued.append(task)))
    monkeypatch.setattr(WorkerThread, "tasks", property(lambda self: []))
    c = _login(env)
    html = c.get("/cwa-settings").get_data(as_text=True)
    assert 'id="rebuild_metadata_full"' in html
    c.post("/cwa-settings/rebuild-metadata", data={"full": "1"})
    c.post("/cwa-settings/rebuild-metadata")
    assert [task.full for task in queued] == [True, False]


@pytest.mark.unit
def test_a_full_rebuild_overwrites_after_copying_the_library(env, monkeypatch):
    import os
    first, second = env.add_book("A"), env.add_book("B")
    task, looked_up = _run_rebuild(env, monkeypatch, full=True)
    assert looked_up == [first, second] and task.overwrote == [True, True]
    backup = os.path.join(os.environ["CWA_DB_PATH"], "metadata.db.before-full-rebuild")
    assert os.path.isfile(backup)
    import sqlite3
    con = sqlite3.connect(backup)
    try:
        assert con.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 2
    finally:
        con.close()
    # An ordinary rebuild only fills
    task, __ = _run_rebuild(env, monkeypatch)
    assert not any(task.overwrote)


@pytest.mark.unit
def test_continuing_a_stopped_full_rebuild_keeps_it_full(env, monkeypatch):
    from cps.tasks.metadata_rebuild import saved_progress
    ids = [env.add_book(t) for t in ("One", "Two", "Three", "Four")]
    task, looked_up = _run_rebuild(env, monkeypatch, stop_after=ids[1], full=True)
    assert task.overwrote == [True, True] and saved_progress()["full"] is True
    # The page's Continue sends only resume: the rest are still replaced, not filled
    task, looked_up = _run_rebuild(env, monkeypatch, resume=True)
    assert looked_up == ids[2:] and task.overwrote == [True, True]
    assert task.message == "Done: 4 books checked, 4 updated"


@pytest.mark.unit
def test_a_carried_on_rebuild_skips_the_books_already_checked_past_its_mark(env, monkeypatch):
    # Books run several at once, so a later one can finish before an earlier one
    from cwa_db import CWA_DB
    ids = [env.add_book(t) for t in ("One", "Two", "Three", "Four", "Five")]
    CWA_DB().save_rebuild_progress(ids[1], 2, 2, 0, 5, full=True, done=[ids[3]])
    task, looked_up = _run_rebuild(env, monkeypatch, resume=True)
    assert looked_up == [ids[1], ids[2], ids[4]]
    assert task.message == "Done: 5 books checked, 5 updated"


@pytest.mark.unit
def test_progress_is_saved_every_few_seconds_not_every_book(env, monkeypatch):
    from cwa_db import CWA_DB
    from cps.tasks import metadata_rebuild
    ids = [env.add_book(t) for t in ("One", "Two", "Three", "Four")]
    saves = []
    save = CWA_DB.save_rebuild_progress
    monkeypatch.setattr(CWA_DB, "save_rebuild_progress", lambda self, *a, **k: saves.append(a[0]) or save(self, *a, **k))
    monkeypatch.setattr(metadata_rebuild, "PROGRESS_EVERY", 3600)
    task, looked_up = _run_rebuild(env, monkeypatch, stop_after=ids[2])
    # Once at the first book, then when stopped
    assert looked_up == ids[:3] and saves == [ids[1], ids[3]]
