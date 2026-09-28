# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Library refresh, ingest status helpers and the internal endpoints the ingest
process calls (auto-send scheduling, debounced duplicate scans, DB reconnect)."""

from flask import request, jsonify, current_app
from flask_babel import gettext as _, lazy_gettext as _l

from .. import csrf
from ..usermanagement import login_required_if_no_ano
from ..admin import admin_required

import os
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


def _duplicate_full_scan_running():
    try:
        return WorkerThread.get_instance().has_active_task_of_type(
            "TaskDuplicateScan",
            extra_check=lambda task: getattr(task, "full_scan", False),
        )
    except Exception as ex:
        log.debug("[cwa-duplicates] Could not check duplicate full-scan worker state: %s", str(ex))
    return False

##————————————————————————————————————————————————————————————————————————————##
##                                                                            ##
##                             CWA LIBRARY REFRESH                            ##
##                                                                            ##
##————————————————————————————————————————————————————————————————————————————##

def get_ingest_dir():
    with open(DIRS_JSON, 'r') as f:
        dirs = json.load(f)
        return dirs['ingest_folder']

def get_ingest_status():
    """Read the current ingest service status"""
    try:
        with open('/config/cwa_ingest_status', 'r') as f:
            status_line = f.read().strip()
            if ':' in status_line:
                parts = status_line.split(':')
                return {
                    'state': parts[0],
                    'filename': parts[1] if len(parts) > 1 else '',
                    'timestamp': parts[2] if len(parts) > 2 else '',
                    'detail': parts[3] if len(parts) > 3 else ''
                }
            else:
                return {'state': status_line, 'filename': '', 'timestamp': '', 'detail': ''}
    except (FileNotFoundError, IOError):
        return {'state': 'unknown', 'filename': '', 'timestamp': '', 'detail': ''}


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

def get_ingest_queue_size():
    """Get the number of files in the retry queue"""
    try:
        with open('/config/cwa_ingest_retry_queue', 'r') as f:
            return len([line for line in f if line.strip()])
    except (FileNotFoundError, IOError):
        return 0

def _library_refresh_timeout() -> int:
    """Seconds a manual library refresh may run (CWA_LIBRARY_REFRESH_TIMEOUT, default 2h).
    The processor enforces its own per-book timeout; this only stops a hung run."""
    try:
        value = int(os.environ.get("CWA_LIBRARY_REFRESH_TIMEOUT", "7200"))
    except ValueError:
        value = 7200
    return value if value > 0 else 7200


def refresh_library(app):
    with app.app_context():  # Create app context for session
        ingest_dir = get_ingest_dir()
        timeout = _library_refresh_timeout()
        try:
            result = subprocess.run(['python3', '/app/calibre-web-automated/scripts/ingest_processor.py', ingest_dir],
                                    timeout=timeout)
            return_code = result.returncode
        except subprocess.TimeoutExpired:
            # run() has already killed the processor; its flock is released with it
            log.error("Library refresh: ingest processor did not finish within %s seconds and was stopped", timeout)
            return_code = None

        # Add empty list for messages in app context if a list doesn't already exist
        if "library_refresh_messages" not in current_app.config:
            current_app.config["library_refresh_messages"] = []

        if return_code is None:
            message = _l("Library Refresh 🔄 The ingest process took too long and was stopped, check the logs ⛔")
        elif return_code == 2:
            message = _l("Library Refresh 🔄 The book ingest service is already running ✋ Please wait until it has finished before trying again ⌛")
        elif return_code == 0:
            message = _l("Library Refresh 🔄 Library refreshed & ingest process complete! ✅")
        else:
            message = _l("Library Refresh 🔄 An unexpected error occurred, check the logs ⛔")

        # Store lazy message objects (will be translated when converted to string)
        current_app.config["library_refresh_messages"].append(message)
        # Print result to docker log (force English by casting within temporary locale guard if desired)
        print(str(message).replace('Library Refresh 🔄', '[library-refresh]'), flush=True)

@library_refresh.route("/cwa-library-refresh", methods=["POST"])
@login_required_if_no_ano
@admin_required
def cwa_library_refresh():
    print("[library-refresh] Library refresh manually triggered by user...", flush=True)
    app = current_app._get_current_object()  # Get actual app instance

    current_app.config["library_refresh_messages"] = []

    # Run refresh_library() in a background thread
    library_refresh_thread = Thread(target=refresh_library, args=(app,))
    library_refresh_thread.start()

    return jsonify({"message": _("Library Refresh 🔄 Checking for any books that may have been missed, please wait...")}), 200

@library_refresh.route("/cwa-library-refresh/messages", methods=["GET"])
@login_required_if_no_ano
def get_library_refresh_messages():
    messages = current_app.config.get("library_refresh_messages", [])

    # Convert lazy messages to strings (translation occurs here)
    rendered = [str(m) for m in messages]

    # Clear messages after they have been retrieved
    current_app.config["library_refresh_messages"] = []

    return jsonify({"messages": rendered})

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
        return jsonify({"success": False, "error": str(e)}), 500


@csrf.exempt
@cwa_internal.route('/cwa-internal/run-duplicate-scan', methods=["POST"])
@internal_only
def cwa_internal_run_duplicate_scan():
    """Run a bounded incremental duplicate scan synchronously in the web process.

    Security: CWA's own processes only (@internal_only).
    Payload JSON: {book_ids:[int]}
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        book_ids = _coerce_book_ids(data.get('book_ids'))
        if not book_ids:
            return jsonify({"success": True, "skipped": True, "reason": "no_book_ids"}), 200

        db = CWA_DB()
        enabled = bool(db.cwa_settings.get('duplicate_scan_enabled', 0))
        frequency = db.cwa_settings.get('duplicate_scan_frequency', 'manual')
        if not enabled or frequency != 'after_import':
            return jsonify({"success": True, "skipped": True, "reason": "disabled_or_manual"}), 200

        from ..tasks.duplicate_scan import TaskDuplicateScan
        task = TaskDuplicateScan(
            full_scan=False,
            trigger_type='after_import',
            book_ids=book_ids,
        )
        task.run(None)
        return jsonify({
            "success": True,
            "result_count": task.result_count,
            "message": str(getattr(task, "message", "")),
        }), 200
    except Exception as e:
        log.error("[cwa-duplicates] Failed to run synchronous duplicate scan: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500


@csrf.exempt
@cwa_internal.route('/cwa-internal/duplicate-scan-status', methods=["GET", "POST"])
@internal_only
def cwa_internal_duplicate_scan_status():
    """Expose duplicate scan worker state to localhost-only ingest helpers."""
    try:
        return jsonify({
            "success": True,
            "full_scan_running": _duplicate_full_scan_running(),
        }), 200
    except Exception as e:
        log.error("[cwa-duplicates] Failed to read duplicate scan status: %s", str(e))
        return jsonify({"success": False, "error": str(e)}), 500


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
    except Exception as e:
        log.exception("Internal reconnect-db failed")
        return jsonify({"error": str(e)}), 500
