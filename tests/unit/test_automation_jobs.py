# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Durable cwa_operation_jobs helper, refresh job lifecycle and failure sidecars."""

import os
import threading

import pytest

@pytest.fixture
def jobs_db(temp_cwa_db):
    import automation_jobs
    yield automation_jobs, temp_cwa_db

@pytest.mark.unit
class TestOperationJobs:
    def test_schema_created_and_crud(self, jobs_db):
        aj, db = jobs_db
        rows = db.cur.execute(
            "SELECT name FROM sqlite_master WHERE name='cwa_operation_jobs'").fetchall()
        assert rows
        jid = aj.create_job("ingest", user_id=3, filename="a.epub", parent_id="p1")
        job = aj.get_job(jid)
        assert job["state"] == "running" and job["pid"] == os.getpid()
        assert job["parent_id"] == "p1" and job["filename"] == "a.epub"
        aj.finish_job(jid, "failed", error="boom")
        job = aj.get_job(jid)
        assert job["state"] == "failed" and job["error"] == "boom"
        assert job["finished_utc"]

    def test_finish_rejects_bad_state(self, jobs_db):
        aj, _ = jobs_db
        jid = aj.create_job("refresh")
        with pytest.raises(ValueError):
            aj.finish_job(jid, "running")

    def test_active_refresh_job_single_winner(self, jobs_db):
        aj, _ = jobs_db
        first = aj.active_job("refresh", create=True)
        second = aj.active_job("refresh", create=True)
        assert first == second
        got = []

        def worker():
            got.append(aj.active_job("refresh", create=True))
        aj.finish_job(first, "succeeded")
        third = aj.active_job("refresh", create=True)
        assert third != first
        threads = [threading.Thread(target=worker) for _ in range(4)]
        aj.finish_job(third, "failed")
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(set(got)) == 1
        assert len(aj.list_jobs(kind="refresh")) >= 2

    def test_stale_pid_marked_interrupted(self, jobs_db):
        aj, _ = jobs_db
        jid = aj.create_job("refresh")
        db = aj._connect()
        db.cur.execute("UPDATE cwa_operation_jobs SET pid=2147483000 WHERE id=?",
                       (jid,))
        db.con.commit()
        db.con.close()
        aj.mark_stale_interrupted()
        assert aj.get_job(jid)["state"] == "interrupted"

    def test_get_job_unknown(self, jobs_db):
        aj, _ = jobs_db
        assert aj.get_job("not-a-job") is None

@pytest.mark.unit
class TestFailureSidecars:
    def test_write_list_retry_delete(self, tmp_path):
        import ingest_failures
        failed = tmp_path / "failed"
        ingest = tmp_path / "ingest"
        failed.mkdir(); ingest.mkdir()
        f = failed / "bad.epub"
        f.write_bytes(b"x")
        ingest_failures.write_failure(str(f), "Not a known ebook format", job_id="j9")
        items = ingest_failures.list_failed(str(failed))
        assert items[0]["reason"] == "Not a known ebook format"
        assert items[0]["job_id"] == "j9"
        (failed / "old.epub").write_bytes(b"y")
        legacy = [i for i in ingest_failures.list_failed(str(failed))
                  if i["name"] == "old.epub"][0]
        assert legacy["reason"] == "No failure details recorded"
        (new_path,) = ingest_failures.retry_failed(str(failed), str(ingest), "bad.epub")
        assert os.path.exists(new_path)
        assert not os.path.exists(failed / ".bad.epub.failure.json")
        (failed / ".stray.failure.json").write_text("{}")
        (failed / "gone.epub").write_bytes(b"z")
        ingest_failures.delete_failed(str(failed), "gone.epub")
        assert not os.path.exists(failed / "gone.epub")
        assert os.path.exists(failed / ".stray.failure.json")

