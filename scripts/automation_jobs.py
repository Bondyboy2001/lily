# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Persistent records for long-running automation (library refresh, ingest).

Each call opens its own CWA_DB context so it works from the web process and
from the ingest subprocess alike. Kept free of Flask/cps imports.
"""

import os
import sqlite3
import uuid
from datetime import datetime, timezone

TERMINAL_STATES = ("succeeded", "failed", "interrupted", "skipped")


def _connect():
    from cwa_db import CWA_DB
    return CWA_DB()


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_job(kind, user_id=None, filename="", parent_id=None, db=None):
    """Insert a running job row and return its id. `db` may be an open CWA_DB."""
    job_id = uuid.uuid4().hex
    if db is not None:
        _insert(db, job_id, kind, user_id, filename, parent_id)
        return job_id
    with _connect() as c:
        _insert(c, job_id, kind, user_id, filename, parent_id)
    return job_id


def _insert(c, job_id, kind, user_id, filename, parent_id):
    c.cur.execute(
        "INSERT INTO cwa_operation_jobs "
        "(id, kind, user_id, filename, parent_id, state, started_utc, error, pid) "
        "VALUES (?, ?, ?, ?, ?, 'running', ?, '', ?)",
        (job_id, kind, user_id, filename or "", parent_id, _now(), os.getpid()))
    c.con.commit()


def finish_job(job_id, state, error="", db=None):
    """Mark a job finished; state must be a terminal one."""
    if state not in TERMINAL_STATES:
        raise ValueError("not a terminal job state: %r" % state)
    with _connect() as c:
        c.cur.execute(
            "UPDATE cwa_operation_jobs SET state=?, finished_utc=?, error=? WHERE id=?",
            (state, _now(), str(error or "")[:2000], job_id))
        c.con.commit()


def get_job(job_id):
    with _connect() as c:
        mark_stale_interrupted(db=c)
        c.cur.execute(
            "SELECT id, kind, user_id, filename, parent_id, state, started_utc, "
            "finished_utc, error, pid FROM cwa_operation_jobs WHERE id=?",
            (job_id,))
        row = c.cur.fetchone()
    if not row:
        return None
    return _job_dict(row)


def latest_job_for_file(kind, filename):
    """The newest job of this kind for one ingest file name, or None before it starts."""
    with _connect() as c:
        mark_stale_interrupted(db=c)
        c.cur.execute(
            "SELECT id, kind, user_id, filename, parent_id, state, started_utc, "
            "finished_utc, error, pid FROM cwa_operation_jobs WHERE kind=? AND filename=? "
            "ORDER BY started_utc DESC LIMIT 1",
            (kind, filename))
        row = c.cur.fetchone()
    return _job_dict(row) if row else None


def _job_dict(row):
    return {"id": row[0], "kind": row[1], "user_id": row[2], "filename": row[3],
            "parent_id": row[4], "state": row[5], "started_utc": row[6],
            "finished_utc": row[7], "error": row[8], "pid": row[9]}


def _pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def mark_stale_interrupted(db=None):
    """Running rows whose owner PID is verifiably dead become interrupted."""
    own = db is None
    c = db or _connect()
    try:
        c.cur.execute(
            "SELECT id, pid FROM cwa_operation_jobs WHERE state='running'")
        for job_id, pid in c.cur.fetchall():
            if not _pid_alive(pid):
                c.cur.execute(
                    "UPDATE cwa_operation_jobs SET state='interrupted', "
                    "finished_utc=?, error=? WHERE id=? AND state='running'",
                    (_now(), "owner process is gone", job_id))
        c.con.commit()
    finally:
        if own:
            c.con.close()


def list_jobs(limit=25, kind=None):
    """Newest jobs first; running rows whose owner PID is verifiably dead are
    marked interrupted."""
    with _connect() as c:
        mark_stale_interrupted(db=c)
        if kind:
            c.cur.execute(
                "SELECT id, kind, user_id, filename, parent_id, state, started_utc, "
                "finished_utc, error, pid FROM cwa_operation_jobs WHERE kind=? "
                "ORDER BY started_utc DESC LIMIT ?", (kind, int(limit)))
        else:
            c.cur.execute(
                "SELECT id, kind, user_id, filename, parent_id, state, started_utc, "
                "finished_utc, error, pid FROM cwa_operation_jobs "
                "ORDER BY started_utc DESC LIMIT ?", (int(limit),))
        return [_job_dict(r) for r in c.cur.fetchall()]


def active_job(kind, create=False, user_id=None, filename=""):
    """The id of the running job of this kind, or None. With create=True,
    returns claim_job(kind, ...)[0] instead of None."""
    with _connect() as c:
        mark_stale_interrupted(db=c)
        c.cur.execute(
            "SELECT id FROM cwa_operation_jobs WHERE kind=? AND state='running' "
            "ORDER BY started_utc DESC LIMIT 1", (kind,))
        row = c.cur.fetchone()
    if row:
        return row[0]
    if not create:
        return None
    return claim_job(kind, user_id=user_id, filename=filename)[0]


def failed_children(job_id):
    """How many child jobs of `job_id` ended failed."""
    with _connect() as c:
        c.cur.execute(
            "SELECT COUNT(*) FROM cwa_operation_jobs WHERE parent_id=? AND state='failed'",
            (job_id,))
        return c.cur.fetchone()[0]


def claim_job(kind, user_id=None, filename=""):
    """Returns (job_id, created): the running job of this kind, inserting one
    when absent. The unique partial index on running rows makes concurrent
    starters converge on a single job; created tells the caller whether it
    must launch the worker."""
    existing = active_job(kind)
    if existing:
        return existing, False
    job_id = uuid.uuid4().hex
    try:
        with _connect() as c:
            _insert(c, job_id, kind, user_id, filename, None)
        return job_id, True
    except sqlite3.IntegrityError:
        existing = active_job(kind)
        if existing:
            return existing, False
        raise
