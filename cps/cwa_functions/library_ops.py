# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Convert Library and EPUB Fixer services: run/cancel/status routes and scheduling helpers."""

from flask import redirect, flash, url_for, request, send_from_directory, abort, jsonify
from flask_babel import gettext as _

from .. import config, csrf, calibre_db
from ..usermanagement import login_required_if_no_ano
from ..admin import admin_required
from ..render_template import render_title_template
from ..cw_login import current_user

import subprocess
from pathlib import Path
from time import sleep

import json
from threading import Thread
import queue
import os
import tempfile
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename

# common puts the scripts dir on sys.path, so it must be imported before cwa_db
from .common import convert_library, epub_fixer, cwa_internal, log, DIRS_JSON
from cwa_db import CWA_DB
from ..services.background_scheduler import BackgroundScheduler, DateTrigger
from ..services.worker import WorkerThread
from ..tasks.ops import TaskConvertLibraryRun, TaskEpubFixerRun
from ..internal_api import internal_only
from .logs import (extract_progress, extract_progress_from_file, read_log_tail, archive_run_log,
                   get_logs_from_archive, get_log_dates)

# Status endpoints return at most this many trailing log lines per UI poll
STATUS_MAX_LINES = 2000
# How often the run watcher checks for a cancel request / process exit
WATCH_INTERVAL = 0.25
# Give up waiting for the start thread to hand over the Popen handle after this long
PROCESS_HANDLE_TIMEOUT = 60


def _log_status_response(log_path):
    """JSON for the status endpoints: {'status': last STATUS_MAX_LINES log lines, 'progress': {...}}.

    The end-of-run / cancelled markers the UI looks for are the log's last lines, so the tail
    always contains them. Progress is the last "n/n" in the whole log (as before).
    """
    try:
        status, truncated = read_log_tail(log_path, STATUS_MAX_LINES)
        progress = extract_progress_from_file(log_path, status, truncated)
        if truncated:
            status = (f"[... earlier lines omitted - showing the last {STATUS_MAX_LINES} lines. "
                      f"Download the log for the full run ...]\n") + status
    except FileNotFoundError:  # no run yet on this install
        status = ""
        progress = extract_progress(status)
    return json.dumps({'status': status, 'progress': progress})


def _watch_run(proc_queue, trigger_file: Path, log_path: str, terminated_line: str, on_cancel=None):
    """Wait for a Convert Library / EPUB Fixer subprocess to finish or be cancelled, then archive its log.

    The start thread puts the Popen handle on proc_queue; it is fetched once up front. Completion is
    detected with process.poll() (cheap), not by re-reading the log: the script writes its
    "Run Ended" line before exiting, so archiving after exit never misses it.
    """
    process = None
    waited = 0.0
    while process is None:
        try:
            process = proc_queue.get(timeout=WATCH_INTERVAL)
        except queue.Empty:
            waited += WATCH_INTERVAL
            if waited >= PROCESS_HANDLE_TIMEOUT:
                log.error(f"Run watcher for {log_path} never received the process handle; giving up")
                return

    while True:
        cancelled = trigger_file.exists()
        if not cancelled and process.poll() is not None:
            # Re-check: a cancel may have killed the process between the two checks above
            cancelled = trigger_file.exists()
            if not cancelled:
                archive_run_log(log_path)
                return
        if cancelled:
            process.terminate()
            # Wait for it to exit: the kernel then drops its flock on the script's lock file.
            # The lock file itself is never removed (see the lock comment in the script).
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            if on_cancel is not None:
                on_cancel()
            # Remove the trigger file that triggered this block
            try:
                os.remove(trigger_file)
            except FileNotFoundError:
                ...
            # Add string to log to notify user of successful cancellation and to stop the JS update script
            with open(log_path, 'a') as f:
                f.write(f"\n{terminated_line} {datetime.now()}")
            # Add run log to log_archive
            archive_run_log(log_path)
            return
        sleep(WATCH_INTERVAL)


