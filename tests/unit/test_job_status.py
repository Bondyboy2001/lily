# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Background job status: the cwa.db record, the admin banner and /health checks."""

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import job_status as js  # noqa: E402

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


def test_record_and_read(con):
    assert js.read_status(con) == {}  # no table yet
    js.record_start(con, "db_backup", NOW - timedelta(minutes=5))
    js.record_success(con, "db_backup", NOW)
    js.record_error(con, "library_mirror", "x" * 5000, NOW)
    status = js.read_status(con)
    assert status["db_backup"]["last_started"] == NOW - timedelta(minutes=5)
    assert status["db_backup"]["last_success"] == NOW and status["db_backup"]["last_error_at"] is None
    assert len(status["library_mirror"]["last_error"]) == js.MAX_ERROR_LENGTH
    # Later events update the same row
    js.record_success(con, "library_mirror", NOW + timedelta(hours=1))
    assert js.read_status(con)["library_mirror"]["last_error_at"] == NOW


def test_parse_time():
    assert js.parse_time(None) is None and js.parse_time("garbage") is None
    assert js.parse_time("2026-10-01T12:00:00") == NOW  # naive values are UTC


def _row(success=None, error_at=None, started=None, error=""):
    return {"last_started": started, "last_success": success, "last_error_at": error_at, "last_error": error}


@pytest.mark.parametrize("row,expected", [
    (None, "never_run"),
    (_row(success=NOW - timedelta(hours=2)), "ok"),
    (_row(success=NOW - timedelta(hours=37)), "stale"),
    (_row(success=NOW - timedelta(hours=2), error_at=NOW - timedelta(hours=1)), "failed"),
    (_row(success=NOW - timedelta(hours=1), error_at=NOW - timedelta(hours=2)), "ok"),
    (_row(started=NOW - timedelta(hours=1)), "never_run"),  # first run still going
    (_row(started=NOW - timedelta(hours=40)), "stale"),    # started long ago, never succeeded
])
def test_job_state_nightly(row, expected):
    assert js.job_state(row, js.JOBS["db_backup"], NOW) == expected


def test_non_nightly_jobs_are_only_reported_when_failing():
    old = _row(success=NOW - timedelta(days=30))
    assert js.job_state(old, js.JOBS["duplicate_scan"], NOW) == "ok"
    status = {"duplicate_scan": old, "thumbnails": _row(error_at=NOW, error="boom")}
    problems = js.job_problems(status, {"duplicate_scan", "thumbnails"}, NOW)
    assert [(p["job"], p["state"], p["last_error"]) for p in problems] == [("thumbnails", "failed", "boom")]


def test_job_problems_skip_disabled_and_unrecorded_jobs():
    status = {"library_mirror": _row(success=NOW - timedelta(days=3)),
              "db_backup": _row(success=NOW - timedelta(days=2))}
    assert [p["job"] for p in js.job_problems(status, {"db_backup", "processed_cleanup"}, NOW)] == ["db_backup"]
    assert [p["job"] for p in js.job_problems(status, {"db_backup", "library_mirror"}, NOW)] == \
        ["db_backup", "library_mirror"]


def test_health_summary_has_no_error_text():
    status = {"db_backup": _row(success=NOW - timedelta(hours=5)),
              "library_mirror": _row(error_at=NOW, error="/secret/path failed")}
    summary = js.health_summary(status, {"db_backup", "library_mirror"}, NOW)
    assert summary["ok"] is False and summary["backup_age_hours"] == 5.0
    assert summary["jobs"]["library_mirror"]["state"] == "failed"
    assert summary["jobs"]["thumbnails"]["state"] == "disabled"
    assert "/secret" not in str(summary)
    assert js.health_summary({}, {"db_backup"}, NOW) == {
        "ok": True, "backup_age_hours": None,
        "jobs": {name: {"state": "never_run" if name == "db_backup" else "disabled",
                        "last_success": None, "last_error_at": None} for name in js.JOBS}}


# ------------------------------------------------------------------ app side

@pytest.fixture
def cfg(tmp_path, monkeypatch):
    from cps.services import job_status as svc
    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path))
    svc.invalidate_cache()
    yield tmp_path
    svc.invalidate_cache()


def test_tasks_record_their_runs(cfg):
    from cps.services import job_status as svc
    from cps.services.worker import CalibreTask

    class Job(CalibreTask):
        job_name = "db_backup"
        fail = False

        def run(self, worker_thread):
            if self.fail:
                raise RuntimeError("disk full")
            self._handleSuccess()

        name = "Job"
        is_cancellable = False

    Job("ok").start(None)
    status, enabled = svc.job_snapshot()
    assert status["db_backup"]["last_success"] is not None and "db_backup" in enabled
    assert svc.job_problems() == []

    bad = Job("bad")
    bad.fail = True
    bad.start(None)  # the record is invalidated, so the failure shows at once
    problems = svc.job_problems()
    assert [(p["job"], p["state"], p["last_error"]) for p in problems] == [("db_backup", "failed", "disk full")]
    assert svc.last_success("db_backup") is not None


