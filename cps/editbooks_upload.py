# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Browser uploads: validation, the ingest hand-off, and the upload endpoint.

Routes are attached to the editbook blueprint; editbooks.py imports this module at its end."""

import os
import re
from datetime import datetime, UTC


from flask import flash
from flask_babel import gettext as _

from . import config, constants
from .file_helper import validate_mime_type
from .cwa_functions import get_ingest_dir
from werkzeug.utils import secure_filename
import uuid

from flask_babel import lazy_gettext as N_
from flask import request, url_for, abort, Response
from .tasks.upload import TaskUpload
from .services.worker import WorkerThread
from . import calibre_db
from .recent_imports import earlier_copy, unreadable_formats
from .cw_login import current_user
from markupsafe import escape
import json
from .usermanagement import login_required_if_no_ano

from .editbooks import editbook, log, upload_required


# Helper to validate upload according to server settings
def _validate_uploaded_file(uploaded_file):
    """The name decides whether the format is allowed; only then is the content checked,
    so a damaged file isn't reported as a format the server refuses."""
    allowed_extensions = config.config_upload_formats.split(',')
    if '.' not in uploaded_file.filename:
        flash(_("“%(name)s” has no file type at the end of its name (like .epub or .pdf), so it wasn't "
                "added.", name=uploaded_file.filename), category="error")
        return False
    file_ext = uploaded_file.filename.rsplit('.', 1)[-1].lower()
    if file_ext not in allowed_extensions and '' not in allowed_extensions:
        flash(_("Lily can't open .%(ext)s files, so “%(name)s” wasn't added. Upload an EPUB, PDF or DjVu "
                "book, or an audiobook.", ext=file_ext, name=uploaded_file.filename), category="error")
        return False
    if config.config_check_extensions and allowed_extensions != ['']:
        if not validate_mime_type(uploaded_file, allowed_extensions):
            flash(_("“%(name)s” couldn't be read as %(ext)s, so it wasn't added. The file may be "
                    "damaged or incomplete; try downloading or exporting it again.",
                    name=uploaded_file.filename, ext=file_ext.upper()), category="error")
            return False
    return True


# Ingest file names the upload status route will report on: <new|format>_<id>_<timestamp>_<name>
_QUEUED_NAME = re.compile(r"^(new|format)_(\d+)_\d{8}_\d{6}_\d+_[\w.\-]*$")


def _upload_response(location, queued):
    """The JSON uploadprogress.js reads: where to go next and, when files were queued,
    what lily.js should watch until the ingest finishes."""
    body = {"location": location}
    if queued:
        body["uploads"] = queued
        body["status_url"] = url_for("edit-book.upload_status")
    return Response(json.dumps(body), mimetype='application/json')

def upload_too_large_response():
    """Plain-text 413 for an upload over MAX_UPLOAD_BYTES; uploadprogress.js shows the text."""
    limit_mb = constants.MAX_UPLOAD_BYTES // (1000 * 1000)
    message = _("This file is larger than the %(size)s MB upload limit. Try a smaller file.", size=limit_mb)
    return Response(message, status=413, mimetype="text/plain")


# Helper to get a unique, prefixed path in the ingest directory
def _get_ingest_path(uploaded_file, prefix_parts=None):
    ingest_dir = get_ingest_dir()
    try:
        os.makedirs(ingest_dir, exist_ok=True)
    except Exception as e:
        log.error_or_exception("Failed to create ingest directory %s: %s", ingest_dir, e)
        raise
    # Ensure proper ownership of ingest directory (fix for issue #603)
    try:
        nsm = os.getenv("NETWORK_SHARE_MODE", "false").strip().lower() in ("1", "true", "yes", "on")
        if not (nsm and ingest_dir == "/cwa-book-ingest"):
            # Set ownership to abc:abc (uid=1000, gid=1000)
            os.chown(ingest_dir, 1000, 1000)
    except (OSError, PermissionError) as e:
        # Log warning but don't crash the upload process
        log.warning('Failed to set ownership of ingest directory %s: %s', ingest_dir, e)
        log.warning("If you're using a network share, consider setting NETWORK_SHARE_MODE=true in your environment variables to skip this step.")
    except Exception as e:
        # Silently ignore any other permission-related errors but log for debugging
        log.debug('Other permission error setting ingest directory ownership: %s', e)

    _ensure_ingest_dir_writable(ingest_dir)

    base_name = secure_filename(uploaded_file.filename)
    # CWA change: use timestamp for more predictable sorting vs uuid
    unique = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    prefix = "_".join([str(p) for p in (prefix_parts or []) if p])
    final_name = f"{prefix + '_' if prefix else ''}{unique}_{base_name}"
    final_path = os.path.join(ingest_dir, final_name)
    return final_path