@pytest.mark.unit
class TestRefreshRoute:
    def test_refresh_durable_job_and_status(self, tmp_path, temp_cwa_db, monkeypatch):
        monkeypatch.setenv("BOOK_RECOVERY_DIR", str(tmp_path / "recovery"))
        from .lily_env import lily_env, ADMIN_PASSWORD
        with lily_env(tmp_path) as env:
            client = env.app.test_client()
            client.post("/login", data={"username": env.admin().name,
                                        "password": ADMIN_PASSWORD})
            monkeypatch.setenv("CWA_LIBRARY_REFRESH_TIMEOUT", "5")

            def fake_run(cmd, **kw):
                class R:
                    returncode = 0
                return R()
            from cps.cwa_functions import ingest
            monkeypatch.setattr(ingest.subprocess, "run", fake_run)
            monkeypatch.setattr(ingest, "get_ingest_dir", lambda: str(tmp_path))

            class InlineThread:
                def __init__(self, target, args):
                    self.target, self.args = target, args

                def start(self):
                    self.target(*self.args)

            monkeypatch.setattr(ingest, "Thread", InlineThread)
            resp = client.post("/cwa-library-refresh")
            assert resp.status_code == 200
            body = resp.get_json()
            assert body["job_id"] and body["status_url"]
            status_url = f"/cwa-library-refresh/jobs/{body['job_id']}"
            status = client.get(status_url).get_json()
            assert status["job_id"] == body["job_id"]
            assert status["state"] == "succeeded"
            legacy_msgs = env.app.config.get("library_refresh_messages", [])
            assert any("complete" in str(m) or "refreshed" in str(m) for m in legacy_msgs)
            again = client.get(status_url).get_json()
            assert again["job_id"] == body["job_id"] and again["state"] == status["state"]
            assert client.get("/cwa-library-refresh/jobs/nothex").status_code == 404
            assert client.get("/cwa-library-refresh/jobs/" + "0" * 32).status_code == 404

    def test_refresh_denied_to_reader(self, tmp_path, temp_cwa_db, monkeypatch):
        monkeypatch.setenv("BOOK_RECOVERY_DIR", str(tmp_path / "recovery"))
        from .lily_env import lily_env
        with lily_env(tmp_path) as env:
            env.add_user("plain", password="pw")
            client = env.app.test_client()
            client.post("/login", data={"username": "plain", "password": "pw"})
            assert client.post("/cwa-library-refresh").status_code in (302, 403)
            assert client.get("/cwa-library-refresh/jobs/abc").status_code in (302, 403)

@pytest.mark.unit
class TestOperationJobsExtended:
    def test_active_job_exact_lookup_survives_many_ingests(self, jobs_db):
        aj, db = jobs_db
        refresh = aj.create_job("refresh")
        for i in range(300):
            jid = aj.create_job("ingest", filename="f%d.epub" % i)
            aj.finish_job(jid, "succeeded")
        assert aj.active_job("refresh") == refresh

    def test_get_job_reconciles_dead_owner(self, jobs_db):
        aj, _ = jobs_db
        jid = aj.create_job("refresh")
        db = aj._connect()
        db.cur.execute("UPDATE cwa_operation_jobs SET pid=2147483000 WHERE id=?",
                       (jid,))
        db.con.commit()
        db.con.close()
        assert aj.get_job(jid)["state"] == "interrupted"

    def test_failed_children_count(self, jobs_db):
        aj, _ = jobs_db
        parent = aj.create_job("refresh")
        for _ in range(2):
            jid = aj.create_job("ingest", filename="x", parent_id=parent)
            aj.finish_job(jid, "failed", "bad file")
        ok = aj.create_job("ingest", filename="y", parent_id=parent)
        aj.finish_job(ok, "succeeded")
        assert aj.failed_children(parent) == 2
        assert aj.failed_children(ok) == 0

