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

import os
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
    
    except Exception:
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

        return render_title_template('cwa_read_log.html', title=_("Lily - Log Archive - Read Log - %(filename)s", filename=log_filename), page="cwa-log-read",
                                    log_filename=log_filename, log=log)
    
    except Exception:
        # Handle any other errors
        abort(400)  # Bad request for malformed or unsafe file paths

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
