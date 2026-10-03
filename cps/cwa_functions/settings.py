# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Import & Metadata settings page (/cwa-settings) and metadata-provider settings helpers."""

import threading

from flask import redirect, flash, url_for, request, jsonify
from flask_babel import gettext as _

from .. import config, logger
from ..cw_login import current_user
from ..usermanagement import login_required_if_no_ano
from ..admin import admin_required
from ..render_template import render_title_template

# common puts the scripts dir on sys.path, so it must be imported before cwa_db
from .common import cwa_settings
from cwa_db import CWA_DB

log = logger.create()

_rebuild_start_lock = threading.Lock()


##————————————————————————————————————————————————————————————————————————————##
##                                                                            ##
##                              CWA SETTINGS PAGE                             ##
##                                                                            ##
##————————————————————————————————————————————————————————————————————————————##

# What the Import & Metadata page shows. Toggles missing from a submitted form are off;
# every Lily setting not listed here keeps its stored value.
FORM_TOGGLES = ['auto_metadata_fetch_enabled', 'auto_metadata_enforcement']
AUTOMERGE_OPTIONS = ['new_record', 'overwrite', 'ignore']


@cwa_settings.route("/cwa-settings", methods=["GET", "POST"])
@login_required_if_no_ano
@admin_required
def set_cwa_settings():
    cwa_db = CWA_DB()
    if request.method == 'POST':
        result = {setting: 1 if request.form.get(setting) else 0 for setting in FORM_TOGGLES}
        if request.form.get('auto_ingest_automerge') in AUTOMERGE_OPTIONS:
            result['auto_ingest_automerge'] = request.form['auto_ingest_automerge']
        cwa_db.update_cwa_settings(result)

        config.config_uploading = 1 if request.form.get('config_uploading') else 0
        if 'config_google_books_api_key' in request.form:
            config.config_google_books_api_key = request.form['config_google_books_api_key'].strip()
        config.save()

        flash(_("Settings saved"), category="success")
        return redirect(url_for('cwa_settings.set_cwa_settings'))

    return render_title_template("cwa_settings.html", title=_("Import & Metadata"), page="cwa-settings",
                                 cwa_settings=cwa_db.get_cwa_settings(), config=config, lookups=_lookup_counts(cwa_db),
                                 rebuild_time=_rebuild_time())


# A lookup takes about this long a book, four at once (measured on 2026-10-03)
SECONDS_A_BOOK = 1.5


def _rebuild_time():
    """For the confirmation: how many books a rebuild looks up (those not up to date) and a full
    rebuild (all), and how long each takes, in words."""
    from .. import calibre_db, db
    from ..tasks.metadata_rebuild import TaskRebuildMetadata
    try:
        books = calibre_db.session.query(db.Books.id, db.Books.last_modified).all()
        task = TaskRebuildMetadata()
        task._store = CWA_DB()
        pending = len(task._still_to_look_up(books))
    except Exception as e:
        log.debug("No books to count: %s", e)
        return {"books": 0, "pending": 0, "time": "", "full_time": ""}
    return {"books": len(books), "pending": pending, "time": _duration(pending), "full_time": _duration(len(books))}


def _duration(books):
    hours = books * SECONDS_A_BOOK / 3600
    if hours < 1:
        return _("under an hour")
    if hours < 1.5:
        return _("about an hour")
    return _("about %(hours)s hours", hours=round(hours))


def _lookup_counts(cwa_db):
    """{status: books} for the books whose last metadata lookup failed or found no match,
    counting only books still in the library."""
    from .. import calibre_db, db
    try:
        books = {row[0] for row in calibre_db.session.query(db.Books.id)}
        counts = {}
        for status in ("failed", "nomatch"):
            counts[status] = sum(1 for book_id in cwa_db.metadata_lookup_ids(status) if book_id in books)
        return counts
    except Exception as e:
        log.debug("No metadata lookups to count: %s", e)
        return {}


@cwa_settings.route("/cwa-settings/rebuild-metadata", methods=["POST"])
@login_required_if_no_ano
@admin_required
def rebuild_metadata():
    """Start a lookup of every book with the metadata providers, a few at once. It runs on its
    own thread, so covers and duplicate scans don't wait behind it. With `failed`, it looks up only
    the books whose last lookup failed (Retry failed). With `book_ids` (comma-separated), only
    those books. With `resume`, it carries on where a stopped or interrupted rebuild got to. With
    `full`, it forgets what earlier lookups found and looks every book up again, instead of
    skipping the books that are up to date."""
    from ..services.worker import WorkerThread
    from ..tasks.metadata_rebuild import TaskRebuildMetadata
    # Two requests at once (two tabs) start one rebuild
    with _rebuild_start_lock:
        if _running_rebuilds(including_stopping=True):
            return jsonify({"success": True, "running": True})
        if request.form.get("failed"):
            book_ids = CWA_DB().metadata_lookup_ids("failed")
            if not book_ids:
                return jsonify({"success": True, "none": True})
            task = TaskRebuildMetadata(book_ids=book_ids)
        elif request.form.get("book_ids"):
            # The books ticked in the book table
            book_ids = [int(i) for i in request.form["book_ids"].split(",") if i.strip().isdigit()]
            if not book_ids:
                return jsonify({"success": True, "none": True})
            task = TaskRebuildMetadata(book_ids=book_ids, selection=True)
        else:
            task = TaskRebuildMetadata(resume=bool(request.form.get("resume")), full=bool(request.form.get("full")))
        WorkerThread.add_parallel(current_user.name, task)
    return jsonify({"success": True, "task_id": str(task.id)})


@cwa_settings.route("/cwa-settings/rebuild-metadata/stop", methods=["POST"])
@login_required_if_no_ano
@admin_required
def stop_rebuild_metadata():
    """Stop a running rebuild after the books it is on; what it changed so far stays."""
    from ..services.worker import WorkerThread
    running = _running_rebuilds()
    for task in running:
        WorkerThread.get_instance().end_task(task.id)
    return jsonify({"success": True, "stopped": len(running)})


@cwa_settings.route("/cwa-settings/rebuild-metadata/status")
@login_required_if_no_ano
@admin_required
def rebuild_metadata_status():
    """The latest rebuild, for the settings page's status line. `state` is idle (none since the
    server started) or the task's own (TaskRebuildMetadata.state). `resume` says how far an
    unfinished rebuild got, when the next can carry on."""
    from ..tasks.metadata_rebuild import saved_progress
    rebuilds = _rebuilds()
    task = rebuilds[-1] if rebuilds else None
    resume = ""
    if task is None or task.state not in ("running", "stopping"):
        progress = saved_progress()
        if progress:
            resume = _("The last rebuild stopped after %(checked)s of %(total)s books.",
                       checked=progress["checked"], total=progress["total"])
    if task is None:
        # None since the server started: one cut short by the restart shows as stopped
        return jsonify({"state": "idle", "message": resume, "resume": resume})
    return jsonify({"state": task.state, "message": str(task.status_line), "resume": resume})


def _rebuilds():
    """Every rebuild task the worker still lists, oldest first."""
    from ..services.worker import WorkerThread
    from ..tasks.metadata_rebuild import TaskRebuildMetadata
    return [task for __, __, __, task, __ in WorkerThread.get_instance().tasks
            if isinstance(task, TaskRebuildMetadata)]


def _running_rebuilds(including_stopping=False):
    """Rebuild tasks still to finish; with including_stopping, also those stopped but still on
    their last book, so a new rebuild never runs beside one."""
    return [task for task in _rebuilds()
            if task.state == "running" or (including_stopping and task.state == "stopping")]
