# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Service-status check, log archive download/read routes and log archive helpers."""

from flask import redirect, flash, url_for, send_from_directory, abort
from flask_babel import gettext as _

from ..usermanagement import login_required_if_no_ano
from ..admin import admin_required
from ..render_template import render_title_template

import subprocess
from pathlib import Path

import os
from datetime import datetime
import re
import shutil
from werkzeug.utils import secure_filename

from .common import cwa_check_status, cwa_logs, LOG_ARCHIVE

# Lines of an archived log rendered in the browser (the full file is still downloadable)
READ_LOG_MAX_LINES = 10000

##————————————————————————————————————————————————————————————————————————————##
##                                                                            ##
##                               CWA CHECK STATUS                             ##
##                                                                            ##
##————————————————————————————————————————————————————————————————————————————##

@cwa_check_status.route("/cwa-check-monitoring", methods=["GET", "POST"])
@login_required_if_no_ano
@admin_required
def cwa_flash_status():
    result = subprocess.run(['/app/calibre-web-automated/scripts/check-cwa-services.sh'])
    services_status = result.returncode

    match services_status:
        case 0:
            flash(_("✅ All Monitoring Services are running as intended! 👍"), category="cwa_refresh")
        case 1:
            flash(_("🔴 The Ingest Service is running but the Metadata Change Detector is not"), category="cwa_refresh")
        case 2:
            flash(_("🔴 The Metadata Change Detector is running but the Ingest Service is not"), category="cwa_refresh")
        case 3:
            flash(_("⛔ Neither the Ingest Service or the Metadata Change Detector are running"), category="cwa_refresh")
        case _:
            flash(_("An Error has occurred"), category="cwa_refresh")

    return redirect(url_for('admin.admin'))

##————————————————————————————————————————————————————————————————————————————##
##                                                                            ##
##                                 CWA LOGS                                   ##
##                                                                            ##
##————————————————————————————————————————————————————————————————————————————##

@cwa_logs.route('/cwa-logs/download/<log_filename>')
@login_required_if_no_ano
@admin_required
def download_log(log_filename):
    try:
        # Secure the filename to prevent directory traversal (e.g., '..')
        safe_filename = secure_filename(log_filename)
        
        # Join the logs directory with the filename and get the absolute path
        file_path = os.path.abspath(os.path.join(LOG_ARCHIVE, safe_filename))
        
        # Check if the file path is within the allowed directory
        if not file_path.startswith(os.path.abspath(LOG_ARCHIVE)):
            abort(403)  # Forbidden if it's not within the logs directory

        # Check if the file exists
        if not os.path.exists(file_path):
            abort(404)  # Return a 404 if the file does not exist

        # Send the file as an attachment (to trigger a download)
        return send_from_directory(LOG_ARCHIVE, safe_filename, as_attachment=True)
    
    except Exception as e:
        # Handle any other errors
        abort(400)  # Bad request for malformed or unsafe file paths

@cwa_logs.route('/cwa-logs/read/<log_filename>')
@login_required_if_no_ano
@admin_required
def read_log(log_filename):
    try:
        # Secure the filename to prevent directory traversal (e.g., '..')
        safe_filename = secure_filename(log_filename)
        
        # Join the logs directory with the filename and get the absolute path
        file_path = os.path.abspath(os.path.join(LOG_ARCHIVE, safe_filename))
        
        # Check if the file path is within the allowed directory
        if not file_path.startswith(os.path.abspath(LOG_ARCHIVE)):
            abort(403)  # Forbidden if it's not within the logs directory

        # Check if the file exists
        if not os.path.exists(file_path):
            abort(404)  # Return a 404 if the file does not exist

        # Render at most the last READ_LOG_MAX_LINES lines; the full log stays downloadable
        log, truncated = read_log_tail(file_path, READ_LOG_MAX_LINES)
        if truncated:
            log = (f"[... earlier lines omitted - showing the last {READ_LOG_MAX_LINES} lines. "
                   f"Use Download Log for the full file ...]\n\n") + log

        return render_title_template('cwa_read_log.html', title=_(f"Lily - Log Archive - Read Log - {log_filename}"), page="cwa-log-read",
                                    log_filename=log_filename, log=log)
    
    except Exception as e:
        # Handle any other errors
        abort(400)  # Bad request for malformed or unsafe file paths

