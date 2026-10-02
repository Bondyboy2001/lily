# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Library refresh, ingest status helpers and the internal endpoints the ingest
process calls (auto-send scheduling, debounced duplicate scans, DB reconnect)."""

from flask import request, jsonify, current_app, url_for, abort
from flask_babel import gettext as _, lazy_gettext as _l

from .. import csrf
from ..usermanagement import login_required_if_no_ano
from ..admin import admin_required
from ..cw_login import current_user

import os
import re
import subprocess

import json
from threading import Thread, Lock, Timer

# common puts the scripts dir on sys.path, so it must be imported before cwa_db
from .common import library_refresh, cwa_internal, log, DIRS_JSON
from cwa_db import CWA_DB
from ..services.worker import WorkerThread
from ..tasks.database import TaskReconnectDatabase
from ..internal_api import internal_only

# Debounced duplicate scan timer (web process)
_duplicate_scan_timer = None
_duplicate_scan_lock = Lock()
_duplicate_scan_book_ids = set()


##————————————————————————————————————————————————————————————————————————————##
##                                                                            ##
##                             CWA LIBRARY REFRESH                            ##
##                                                                            ##
##————————————————————————————————————————————————————————————————————————————##

def get_ingest_dir():
    with open(DIRS_JSON, 'r') as f:
        dirs = json.load(f)
        return dirs['ingest_folder']

def _coerce_book_ids(raw_book_ids):
    if raw_book_ids is None:
        return []
    if isinstance(raw_book_ids, (str, int)):
        raw_book_ids = [raw_book_ids]
    book_ids = []
    for raw_book_id in raw_book_ids:
        try:
            book_id = int(raw_book_id)
        except (TypeError, ValueError):
            continue
        if book_id > 0:
            book_ids.append(book_id)
    return list(dict.fromkeys(book_ids))

def _library_refresh_timeout() -> int:
    """Seconds a manual library refresh may run (CWA_LIBRARY_REFRESH_TIMEOUT, default 2h).
    The processor enforces its own per-book timeout; this only stops a hung run."""
    try:
        value = int(os.environ.get("CWA_LIBRARY_REFRESH_TIMEOUT", "7200"))
    except ValueError:
        value = 7200
    return value if value > 0 else 7200


def _automation_jobs():
    try:
        from scripts.automation_jobs import (
            create_job, finish_job, get_job, list_jobs, active_job,
            claim_job, failed_children,
        )
    except ImportError:
        from automation_jobs import (
            create_job, finish_job, get_job, list_jobs, active_job,
            claim_job, failed_children,
        )
    return create_job, finish_job, get_job, list_jobs, active_job, claim_job, failed_children


def _finish_refresh_job(finish_job, job_id, return_code, app, failed_children=None):
    """Record the message and the durable job outcome for a refresh run."""
    job_failed_imports = 0
    if return_code == 0 and job_id and failed_children:
        try:
            job_failed_imports = failed_children(job_id)
        except Exception as e:
            log.warning("could not count failed ingest jobs for refresh %s: %s", job_id, e)
            job_failed_imports = -1
    if return_code is None:
        outcome_message = _l("Library Refresh 🔄 The ingest process took too long and was stopped, check the logs ⛔")
    elif return_code == 2:
        outcome_message = _l("Library Refresh 🔄 The book ingest service is already running ✋ Please wait until it has finished before trying again ⌛")
    elif return_code == 0 and job_failed_imports == 0:
        outcome_message = _l("Library Refresh 🔄 Library refreshed & ingest process complete! ✅")
    elif return_code == 0 and job_failed_imports > 0:
        outcome_message = _l("Library Refresh 🔄 %d import(s) failed, check the logs ⛔") % job_failed_imports
    else:
        outcome_message = _l("Library Refresh 🔄 An unexpected error occurred, check the logs ⛔")

    # Print result to docker log (force English by casting within temporary locale guard if desired)
    print(str(outcome_message).replace('Library Refresh 🔄', '[library-refresh]'), flush=True)

    if job_id and finish_job:
        try:
            if return_code == 0 and job_failed_imports == 0:
                finish_job(job_id, "succeeded")
            elif return_code == 0 and job_failed_imports > 0:
                finish_job(job_id, "failed",
                           "%d import(s) failed; check the logs" % job_failed_imports)
            elif return_code == 2:
                finish_job(job_id, "skipped",
                           "the ingest service is already running")
            elif return_code == 0:
                finish_job(job_id, "failed", "could not verify ingest results")
            elif return_code is None:
                finish_job(job_id, "failed", "ingest processor timed out")
            else:
                finish_job(job_id, "failed", "ingest processor exited with %s" % return_code)
        except Exception as e:
            log.warning("could not persist refresh job %s result: %s", job_id, e)


def refresh_library(app, job_id=None):
    _create, finish_job, _get, _list, _active, _claim, failed_children = \
        ([None] * 7) if not job_id else _automation_jobs()
    return_code = -1
    try:
        with app.app_context():  # Create app context for session
            ingest_dir = get_ingest_dir()
            timeout = _library_refresh_timeout()
            env = dict(os.environ)
            if job_id:
                env["LILY_REFRESH_JOB_ID"] = job_id
            try:
                result = subprocess.run(['python3', '/app/calibre-web-automated/scripts/ingest_processor.py', ingest_dir],
                                        timeout=timeout, env=env)
                return_code = result.returncode
            except subprocess.TimeoutExpired:
                # run() has already killed the processor; its flock is released with it
                log.error("Library refresh: ingest processor did not finish within %s seconds and was stopped", timeout)
                return_code = None
            except Exception as e:
                log.error("Library refresh: could not start the ingest processor: %s", e)
                return_code = -1
    except Exception as e:
        log.error("Library refresh failed before the ingest processor ran: %s", e)
        return_code = -1
    _finish_refresh_job(finish_job, job_id, return_code, app, failed_children)


@library_refresh.route("/cwa-library-refresh", methods=["POST"])
@login_required_if_no_ano
@admin_required
def cwa_library_refresh():
    print("[library-refresh] Library refresh manually triggered by user...", flush=True)
    app = current_app._get_current_object()  # Get actual app instance

    try:
        try:
            from scripts.automation_jobs import claim_job, finish_job
        except ImportError:
            from automation_jobs import claim_job, finish_job
        job_id, created = claim_job("refresh", user_id=int(current_user.id))
        if created:
            library_refresh_thread = Thread(target=refresh_library, args=(app, job_id))
            try:
                library_refresh_thread.start()
            except Exception as e:
                log.error("could not start the refresh worker: %s", e)
                try:
                    finish_job(job_id, "failed", "refresh worker could not start")
                except Exception:
                    pass
                return jsonify({"error": _("Could not start the library refresh")}), 503
    except Exception as e:
        log.error("could not start a durable refresh job: %s", e)
        return jsonify({"error": _("Refresh status storage is unavailable; refresh not started")}), 503

    status_url = url_for("library_refresh.library_refresh_job", job_id=job_id)
    return jsonify({"message": _("Library Refresh 🔄 Checking for any books that may have been missed, please wait..."),
                    "state": "running", "job_id": job_id, "status_url": status_url}), 200


@library_refresh.route("/cwa-library-refresh/jobs/<job_id>", methods=["GET"])
@login_required_if_no_ano
@admin_required
def library_refresh_job(job_id):
    if not re.fullmatch(r"[0-9a-fA-F]{32}", job_id or ""):
        abort(404)
    _create, _finish, get_job, _list, _active, _claim, _fc = _automation_jobs()
    job = get_job(job_id)
    if job is None or job["kind"] != "refresh":
        return jsonify({"error": "Unknown job"}), 404
    message = job["error"] or ""
    if job["state"] == "running":
        message = message or _("Checking the library for new books, please wait...")
    elif job["state"] == "succeeded":
        message = _("Library refreshed & ingest process complete!")
    elif job["state"] == "skipped":
        message = _("The ingest service is already running; nothing was queued twice.")
    elif job["state"] in ("failed", "interrupted"):
        message = message or _("Refresh did not finish; check the logs.")
    return jsonify({"job_id": job["id"], "state": job["state"], "message": message,
                    "kind": job["kind"], "filename": job["filename"] or "",
                    "started_utc": job["started_utc"],
                    "finished_utc": job["finished_utc"]})


##————————————————————————————————————————————————————————————————————————————##
##                                                                            ##
##                           CWA INTERNAL ENDPOINTS                           ##
##                                                                            ##
##————————————————————————————————————————————————————————————————————————————##

@csrf.exempt
@cwa_internal.route('/cwa-internal/queue-duplicate-scan', methods=["POST"])
@internal_only
def cwa_internal_queue_duplicate_scan():
    """Debounce and queue an incremental duplicate scan in the web process.

    Security: CWA's own processes only (@internal_only).
    Payload JSON: {delay_seconds:int, book_ids:[int]}
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        result = queue_debounced_duplicate_scan(
            delay_seconds=data.get('delay_seconds'),
            book_ids=data.get('book_ids'),
        )
        return jsonify(result), 200
    except Exception as e:
        log.error("[cwa-duplicates] Failed to schedule debounced duplicate scan: %s", str(e))
        return jsonify({"success": False, "error": 'Internal error; see server log for details'}), 500