def test_unconfigured_mirror_run_is_not_recorded(cfg, monkeypatch):
    from cps.services import job_status as svc
    from cps.tasks import library_mirror as task_mod
    monkeypatch.setattr(task_mod, "get_mirror_dir", lambda: "")
    task_mod.TaskMirrorLibrary().start(None)
    status, enabled = svc.job_snapshot()
    assert status["library_mirror"]["last_success"] is None and "library_mirror" not in enabled


def test_snapshot_is_cached(cfg, monkeypatch):
    from cps.services import job_status as svc
    calls = []
    monkeypatch.setattr(svc, "_read", lambda: calls.append(1) or ({}, set()))
    svc.job_snapshot()
    svc.job_snapshot()
    assert calls == [1]
    monkeypatch.setattr(svc, "CACHE_SECONDS", 0)
    svc.job_snapshot()
    assert calls == [1, 1]


def test_snapshot_without_cwa_db(cfg):
    from cps.services import job_status as svc
    status, enabled = svc.job_snapshot()
    assert status == {} and enabled == {"db_backup", "processed_cleanup"}
    assert not (cfg / "cwa.db").exists()


def test_enabled_jobs(monkeypatch):
    from cps import config
    from cps.services.job_status import enabled_jobs
    monkeypatch.setattr(config, "schedule_generate_book_covers", True, raising=False)
    assert enabled_jobs("/mirror", 1) == {"db_backup", "processed_cleanup", "library_mirror",
                                          "thumbnails", "duplicate_scan"}
    monkeypatch.setattr(config, "schedule_generate_book_covers", False, raising=False)
    assert enabled_jobs(" ", "0") == {"db_backup", "processed_cleanup"}


def test_scheduled_mirror_is_visible_and_skipped_when_unset(monkeypatch):
    from cps import schedule
    from cps.services.worker import WorkerThread
    from cps.tasks import library_mirror as task_mod
    added = []
    monkeypatch.setattr(WorkerThread, "add", classmethod(lambda cls, user, task, hidden=False:
                                                         added.append((task, hidden))))
    monkeypatch.setattr(task_mod, "get_mirror_dir", lambda: "")
    assert schedule.queue_scheduled_library_mirror() is False and added == []
    monkeypatch.setattr(task_mod, "get_mirror_dir", lambda: "/mirror")
    assert schedule.queue_scheduled_library_mirror() is True
    task, hidden = added[0]
    assert isinstance(task, task_mod.TaskMirrorLibrary) and task.scheduled and hidden is False


def test_health_includes_checks_and_stays_200(cfg, tmp_path, monkeypatch):
    import cps
    from cps import web
    from cps.services import job_status as svc
    from cwa_db import CWA_DB
    lib = tmp_path / "lib"
    lib.mkdir()
    sqlite3.connect(lib / "metadata.db").execute("CREATE TABLE books (id INTEGER)").connection.close()
    with CWA_DB() as db:
        js.record_success(db.con, "db_backup", datetime.now(timezone.utc) - timedelta(days=3))
    svc.invalidate_cache()
    monkeypatch.setattr(web, "cwa_get_library_location", lambda: str(lib))
    with cps.app.test_request_context("/health"):
        response, status = web.health_check()
    body = response.get_json()
    assert status == 200 and body["status"] == "ok"
    assert body["checks"]["ok"] is False
    assert body["checks"]["jobs"]["db_backup"]["state"] == "stale"
    assert 71 < body["checks"]["backup_age_hours"] < 73


def test_admin_banner_lists_failing_jobs(tmp_path, monkeypatch):
    from .lily_env import lily_env, ADMIN_PASSWORD
    from .test_lily_reader_static import _register_remaining_blueprints
    from cps.services import job_status as svc
    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path / "cfg"))
    failing = [{"job": "db_backup", "label": "Database backup", "state": "failed",
                "last_success": None, "last_error_at": NOW, "last_error": "disk full"},
               {"job": "library_mirror", "label": "Library mirror", "state": "stale",
                "last_success": NOW - timedelta(days=2), "last_error_at": None, "last_error": ""}]
    monkeypatch.setattr(svc, "job_problems", lambda: failing)
    with lily_env(tmp_path) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        _register_remaining_blueprints(env.app)
        c = env.app.test_client()
        c.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
        html = c.get("/").get_data(as_text=True)
        assert 'id="job_status_warning"' in html
        assert "Database backup failed at" in html and "disk full" in html
        assert "Library mirror has not succeeded since" in html

        env.add_user("reader", password="pw")
        c2 = env.app.test_client()
        c2.post("/login", data={"username": "reader", "password": "pw"})
        page = c2.get("/")
        assert page.status_code == 200 and 'id="job_status_warning"' not in page.get_data(as_text=True)