def _schedule_library_op(job_type, title, task_cls, delay_minutes, username):
    """Schedule a Convert Library / EPUB Fixer run in the web process scheduler.

    Returns (run_at_local, schedule_row_id). Raises RuntimeError if the scheduler is unavailable.
    """
    delay_minutes = max(0, min(60, int(delay_minutes)))
    username = username or 'System'

    scheduler = BackgroundScheduler()
    if not scheduler:
        raise RuntimeError("Scheduler unavailable")

    run_at_local = datetime.now() + timedelta(minutes=delay_minutes)
    try:
        from datetime import timezone
        run_at_utc_iso = run_at_local.astimezone(timezone.utc).replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')
    except Exception:
        run_at_utc_iso = run_at_local.isoformat()

    # Persist scheduled intent
    row_id = None
    try:
        row_id = CWA_DB().scheduled_add_job(job_type, run_at_utc_iso, username=username, title=title)
    except Exception as e:
        log.error(f"Failed to record scheduled {job_type} in cwa.db: {e}")

    def _trigger(sid=row_id, u=username):
        should_run = True
        try:
            if sid is not None:
                should_run = bool(CWA_DB().scheduled_mark_dispatched(int(sid)))
        except Exception:
            pass
        if not should_run:
            return
        # Enqueue wrapper task so it shows in Tasks UI and triggers run internally
        WorkerThread.add(u, task_cls(), hidden=False)

    job = scheduler.schedule(func=_trigger, trigger=DateTrigger(run_date=run_at_local), name=f"{title} (scheduled)")
    try:
        if row_id is not None and job is not None:
            CWA_DB().scheduled_update_job_id(int(row_id), str(job.id))
    except Exception:
        pass
    return run_at_local, row_id


def _schedule_library_op_response(job_type, title, task_cls):
    try:
        data = request.get_json(force=True, silent=True) or {}
        run_at_local, row_id = _schedule_library_op(job_type, title, task_cls,
                                                    data.get('delay_minutes', 5), data.get('username'))
        return jsonify({"status": "scheduled", "run_at": run_at_local.isoformat(), "schedule_id": row_id}), 200
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        log.error(f"Internal schedule-{job_type} failed: {e}")
        return jsonify({"error": str(e)}), 400


@csrf.exempt
@cwa_internal.route('/cwa-internal/schedule-convert-library', methods=["POST"])
@internal_only
def cwa_internal_schedule_convert_library():
    """Schedule a Convert Library run in the web process scheduler.

    Security: CWA's own processes only (@internal_only).
    Payload JSON: {delay_minutes:int, username:str}
    """
    return _schedule_library_op_response('convert_library', 'Convert Library', TaskConvertLibraryRun)

@csrf.exempt
@cwa_internal.route('/cwa-internal/schedule-epub-fixer', methods=["POST"])
@internal_only
def cwa_internal_schedule_epub_fixer():
    """Schedule an EPUB Fixer run in the web process scheduler.

    Security: CWA's own processes only (@internal_only).
    Payload JSON: {delay_minutes:int, username:str}
    """
    return _schedule_library_op_response('epub_fixer', 'EPUB Fixer', TaskEpubFixerRun)

##————————————————————————————————————————————————————————————————————————————##
##                                                                            ##
##                        CWA LIBRARY CONVERSION SERVICE                      ##
##                                                                            ##
##————————————————————————————————————————————————————————————————————————————##

def convert_library_start(queue):
    cl_process = subprocess.Popen(['python3', '/app/calibre-web-automated/scripts/convert_library.py'])
    queue.put(cl_process)

def get_tmp_conversion_dir() -> str:
    dirs = {}
    with open(DIRS_JSON, 'r') as f:
        dirs: dict[str, str] = json.load(f)
    tmp_conversion_dir = f"{dirs['tmp_conversion_dir']}/"

    return tmp_conversion_dir

def empty_tmp_con_dir(tmp_conversion_dir) -> None:
    try:
        files = os.listdir(tmp_conversion_dir)
        for file in files:
            file_path = os.path.join(tmp_conversion_dir, file)
            if os.path.isfile(file_path):
                os.remove(file_path)
    except Exception as e:
        print(f"[cwa-functions]: An error occurred while emptying {tmp_conversion_dir}. See the following error: {e}")

def is_convert_library_finished() -> bool:
    log_path = "/config/convert-library.log"
    with open(log_path, 'r') as log:
        if "CWA Convert Library Service - Run Ended: " in log.read():
            return True
        else:
            return False

