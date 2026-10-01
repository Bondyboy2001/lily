# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Records background job runs in cwa.db and reports unhealthy jobs.

CalibreTask.start() calls record_job_event() for tasks that set `job_name`. The admin
banner (render_template.py) and /health read the record through job_snapshot(), which
does one small read of cwa.db and caches it for CACHE_SECONDS.
"""

import os
import sqlite3
import sys
import threading
import time

from cps import config, logger

if '/app/calibre-web-automated/scripts/' not in sys.path:
    sys.path.insert(1, '/app/calibre-web-automated/scripts/')
import job_status  # noqa: E402

log = logger.create()

CACHE_SECONDS = 60
_cache_lock = threading.Lock()
_cache: dict = {"at": 0.0, "value": None}


def _cwa_db_file() -> str:
    # Same resolution as scripts/cwa_db.py (CWA_DB_PATH only differs in tests)
    return os.path.join(os.environ.get("CWA_DB_PATH", "/config"), "cwa.db")


def invalidate_cache() -> None:
    with _cache_lock:
        _cache["value"] = None


def record_job_event(job: str, event: str, error: str | None = None) -> None:
    """event: 'start', 'success' or 'error'. Never raises: a broken record must not fail a task."""
    try:
        from cwa_db import CWA_DB
        with CWA_DB() as cwa_db:
            if event == "start":
                job_status.record_start(cwa_db.con, job)
            elif event == "success":
                job_status.record_success(cwa_db.con, job)
            else:
                job_status.record_error(cwa_db.con, job, error or "failed")
    except Exception as e:
        log.warning("Could not record %s of job %s: %s", event, job, e)
    invalidate_cache()


def _setting_on(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "off")
    return bool(value)


def enabled_jobs(mirror_dir, duplicate_scan_enabled) -> set:
    """Which tracked jobs are expected to run with the current settings."""
    enabled = {"db_backup", "processed_cleanup"}
    if str(mirror_dir or "").strip():
        enabled.add("library_mirror")
    if getattr(config, "schedule_generate_book_covers", False):
        enabled.add("thumbnails")
    if _setting_on(duplicate_scan_enabled):
        enabled.add("duplicate_scan")
    return enabled


def _read() -> tuple[dict, set]:
    path = _cwa_db_file()
    if not os.path.isfile(path):  # sqlite3.connect would create it
        return {}, enabled_jobs("", False)
    con = sqlite3.connect(path, timeout=2)
    try:
        status = job_status.read_status(con)
        try:
            mirror_dir, dup_scan = con.execute(
                "SELECT library_mirror_dir, duplicate_scan_enabled FROM cwa_settings LIMIT 1").fetchone() or ("", 0)
        except sqlite3.Error:
            mirror_dir, dup_scan = "", 0
    finally:
        con.close()
    return status, enabled_jobs(mirror_dir, dup_scan)


def job_snapshot() -> tuple[dict, set]:
    """(status by job, enabled job names), cached for CACHE_SECONDS. ({}, defaults) on errors."""
    with _cache_lock:
        cached = _cache["value"]
        if cached is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return cached
    try:
        value = _read()
    except Exception as e:
        log.debug("Could not read job status: %s", e)
        value = ({}, enabled_jobs("", False))
    with _cache_lock:
        _cache["value"], _cache["at"] = value, time.monotonic()
    return value


def job_problems() -> list[dict]:
    """Enabled jobs whose last run failed or whose last success is too old (for the admin banner)."""
    status, enabled = job_snapshot()
    return job_status.job_problems(status, enabled)


def health_checks() -> dict:
    status, enabled = job_snapshot()
    return job_status.health_summary(status, enabled)


def last_success(job: str):
    """Aware UTC datetime of the job's last success, or None."""
    status, _ = job_snapshot()
    return (status.get(job) or {}).get("last_success")