def queue_debounced_duplicate_scan(delay_seconds=None, book_ids=None):
    """Debounce and queue an incremental duplicate scan in the web process."""
    db = CWA_DB()
    enabled = bool(db.cwa_settings.get('duplicate_scan_enabled', 0))
    frequency = db.cwa_settings.get('duplicate_scan_frequency', 'manual')
    default_delay = db.cwa_settings.get('duplicate_scan_debounce_seconds', 60)
    delay_seconds = int(delay_seconds if delay_seconds is not None else default_delay)
    delay_seconds = max(5, min(600, delay_seconds))
    book_ids = _coerce_book_ids(book_ids)

    if not enabled or frequency != 'after_import':
        return {"success": True, "skipped": True, "reason": "disabled_or_manual"}

    global _duplicate_scan_timer, _duplicate_scan_book_ids
    with _duplicate_scan_lock:
        if _duplicate_scan_timer is not None:
            try:
                _duplicate_scan_timer.cancel()
            except Exception:
                pass

        _duplicate_scan_book_ids.update(book_ids)

        def _enqueue_scan():
            try:
                log.debug("[cwa-duplicates] Timer fired, attempting to import TaskDuplicateScan...")
                from ..tasks.duplicate_scan import TaskDuplicateScan
                log.debug("[cwa-duplicates] TaskDuplicateScan imported successfully, creating task...")
                with _duplicate_scan_lock:
                    queued_book_ids = sorted(_duplicate_scan_book_ids) if _duplicate_scan_book_ids else None
                    _duplicate_scan_book_ids.clear()
                task = TaskDuplicateScan(
                    full_scan=False,
                    trigger_type='after_import',
                    book_ids=queued_book_ids,
                )
                log.debug("[cwa-duplicates] Task created, adding to WorkerThread...")
                WorkerThread.add('System', task, hidden=False)
                log.info("[cwa-duplicates] Debounced duplicate scan queued (after_import)")
                print("[cwa-duplicates] Debounced duplicate scan queued (after_import)", flush=True)
            except Exception as e:
                log.error("[cwa-duplicates] Failed to queue debounced duplicate scan: %s", str(e))
                print(f"[cwa-duplicates] ERROR: Failed to queue debounced duplicate scan: {e}", flush=True)
                import traceback
                traceback.print_exc()

        _duplicate_scan_timer = Timer(delay_seconds, _enqueue_scan)
        _duplicate_scan_timer.daemon = True
        _duplicate_scan_timer.start()
        log.info("[cwa-duplicates] Timer started with %d second delay", delay_seconds)
        print(f"[cwa-duplicates] Timer started with {delay_seconds} second delay", flush=True)

    return {"success": True, "queued": True, "delay_seconds": delay_seconds}


def duplicate_scan_debounce_pending():
    with _duplicate_scan_lock:
        return _duplicate_scan_timer is not None and _duplicate_scan_timer.is_alive()

@csrf.exempt
@cwa_internal.route('/cwa-internal/reconnect-db', methods=["POST"])
@internal_only
def cwa_internal_reconnect_db():
    """Enqueue a database reconnect task in the web process.

    Security: CWA's own processes only (@internal_only).
    """
    try:
        task = TaskReconnectDatabase()
        WorkerThread.add(None, task, hidden=True)
        return jsonify({"status": "enqueued"}), 200
    except Exception:
        log.exception("Internal reconnect-db failed")
        return jsonify({"error": 'Internal error; see server log for details'}), 500