@pytest.mark.unit
class TestRefreshRouteFailures:
    def _env(self, tmp_path, temp_cwa_db, monkeypatch):
        monkeypatch.setenv("BOOK_RECOVERY_DIR", str(tmp_path / "recovery"))
        from .lily_env import lily_env, ADMIN_PASSWORD
        env = lily_env(tmp_path)
        ctx = env.__enter__()
        client = ctx.app.test_client()
        client.post("/login", data={"username": ctx.admin().name,
                                    "password": ADMIN_PASSWORD})
        return ctx, client, env

    def test_claim_failure_returns_503_no_thread(self, tmp_path, temp_cwa_db,
                                                 monkeypatch):
        ctx, client, env = self._env(tmp_path, temp_cwa_db, monkeypatch)
        try:
            from cps.cwa_functions import ingest
            import automation_jobs

            def broken(*a, **k):
                raise RuntimeError("db down")

            monkeypatch.setattr(automation_jobs, "claim_job", broken)
            try:
                import scripts.automation_jobs as saj
                monkeypatch.setattr(saj, "claim_job", broken)
            except ImportError:
                pass
            started = []
            monkeypatch.setattr(ingest, "Thread",
                                lambda *a, **k: started.append(1))
            resp = client.post("/cwa-library-refresh")
            assert resp.status_code == 503
            assert started == []
        finally:
            env.__exit__(None, None, None)

    def test_thread_start_failure_marks_job_failed(self, tmp_path, temp_cwa_db,
                                                   monkeypatch):
        ctx, client, env = self._env(tmp_path, temp_cwa_db, monkeypatch)
        try:
            from cps.cwa_functions import ingest
            import automation_jobs

            class BoomThread:
                def __init__(self, target, args):
                    self.target, self.args = target, args

                def start(self):
                    raise RuntimeError("no threads left")

            monkeypatch.setattr(ingest, "Thread", BoomThread)
            resp = client.post("/cwa-library-refresh")
            assert resp.status_code == 503
            job = automation_jobs.list_jobs(kind="refresh")[0]
            assert job["state"] == "failed"
            assert "could not start" in job["error"]
        finally:
            env.__exit__(None, None, None)

    def test_status_endpoint_rejects_non_refresh_jobs(self, tmp_path, temp_cwa_db,
                                                     monkeypatch):
        ctx, client, env = self._env(tmp_path, temp_cwa_db, monkeypatch)
        try:
            import automation_jobs
            jid = automation_jobs.create_job("ingest", filename="a.epub")
            resp = client.get("/cwa-library-refresh/jobs/%s" % jid)
            assert resp.status_code == 404
        finally:
            env.__exit__(None, None, None)

    def test_messages_requires_admin(self, tmp_path, temp_cwa_db, monkeypatch):
        ctx, client, env = self._env(tmp_path, temp_cwa_db, monkeypatch)
        ctx.add_user("plain", password="pw")
        reader = ctx.app.test_client()
        reader.post("/login", data={"username": "plain", "password": "pw"})
        assert reader.get("/cwa-library-refresh/messages").status_code in (302, 403)
        env.__exit__(None, None, None)

    def test_failed_children_fail_parent(self, tmp_path, temp_cwa_db, monkeypatch):
        ctx, client, env = self._env(tmp_path, temp_cwa_db, monkeypatch)
        try:
            from cps.cwa_functions import ingest
            import automation_jobs

            def fake_run(cmd, **kw):
                parent = kw["env"]["LILY_REFRESH_JOB_ID"]
                for i in range(2):
                    c = automation_jobs.create_job("ingest", filename="bad%d" % i,
                                                   parent_id=parent)
                    automation_jobs.finish_job(c, "failed", "nope")
                good = automation_jobs.create_job("ingest", filename="ok",
                                                  parent_id=parent)
                automation_jobs.finish_job(good, "succeeded")

                class R:
                    returncode = 0
                return R()

            class InlineThread:
                def __init__(self, target, args):
                    self.target, self.args = target, args

                def start(self):
                    self.target(*self.args)

            monkeypatch.setattr(ingest.subprocess, "run", fake_run)
            monkeypatch.setattr(ingest, "get_ingest_dir", lambda: str(tmp_path))
            monkeypatch.setattr(ingest, "Thread", InlineThread)
            resp = client.post("/cwa-library-refresh")
            assert resp.status_code == 200
            job = automation_jobs.get_job(resp.get_json()["job_id"])
            assert job["state"] == "failed"
            assert job["error"] == "2 import(s) failed; open Failed Imports"
        finally:
            env.__exit__(None, None, None)

    def test_duplicate_posts_launch_one_thread(self, tmp_path, temp_cwa_db,
                                               monkeypatch):
        ctx, client, env = self._env(tmp_path, temp_cwa_db, monkeypatch)
        try:
            from cps.cwa_functions import ingest

            launches = []

            class FakeThread:
                def __init__(self, target, args):
                    self.target, self.args = target, args

                def start(self):
                    launches.append(1)

            monkeypatch.setattr(ingest, "Thread", FakeThread)
            r1 = client.post("/cwa-library-refresh")
            r2 = client.post("/cwa-library-refresh")
            assert r1.status_code == 200 and r2.status_code == 200
            assert r1.get_json()["job_id"] == r2.get_json()["job_id"]
            assert len(launches) == 1
        finally:
            env.__exit__(None, None, None)

    def test_ingest_dir_failure_is_terminal(self, tmp_path, temp_cwa_db, monkeypatch):
        ctx, client, env = self._env(tmp_path, temp_cwa_db, monkeypatch)
        try:
            from cps.cwa_functions import ingest
            import automation_jobs

            class InlineThread:
                def __init__(self, target, args):
                    self.target, self.args = target, args

                def start(self):
                    self.target(*self.args)

            monkeypatch.setattr(ingest, "Thread", InlineThread)
            monkeypatch.setattr(ingest, "get_ingest_dir",
                                lambda: (_ for _ in ()).throw(RuntimeError("no dirs")))
            resp = client.post("/cwa-library-refresh")
            assert resp.status_code == 200
            job = automation_jobs.get_job(resp.get_json()["job_id"])
            assert job["state"] == "failed"
        finally:
            env.__exit__(None, None, None)