def kill_convert_library(queue):
    trigger_file = Path(tempfile.gettempdir() + "/.kill_convert_library_trigger")
    log_path = "/config/convert-library.log"
    # On cancel, also empty the tmp conversion dir of half finished files
    _watch_run(queue, trigger_file, log_path, "CONVERT LIBRARY PROCESS TERMINATED BY USER AT",
               on_cancel=lambda: empty_tmp_con_dir(get_tmp_conversion_dir()))

@convert_library.route('/cwa-convert-library-overview', methods=["GET"])
@login_required_if_no_ano
@admin_required
def show_convert_library_page():
    return render_title_template('cwa_convert_library.html', title=_("Lily - Convert Library"), page="cwa-library-convert",
                                target_format=CWA_DB().cwa_settings['auto_convert_target_format'].upper())

@convert_library.route('/cwa-convert-library/schedule/<int:delay>', methods=["POST"])
@login_required_if_no_ano
@admin_required
def schedule_convert_library(delay: int):
    # Clamp delay to sane range
    delay = max(0, min(60, int(delay)))
    try:
        # Called directly: an HTTP request from the web process to itself blocks the gevent server.
        username = getattr(current_user, 'name', 'System') or 'System'
        _schedule_library_op('convert_library', 'Convert Library', TaskConvertLibraryRun, delay, username)
        flash(_(f"Convert Library scheduled in {delay} minute(s)."), category="success")
    except Exception as e:
        flash(_(f"Failed to schedule Convert Library: {e}"), category="error")
    return redirect(url_for('convert_library.show_convert_library_page'))

@convert_library.route('/cwa-convert-library/log-archive', methods=["GET"])
@login_required_if_no_ano
@admin_required
def show_convert_library_logs():
    logs=get_logs_from_archive("convert-library")
    log_dates = get_log_dates(logs)
    return render_title_template('cwa_list_logs.html', title=_("Lily - Convert Library"), page="cwa-library-convert-logs",
                                logs=logs, log_dates=log_dates)

@convert_library.route('/cwa-convert-library/download-current-log/<log_filename>')
@login_required_if_no_ano
@admin_required
def download_current_log(log_filename):
    log_filename = "convert-library.log"
    LOG_DIR = "/config"
    try:
        # Secure the filename to prevent directory traversal (e.g., '..')
        safe_filename = secure_filename(log_filename)
        
        # Join the logs directory with the filename and get the absolute path
        file_path = os.path.abspath(os.path.join(LOG_DIR, safe_filename))
        
        # Check if the file path is within the allowed directory
        if not file_path.startswith(os.path.abspath(LOG_DIR)):
            abort(403)  # Forbidden if it's not within the logs directory

        # Check if the file exists
        if not os.path.exists(file_path):
            abort(404)  # Return a 404 if the file does not exist

        # Send the file as an attachment (to trigger a download)
        return send_from_directory(LOG_DIR, safe_filename, as_attachment=True)
    
    except Exception as e:
        # Handle any other errors
        abort(400)  # Bad request for malformed or unsafe file paths

def start_convert_library_run():
    """Start a full Convert Library run in background threads.

    Called by the admin route and directly by TaskConvertLibraryRun (scheduled runs).
    """
    # Wipe conversion log from previous runs
    open('/config/convert-library.log', 'w').close()
    # Remove any left over kill file
    try:
        os.remove(tempfile.gettempdir() + "/.kill_convert_library_trigger")
    except FileNotFoundError:
        ...
    # Queue to share the subprocess reference
    process_queue = queue.Queue()
    # Create and start the subprocess thread
    cl_thread = Thread(target=convert_library_start, args=(process_queue,))
    cl_thread.start()
    # Create and start the kill thread
    cl_kill_thread = Thread(target=kill_convert_library, args=(process_queue,))
    cl_kill_thread.start()

def request_convert_library_cancel():
    # Create kill trigger file
    open(tempfile.gettempdir() + "/.kill_convert_library_trigger", 'w').close()

@convert_library.route('/cwa-convert-library-start', methods=["POST"])
@login_required_if_no_ano
@admin_required
def start_conversion():
    start_convert_library_run()
    return redirect(url_for('convert_library.show_convert_library_page'))

