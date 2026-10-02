# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Import & Metadata settings page (/cwa-settings) and metadata-provider settings helpers."""

from flask import redirect, flash, url_for, request, jsonify
from flask_babel import gettext as _

from .. import config
from ..cw_login import current_user
from ..usermanagement import login_required_if_no_ano
from ..admin import admin_required
from ..render_template import render_title_template

# common puts the scripts dir on sys.path, so it must be imported before cwa_db
from .common import cwa_settings
from cwa_db import CWA_DB


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
                                 cwa_settings=cwa_db.get_cwa_settings(), config=config)


@cwa_settings.route("/cwa-settings/rebuild-metadata", methods=["POST"])
@login_required_if_no_ano
@admin_required
def rebuild_metadata():
    """Start a lookup of every book with the metadata providers, one at a time. It runs on its
    own thread, so covers and duplicate scans don't wait behind it."""
    from ..services.worker import WorkerThread
    from ..tasks.metadata_rebuild import TaskRebuildMetadata
    if _running_rebuilds(including_stopping=True):
        return jsonify({"success": True, "running": True})
    task = TaskRebuildMetadata()
    WorkerThread.add_parallel(current_user.name, task)
    return jsonify({"success": True, "task_id": str(task.id)})


@cwa_settings.route("/cwa-settings/rebuild-metadata/stop", methods=["POST"])
@login_required_if_no_ano
@admin_required
def stop_rebuild_metadata():
    """Stop a running rebuild after the book it is on; what it changed so far stays."""
    from ..services.worker import WorkerThread
    running = _running_rebuilds()
    for task in running:
        WorkerThread.get_instance().end_task(task.id)
    return jsonify({"success": True, "stopped": len(running)})


def _running_rebuilds(including_stopping=False):
    """Rebuild tasks still to finish; with including_stopping, also those stopped but still on
    their last book, so a new rebuild never runs beside one."""
    from ..services.worker import WorkerThread, STAT_WAITING, STAT_STARTED
    return [task for __, __, __, task, __ in WorkerThread.get_instance().tasks
            if type(task).__name__ == "TaskRebuildMetadata"
            and (task.stat in (STAT_WAITING, STAT_STARTED)
                 or (including_stopping and not getattr(task, "finished", True)))]
