"""Import & Metadata → Rebuild metadata: the task, its route and the forced lookup."""
from types import SimpleNamespace

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


@pytest.mark.unit
def test_rebuild_task_looks_up_every_book(env, monkeypatch):
    from cps import metadata_helper
    from cps.services.worker import STAT_FINISH_SUCCESS
    from cps.tasks.metadata_rebuild import TaskRebuildMetadata
    ids = [env.add_book(t) for t in ("One", "Two", "Three")]
    calls = []

    def fake_fetch(book_id, force=False):
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

    def fake_fetch(book_id, force=False):
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

    from cps.services.worker import STAT_CANCELLED
    assert c.post("/cwa-settings/rebuild-metadata/stop").get_json() == {"success": True, "stopped": 1}
    assert queued[0].stat == STAT_CANCELLED
    # Still on its last book: no second rebuild beside it until it has finished
    assert c.post("/cwa-settings/rebuild-metadata").get_json() == {"success": True, "running": True}
    queued[0].finished = True
    assert c.post("/cwa-settings/rebuild-metadata").get_json()["task_id"] == str(queued[1].id)
    queued.pop()

    env.add_user("reader", password="pw")
    resp = _login(env, "reader", "pw").post("/cwa-settings/rebuild-metadata")
    assert resp.status_code in (302, 403) and len(queued) == 1


@pytest.mark.unit
def test_forced_lookup_runs_with_auto_fetch_off_and_skips_unknown_author(env, monkeypatch):
    from cps import metadata_helper
    queries = []
    provider = SimpleNamespace(__id__="google", __name__="Google", identifier_types=frozenset(),
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


def _provider_returning(**found):
    record = SimpleNamespace(title="", authors=[], description="", publisher="", tags=[], series="",
                             series_index=0, publishedDate=None, identifiers={}, cover=None,
                             source=SimpleNamespace(description="Google Books"))
    record.__dict__.update(found)
    return SimpleNamespace(__id__="google", __name__="Google", identifier_types=frozenset(),
                           search=lambda q, *a: [record])


@pytest.mark.unit
def test_rebuilt_book_moves_folder_sorts_author_and_queues_the_file_write(env, monkeypatch, tmp_path):
    from cps import metadata_helper
    from cps.tasks import metadata_rebuild
    settings = {"auto_metadata_fetch_enabled": 0, "auto_metadata_enforcement": 1}
    monkeypatch.setattr(metadata_helper, "CWA_DB", lambda: SimpleNamespace(get_cwa_settings=lambda: settings))
    monkeypatch.setattr(metadata_helper, "metadata_providers",
                        [_provider_returning(title="Abstract Algebra", authors=["Alexander Paulin"])])
    monkeypatch.setattr(metadata_helper, "pdf_first_page_text", lambda book: "")
    monkeypatch.setattr(metadata_rebuild, "tidy_library_tags", lambda session: (0, 0))
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