##——————————————LOG ARCHIVE HELPERS (used by Convert Library / EPUB Fixer)——————————————##

def extract_progress(log_content):
    """Analyses a log's given contents & returns the processes current progress as a dict"""
    # Regex to find all progress matches (e.g., "n/n")
    matches = re.findall(r'(\d+)/(\d+)', log_content)
    if matches:
        # Convert the matches to integers and take the last one
        current, total = map(int, matches[-1])
        return {"current": current, "total": total}
    return {"current": 0, "total": 0}

_PROGRESS_RE = re.compile(r'(\d+)/(\d+)')

def read_log_tail(log_path, max_lines):
    """Return (tail_text, truncated) with at most the last max_lines lines of log_path.

    Reads backwards from the end in blocks so a huge log is never held in memory whole.
    Raises FileNotFoundError like open() does.
    """
    block = 64 * 1024
    with open(log_path, 'rb') as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        data = b''
        # max_lines + 1 newlines guarantee max_lines complete lines after the first split point
        while pos > 0 and data.count(b'\n') <= max_lines:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
    lines = data.decode('utf-8', errors='replace').split('\n')
    if pos > 0:
        lines = lines[1:]  # first piece is a partial line cut by the block boundary
    trailing = 1 if lines and lines[-1] == '' else 0  # '' after a final newline isn't a line
    truncated = pos > 0
    if len(lines) - trailing > max_lines:
        lines = lines[-(max_lines + trailing):]
        truncated = True
    return '\n'.join(lines), truncated

def extract_progress_from_file(log_path, tail_text=None, truncated=False):
    """extract_progress() for a log file without loading it all.

    The last "n/n" match in the file is the last match in its tail when the tail has one; only
    when it doesn't (and the tail isn't the whole file) is the file scanned line by line.
    A match can't span lines (\\d never matches a newline), so the per-line scan is equivalent.
    """
    if tail_text is not None and (not truncated or _PROGRESS_RE.search(tail_text)):
        return extract_progress(tail_text)
    last = None
    with open(log_path, 'r', errors='replace') as f:
        for line in f:
            matches = _PROGRESS_RE.findall(line)
            if matches:
                last = matches[-1]
    if last:
        current, total = map(int, last)
        return {"current": current, "total": total}
    return {"current": 0, "total": 0}

def archive_run_log(log_path):
    try:
        log_name = Path(log_path).stem + f"-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.log"
        shutil.copy2(log_path, f"{LOG_ARCHIVE}/{log_name}")
        print(f"[cwa-functions] Log '{log_path}' has been successfully archived as {log_name} in '{LOG_ARCHIVE}'")
    except Exception as e:
        print(f"[cwa-functions] The following error occurred when trying to back up {log_path} at {datetime.now()}:\n{e}")

def get_logs_from_archive(log_name) -> dict[str,str]:
    logs = {}
    logs_in_archive = [os.path.join(dirpath,f) for (dirpath, dirnames, filenames) in os.walk(LOG_ARCHIVE) for f in filenames]
    for log in logs_in_archive:
        if log_name in log:
            logs |= {os.path.basename(log):log}

    return logs

def get_log_dates(logs) -> dict[str,str]:
    log_dates = {}
    for log in logs:
        log_date, time = re.findall(r"([0-9]{4}-[0-9]{2}-[0-9]{2})-([0-9]+)+", log)[0]
        log_time = f"{time[:2]}:{time[2:4]}:{time[-2:]}"
        log_dates |= {log:{"date":log_date,
                            "time":log_time}}
    return log_dates
