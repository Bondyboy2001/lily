# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Last-run record of Lily's recurring background jobs (cwa.db table job_status).

Each tracked job stores when it last started, last succeeded and last failed (with
the error). job_problems() turns that into what an admin needs to know: a job whose
last run failed, or a nightly job that hasn't succeeded for STALE_AFTER_HOURS.
Timestamps are stored as UTC ISO 8601 strings.

Kept free of Flask/cps imports: it works on a plain sqlite3 connection.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

STALE_AFTER_HOURS = 36
MAX_ERROR_LENGTH = 2000

_CREATE_TABLE = ("CREATE TABLE IF NOT EXISTS job_status (job TEXT PRIMARY KEY NOT NULL, last_started TEXT, "
                 "last_success TEXT, last_error_at TEXT, last_error TEXT DEFAULT '')")


@dataclass(frozen=True)
class Job:
    name: str
    label: str
    # Expected to succeed at least once every STALE_AFTER_HOURS while enabled
    nightly: bool


JOBS = {job.name: job for job in (
    Job("db_backup", "Database backup", True),
    Job("library_mirror", "Library mirror", True),
    Job("processed_cleanup", "Processed books cleanup", True),
    Job("thumbnails", "Cover thumbnails", False),
    Job("duplicate_scan", "Duplicate scan", False),
)}


def _iso(now: datetime | None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_time(value) -> datetime | None:
    """A stored timestamp as an aware UTC datetime, or None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _upsert(con: sqlite3.Connection, job: str, values: dict) -> None:
    con.execute(_CREATE_TABLE)
    columns = ", ".join(values)
    updates = ", ".join(f"{c}=excluded.{c}" for c in values)
    con.execute(f"INSERT INTO job_status (job, {columns}) VALUES (?{', ?' * len(values)}) "
                f"ON CONFLICT(job) DO UPDATE SET {updates}", (job, *values.values()))
    con.commit()


def record_start(con: sqlite3.Connection, job: str, now: datetime | None = None) -> None:
    _upsert(con, job, {"last_started": _iso(now)})


def record_success(con: sqlite3.Connection, job: str, now: datetime | None = None) -> None:
    _upsert(con, job, {"last_success": _iso(now)})


def record_error(con: sqlite3.Connection, job: str, error: str, now: datetime | None = None) -> None:
    _upsert(con, job, {"last_error_at": _iso(now), "last_error": str(error or "failed")[:MAX_ERROR_LENGTH]})


def read_status(con: sqlite3.Connection) -> dict[str, dict]:
    """{job: {last_started, last_success, last_error_at (datetimes or None), last_error}}.
    Empty if the table doesn't exist yet."""
    try:
        rows = con.execute("SELECT job, last_started, last_success, last_error_at, last_error "
                           "FROM job_status").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {job: {"last_started": parse_time(started), "last_success": parse_time(success),
                  "last_error_at": parse_time(error_at), "last_error": error or ""}
            for job, started, success, error_at, error in rows}


def job_state(row: dict | None, job: Job, now: datetime, stale_after_hours: float = STALE_AFTER_HOURS) -> str:
    """'ok', 'failed' (the last finished run failed), 'stale' (a nightly job without a
    success for too long) or 'never_run' (nothing recorded yet)."""
    if not row:
        return "never_run"
    success, error_at = row.get("last_success"), row.get("last_error_at")
    if error_at and (success is None or error_at >= success):
        return "failed"
    if job.nightly:
        # Never succeeded: measure from the first recorded start instead
        reference = success or row.get("last_started")
        if reference is None or now - reference > timedelta(hours=stale_after_hours):
            return "stale"
    return "ok" if success else "never_run"


def job_problems(status: dict[str, dict], enabled: Iterable[str], now: datetime | None = None,
                 stale_after_hours: float = STALE_AFTER_HOURS) -> list[dict]:
    """Enabled jobs needing attention, in JOBS order:
    [{job, label, state ('failed' or 'stale'), last_success, last_error_at, last_error}].
    Jobs with no record yet are not reported (a fresh install hasn't run anything)."""
    now = now or datetime.now(timezone.utc)
    enabled = set(enabled)
    problems = []
    for name, job in JOBS.items():
        row = status.get(name)
        if name not in enabled or not row:
            continue
        state = job_state(row, job, now, stale_after_hours)
        if state in ("failed", "stale"):
            problems.append({"job": name, "label": job.label, "state": state,
                             "last_success": row.get("last_success"), "last_error_at": row.get("last_error_at"),
                             "last_error": row.get("last_error") or ""})
    return problems


def health_summary(status: dict[str, dict], enabled: Iterable[str], now: datetime | None = None) -> dict:
    """The /health `checks` object: per-job state and timestamps (no error text, since
    /health is unauthenticated) plus the age of the last successful backup."""
    now = now or datetime.now(timezone.utc)
    enabled = set(enabled)
    jobs = {}
    for name, job in JOBS.items():
        row = status.get(name)
        state = job_state(row, job, now) if name in enabled else "disabled"
        jobs[name] = {
            "state": state,
            "last_success": row["last_success"].isoformat() if row and row.get("last_success") else None,
            "last_error_at": row["last_error_at"].isoformat() if row and row.get("last_error_at") else None,
        }
    backup = (status.get("db_backup") or {}).get("last_success")
    return {
        "ok": not job_problems(status, enabled, now),
        "backup_age_hours": round((now - backup).total_seconds() / 3600, 1) if backup else None,
        "jobs": jobs,
    }
