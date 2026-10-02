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

    task = TaskRebuildMetadata(pause=0)
    with env.app.test_request_context():
        task.start(None)
        assert task.stat == STAT_FINISH_SUCCESS and task.progress == 1
        assert calls == [(i, True) for i in ids]
        assert (task.checked, task.updated) == (3, 1)
        assert str(task.message) == "Done: 3 books checked, 1 updated"


@pytest.mark.unit
def test_rebuild_route_queues_one_task_for_admins(env, monkeypatch):
    from cps.services.worker import WorkerThread
    queued = []
    monkeypatch.setattr(WorkerThread, "add", classmethod(lambda cls, user, task, hidden=False: queued.append(task)))
    c = _login(env)
    data = c.post("/cwa-settings/rebuild-metadata").get_json()
    assert data["success"] and data["task_id"] == str(queued[0].id)
    assert type(queued[0]).__name__ == "TaskRebuildMetadata"

    monkeypatch.setattr(WorkerThread, "has_active_task_of_type", lambda self, name, extra_check=None: True)
    assert c.post("/cwa-settings/rebuild-metadata").get_json() == {"success": True, "running": True}
    assert len(queued) == 1

    env.add_user("reader", password="pw")
    resp = _login(env, "reader", "pw").post("/cwa-settings/rebuild-metadata")
    assert resp.status_code in (302, 403) and len(queued) == 1


@pytest.mark.unit
def test_forced_lookup_runs_with_auto_fetch_off_and_skips_unknown_author(env, monkeypatch):
    from cps import metadata_helper
    queries = []
    provider = SimpleNamespace(__id__="google", __name__="Google", active=True,
                               is_globally_enabled=lambda enabled: True,
                               search=lambda q, *a: queries.append(q) or [])
    monkeypatch.setattr(metadata_helper, "metadata_providers", [provider])
    settings = {"auto_metadata_fetch_enabled": 0, "metadata_provider_hierarchy": '["google"]'}
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