# Helper to save file to a temporary path, then atomically rename to final path
def _save_to_ingest_atomic_rename(uploaded_file, final_path):
    tmp_path = final_path + ".uploading"
    try:
        # Stream directly into ingest dir, then atomically rename
        uploaded_file.save(tmp_path)
        return tmp_path, final_path
    except Exception as e:
        # Ensure partial uploads are cleaned up on error
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise e


def _ensure_ingest_dir_writable(ingest_dir=None, allow_create=False, check_write=True):
    ingest_dir = ingest_dir or get_ingest_dir()
    if not ingest_dir:
        raise PermissionError("Ingest directory not configured")
    if allow_create and not os.path.isdir(ingest_dir):
        try:
            os.makedirs(ingest_dir, exist_ok=True)
        except Exception as e:
            raise PermissionError(f"Ingest directory missing and could not be created: {ingest_dir} ({e})")
    if not os.path.isdir(ingest_dir):
        raise PermissionError(f"Ingest directory missing: {ingest_dir}")
    if check_write:
        if not os.access(ingest_dir, os.W_OK | os.X_OK):
            raise PermissionError(f"Ingest directory not writable: {ingest_dir}")
        # Verify write by touching a temp file (covers ACL/mount oddities)
        test_path = os.path.join(ingest_dir, f".cwa_write_test_{uuid.uuid4().hex}")
        try:
            with open(test_path, "w", encoding="utf-8") as handle:
                handle.write("ok")
        except Exception as e:
            raise PermissionError(f"Ingest directory not writable: {ingest_dir} ({e})")
        finally:
            try:
                if os.path.exists(test_path):
                    os.remove(test_path)
            except Exception:
                pass