@convert_library.route('/convert-library-cancel', methods=["POST"])
@login_required_if_no_ano
@admin_required
def cancel_convert_library():
    request_convert_library_cancel()
    return redirect(url_for('convert_library.show_convert_library_page'))

@convert_library.route('/convert-library-status', methods=["GET"])
@login_required_if_no_ano
@admin_required
def get_status():
    return _log_status_response("/config/convert-library.log")

##————————————————————————————————————————————————————————————————————————————##
##                                                                            ##
##                            CWA EPUB FIXER SERVICE                          ##
##                                                                            ##
##————————————————————————————————————————————————————————————————————————————##

def epub_fixer_start(queue, input_file: str | None = None):
    if input_file:
        ef_process = subprocess.Popen(['python3', '/app/calibre-web-automated/scripts/kindle_epub_fixer.py', '--input_file', input_file])
    else:
        ef_process = subprocess.Popen(['python3', '/app/calibre-web-automated/scripts/kindle_epub_fixer.py', '--all'])
    queue.put(ef_process)

def is_epub_fixer_finished() -> bool:
    log_path = "/config/epub-fixer.log"
    with open(log_path, 'r') as log:
        if "CWA Kindle EPUB Fixer Service - Run Ended: " in log.read():
            return True
        else:
            return False

def kill_epub_fixer(queue):
    trigger_file = Path(tempfile.gettempdir() + "/.kill_epub_fixer_trigger")
    log_path = "/config/epub-fixer.log"
    _watch_run(queue, trigger_file, log_path, "CWA EPUB FIXER PROCESS TERMINATED BY USER AT")

@epub_fixer.route('/cwa-epub-fixer-overview', methods=["GET"])
@login_required_if_no_ano
@admin_required
def show_epub_fixer_page():
    return render_title_template('cwa_epub_fixer.html', title=_("Lily - EPUB Fixer Service"), page="cwa-epub-fixer")

@epub_fixer.route('/cwa-epub-fixer/schedule/<int:delay>', methods=["POST"])
@login_required_if_no_ano
@admin_required
def schedule_epub_fixer(delay: int):
    delay = max(0, min(60, int(delay)))
    try:
        # Called directly: an HTTP request from the web process to itself blocks the gevent server.
        username = getattr(current_user, 'name', 'System') or 'System'
        _schedule_library_op('epub_fixer', 'EPUB Fixer', TaskEpubFixerRun, delay, username)
        flash(_(f"EPUB Fixer scheduled in {delay} minute(s)."), category="success")
    except Exception as e:
        flash(_(f"Failed to schedule EPUB Fixer: {e}"), category="error")
    return redirect(url_for('epub_fixer.show_epub_fixer_page'))

@epub_fixer.route('/cwa-epub-fixer/log-archive', methods=["GET"])
@login_required_if_no_ano
@admin_required
def show_epub_fixer_logs():
    logs = get_logs_from_archive("epub-fixer")
    log_dates = get_log_dates(logs)
    return render_title_template('cwa_list_logs.html', title=_("Lily - EPUB Fixer Service"), page="cwa-epub-fixer-logs",
                                logs=logs, log_dates=log_dates)

@epub_fixer.route('/cwa-epub-fixer/download-current-log/<log_filename>')
@login_required_if_no_ano
@admin_required
def download_current_log(log_filename):
    log_filename = "epub-fixer.log"
    LOG_DIR = "/config"
    try:
        # Secure the filename to prevent directory traversal (e.g., '..')
        safe_filename = secure_filename(log_filename)
        
        # Join the logs directory with the filename and get the absolute path
        file_path = os.path.abspath(os.path.join(LOG_DIR, safe_filename))
        
        # Check if the file path is within the allowed directory
        if not file_path.startswith(os.path.abspath(LOG_DIR)):
            abort(403)  # Forbidden if it's not within the logs directory

        # Check if the file exists
        if not os.path.exists(file_path):
            abort(404)  # Return a 404 if the file does not exist

        # Send the file as an attachment (to trigger a download)
        return send_from_directory(LOG_DIR, safe_filename, as_attachment=True)
    
    except Exception as e:
        # Handle any other errors
        abort(400)  # Bad request for malformed or unsafe file paths