@editbook.route("/upload", methods=["POST"])
@login_required_if_no_ano
@upload_required
def upload():
    # Turn an oversize body away before anything reads (and buffers) the form
    if request.content_length is not None and request.content_length > constants.MAX_UPLOAD_BYTES:
        log.warning("Upload rejected: %s bytes is over the %s-byte limit",
                    request.content_length, constants.MAX_UPLOAD_BYTES)
        return upload_too_large_response()
    try:
        log.info(
            "Upload request received: user_agent=%s content_length=%s file_fields=%s",
            request.headers.get("User-Agent", ""),
            request.content_length,
            list(request.files.keys())
        )
    except Exception as e:
        log.debug("Failed to log upload request details: %s", e)
    # Upload a new format to an existing book via ingest sidecar manifest
    if len(request.files.getlist("btn-upload-format")):
        try:
            _ensure_ingest_dir_writable(allow_create=True, check_write=False)
        except PermissionError as e:
            log.error_or_exception("Ingest directory not writable: %s", e)
            flash(_("Lily can't write to the import folder, so the upload wasn't saved. Check that folder's permissions."),
                  category="error")
            return Response(json.dumps({"location": url_for("web.index")}), mimetype='application/json')
        raw_book_id = request.form.get('book_id', -1)
        try:
            book_id = int(raw_book_id)
        except Exception:
            book_id = -1
        if book_id == -1:
            flash(_("Missing or invalid book id for format upload"), category="error")
            return Response(json.dumps({"location": url_for("web.index")}), mimetype='application/json')

        # Validate that the book exists before creating manifest
        book = calibre_db.get_book(book_id)
        if not book:
            flash(_("Cannot upload format: Book no longer exists in library"), category="error")
            return Response(json.dumps({"location": url_for("web.index")}), mimetype='application/json')

        queued = []
        for requested_file in request.files.getlist("btn-upload-format"):
            if not _validate_uploaded_file(requested_file):
                return _upload_response(url_for('edit-book.show_edit_book', book_id=book_id), queued)

            try:
                final_path = _get_ingest_path(requested_file, prefix_parts=["format", book_id])
                tmp_path, final_path = _save_to_ingest_atomic_rename(requested_file, final_path)

                # Write sidecar manifest instructing ingest to add this file as a new format
                manifest = {
                    "action": "add_format",
                    "book_id": book_id,
                    "original_filename": requested_file.filename,
                }
                manifest_path = final_path + ".cwa.json"
                with open(manifest_path, 'w', encoding='utf-8') as mf:
                    json.dump(manifest, mf, ensure_ascii=False)

                # Now that manifest is written, perform the atomic rename to trigger ingest
                os.replace(tmp_path, final_path)
                queued.append({"file": os.path.basename(final_path), "name": requested_file.filename})

                # Queue a task entry for UX feedback
                upload_text = N_("Upload done, processing, please wait...")
                WorkerThread.add(current_user.name, TaskUpload(upload_text, escape(requested_file.filename)))

            except Exception as e:
                log.error_or_exception(f"Failed to queue format upload for ingest: {e}")
                flash(_("Failed to queue upload for processing"), category="error")
                return _upload_response(url_for('edit-book.show_edit_book', book_id=book_id), queued)

        # Redirect back to the book edit page
        return _upload_response(url_for('edit-book.show_edit_book', book_id=book_id), queued)

    # New book uploads: queue files to ingest atomically
    if len(request.files.getlist("btn-upload")):
        try:
            _ensure_ingest_dir_writable(allow_create=True, check_write=False)
        except PermissionError as e:
            log.error_or_exception("Ingest directory not writable: %s", e)
            flash(_("Lily can't write to the import folder, so the upload wasn't saved. Check that folder's permissions."),
                  category="error")
            return Response(json.dumps({"location": url_for("web.index")}), mimetype='application/json')
        queued = []
        for requested_file in request.files.getlist("btn-upload"):
            if not _validate_uploaded_file(requested_file):
                return _upload_response(url_for('web.index'), queued)
            try:
                final_path = _get_ingest_path(requested_file, prefix_parts=["new", current_user.id])
                tmp_path, final_path = _save_to_ingest_atomic_rename(requested_file, final_path)
                os.replace(tmp_path, final_path) # No manifest needed, just rename
                queued.append({"file": os.path.basename(final_path), "name": requested_file.filename})
                upload_text = N_("Upload done, processing, please wait...")
                WorkerThread.add(current_user.name, TaskUpload(upload_text, escape(requested_file.filename)))
            except Exception as e:
                log.error_or_exception(f"Failed to queue upload for ingest: {e}")
                flash(_("Failed to queue upload for processing"), category="error")
                return _upload_response(url_for('web.index'), queued)

        return _upload_response(url_for('web.index'), queued)
    abort(400)


@editbook.route("/upload/status", methods=["GET"])
@login_required_if_no_ano
@upload_required
def upload_status():
    """How each queued upload is getting on: queued (the ingest hasn't picked it up yet),
    running, succeeded, failed, skipped or interrupted. Only names this route's uploads
    produce are answered, and new-book names only for the user who uploaded them."""
    try:
        from scripts.automation_jobs import latest_job_for_file
    except ImportError:
        from automation_jobs import latest_job_for_file
    files = []
    for name in request.args.getlist("file")[:20]:
        match = _QUEUED_NAME.match(name or "")
        if not match or (match.group(1) == "new" and int(match.group(2)) != int(current_user.id)):
            abort(400)
        try:
            job = latest_job_for_file("ingest", name)
        except Exception as e:
            log.warning("could not read the ingest job for %s: %s", name, e)
            job = None
        entry = {"file": name,
                 "state": job["state"] if job else "queued",
                 "error": (job["error"] or "") if job else ""}
        if job and job["state"] == "succeeded" and job.get("book_id"):
            entry["book_id"] = int(job["book_id"])
            # Said in the toast: a book the library already had, a file no reader can open
            entry["earlier_copy"] = earlier_copy(entry["book_id"])
            entry["unreadable"] = unreadable_formats(entry["book_id"])
        files.append(entry)
    return Response(json.dumps({"files": files}), mimetype='application/json')