def start_epub_fixer_run():
    """Start a full-library EPUB Fixer run in background threads.

    Called by the admin route and directly by TaskEpubFixerRun (scheduled runs).
    """
    # Wipe conversion log from previous runs
    open('/config/epub-fixer.log', 'w').close()
    # Remove any left over kill file
    try:
        os.remove(tempfile.gettempdir() + "/.kill_epub_fixer_trigger")
    except FileNotFoundError:
        ...
    # Queue to share the subprocess reference
    process_queue = queue.Queue()
    # Create and start the subprocess thread
    ef_thread = Thread(target=epub_fixer_start, args=(process_queue,))
    ef_thread.start()
    # Create and start the kill thread
    ef_kill_thread = Thread(target=kill_epub_fixer, args=(process_queue,))
    ef_kill_thread.start()

def request_epub_fixer_cancel():
    # Create kill trigger file
    open(tempfile.gettempdir() + "/.kill_epub_fixer_trigger", 'w').close()
    try:
        subprocess.run([
            "pkill",
            "-f",
            "/app/calibre-web-automated/scripts/kindle_epub_fixer.py"
        ], check=False)
    except Exception as e:
        log.error(f"Failed to terminate epub fixer process: {e}")

@epub_fixer.route('/cwa-epub-fixer-start', methods=["POST"])
@login_required_if_no_ano
@admin_required
def start_epub_fixer():
    start_epub_fixer_run()
    return redirect(url_for('epub_fixer.show_epub_fixer_page'))


@epub_fixer.route('/cwa-epub-fixer/run-book', methods=["POST"])
@login_required_if_no_ano
def run_epub_fixer_for_book():
    # Rewrites the book's files, so require edit rights (anonymous browsing passes the login check).
    if not (current_user.role_edit() or current_user.role_admin()):
        abort(403)
    if config.config_use_google_drive:
        return jsonify({"success": False, "error": _("Single-book EPUB Fixer is not supported with Google Drive libraries.")}), 400

    payload = request.get_json(silent=True) or {}
    book_id = payload.get("book_id") or request.form.get("book_id")
    try:
        book_id = int(book_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": _("Invalid book selection.")}), 400

    try:
        calibre_db.ensure_session()
        book = calibre_db.get_book(book_id)
        if not book:
            return jsonify({"success": False, "error": _("Book not found.")}), 404

        data = calibre_db.get_book_format(book_id, "EPUB")
        if not data:
            data = calibre_db.get_book_format(book_id, "EPUB3")
        if not data:
            return jsonify({"success": False, "error": _("Selected book has no EPUB format.")}), 400

        epub_path = os.path.join(config.get_book_path(), book.path, f"{data.name}.{data.format.lower()}")
        if not os.path.exists(epub_path):
            return jsonify({"success": False, "error": _("EPUB file not found on disk.")}), 404

        # Wipe conversion log from previous runs
        open('/config/epub-fixer.log', 'w').close()
        # Remove any left over kill file
        try:
            os.remove(tempfile.gettempdir() + "/.kill_epub_fixer_trigger")
        except FileNotFoundError:
            ...

        process_queue = queue.Queue()
        ef_thread = Thread(target=epub_fixer_start, args=(process_queue, epub_path))
        ef_thread.start()
        ef_kill_thread = Thread(target=kill_epub_fixer, args=(process_queue,))
        ef_kill_thread.start()
        return jsonify({"success": True, "message": _("EPUB Fixer started for selected book.")})
    except Exception as e:
        log.error(f"Failed to start EPUB Fixer for book {book_id}: {e}")
        return jsonify({"success": False, "error": _("Failed to start EPUB Fixer for selected book.")}), 500

@epub_fixer.route('/epub-fixer-cancel', methods=["POST"])
@login_required_if_no_ano
@admin_required
def cancel_epub_fixer():
    request_epub_fixer_cancel()
    return redirect(url_for('epub_fixer.show_epub_fixer_page'))

@epub_fixer.route('/epub-fixer-status', methods=["GET"])
@login_required_if_no_ano
@admin_required
def get_status():
    return _log_status_response("/config/epub-fixer.log")
