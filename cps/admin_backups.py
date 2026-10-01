# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Admin routes for database backups, restores, the library mirror and failed imports.

Registered on the admin blueprint (see the bottom of admin.py), so endpoint names are
unchanged: admin.db_backups, admin.restore_db_snapshot, admin.ingest_failures, ...
"""

import os

from flask import abort, flash, jsonify, redirect, request, send_file, url_for
from flask_babel import gettext as _
from markupsafe import Markup

from . import config, logger
from .admin import admi, admin_required, _tasks_page_link
from .cw_login import current_user
from .render_template import render_title_template
from .services.worker import WorkerThread
from .usermanagement import user_login_required

log = logger.create()

# --- Last Resort Calibre DB Restore / database snapshot restore ---
# Both run as background tasks: calibredb restore_database can take ~20 minutes and a
# blocking subprocess inside the request (gevent, no monkey-patching) froze the server.
from .tasks.restore import _acquire_service_lock, restore_in_progress, mark_restore_queued  # noqa: E402,F401




@admi.route("/admin/restore_calibre_db", methods=["POST"])
@user_login_required
@admin_required
def restore_calibre_db():
    """Queue a restore of Calibre metadata.db from the library's OPF files (last resort recovery)."""
    if not config.config_calibre_dir:
        flash(_("Restore failed: Calibre library path is not configured."), category="error")
        return redirect(url_for("admin.db_configuration"))
    metadata_path = os.path.join(config.config_calibre_dir, "metadata.db")
    if not os.path.exists(metadata_path):
        flash(_("Restore failed: metadata.db not found at %(path)s", path=metadata_path), category="error")
        return redirect(url_for("admin.db_configuration"))
    if not mark_restore_queued():
        flash(_("Restore already in progress."), category="error")
        return redirect(url_for("admin.db_configuration"))

    from .tasks.restore import TaskRestoreCalibreLibrary
    WorkerThread.add(current_user.name, TaskRestoreCalibreLibrary())
    flash(Markup(_("Restore started in the background. Follow its progress on the %(link)s page; "
                   "databases are backed up to /config/backup first.", link=_tasks_page_link())),
          category="success")
    return redirect(url_for("admin.db_configuration"))


def _format_size(num_bytes):
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return ("%d %s" % (size, unit)) if unit == "B" else ("%.1f %s" % (size, unit))
        size /= 1024


def _ingest_failure_dirs():
    from .tasks.db_backup import get_backup_root  # noqa: F401 (puts scripts/ on sys.path)
    from ingest_failures import FAILED_DIR
    from .cwa_functions.ingest import get_ingest_dir
    return FAILED_DIR, get_ingest_dir()


@admi.route("/admin/ingest_failures", methods=["GET"])
@user_login_required
@admin_required
def ingest_failures():
    """Lists files the ingest pipeline rejected, with Retry and Delete per file."""
    from .tasks.db_backup import get_backup_root  # noqa: F401 (puts scripts/ on sys.path)
    from ingest_failures import list_failed, FAILED_DIR
    failed = list_failed(FAILED_DIR)
    for item in failed:
        item["size_text"] = _format_size(item["size"])
    return render_title_template("ingest_failures.html", title=_("Failed Imports"), page="ingest_failures",
                                 failed=failed, failed_dir=FAILED_DIR)


@admi.route("/admin/ingest_failures/<action>", methods=["POST"])
@user_login_required
@admin_required
def ingest_failure_action(action):
    from ingest_failures import retry_failed, delete_failed
    failed_dir, ingest_dir = _ingest_failure_dirs()
    names = [n for n in request.form.getlist("names") if n]
    if action not in ("retry", "delete") or not names:
        abort(400)
    done = 0
    for name in names:
        try:
            if action == "retry":
                retry_failed(failed_dir, ingest_dir, name)
            else:
                delete_failed(failed_dir, name)
            done += 1
        except (ValueError, FileNotFoundError):
            flash(_("File not found: %(name)s", name=name), category="error")
        except OSError as e:
            log.error("Ingest failure %s of %s failed: %s", action, name, e)
            flash(_("Could not %(action)s %(name)s; see the server log.", action=action, name=name), category="error")
    if done:
        flash(_("Queued %(n)d file(s) for another import attempt.", n=done) if action == "retry"
              else _("Deleted %(n)d file(s).", n=done), category="success")
    return redirect(url_for("admin.ingest_failures"))


@admi.route("/admin/db_backups", methods=["GET"])
@user_login_required
@admin_required
def db_backups():
    """Lists the nightly database snapshots with a Restore action per snapshot."""
    from .tasks.db_backup import get_backup_root, _configured_backup_dir, RESTORABLE_DBS
    from .tasks.library_mirror import get_mirror_dir
    from db_backup import describe_snapshots
    from .tasks.processed_cleanup import get_retention_days
    backup_root = get_backup_root()
    try:
        snapshots = describe_snapshots(backup_root)
    except OSError as e:
        log.error("Could not list database snapshots in %s: %s", backup_root, e)
        snapshots = []
    for snap in snapshots:
        snap["size_text"] = _format_size(snap["size"])
        snap["db_text"] = ", ".join("%s (%s)" % (name, _format_size(size)) for name, size in snap["databases"].items())
    return render_title_template("db_backups.html", title=_("Database Backups"), page="db_backups",
                                 snapshots=snapshots, backup_root=backup_root,
                                 backup_dir_setting=_configured_backup_dir(),
                                 backup_dir_env=os.environ.get("DB_BACKUP_DIR", ""),
                                 mirror_dir=get_mirror_dir(),
                                 retention_days=get_retention_days(),
                                 restorable_dbs=RESTORABLE_DBS,
                                 restore_running=restore_in_progress())


@admi.route("/admin/db_backups/settings", methods=["POST"])
@user_login_required
@admin_required
def db_backups_settings():
    from .tasks.db_backup import get_backup_root  # noqa: F401 (puts scripts/ on sys.path)
    from cwa_db import CWA_DB
    from .tasks.processed_cleanup import normalize_retention_days
    backup_dir = (request.form.get("db_backup_dir") or "").strip()
    if backup_dir and not os.path.isabs(backup_dir):
        flash(_("The backup folder must be an absolute path."), category="error")
        return redirect(url_for("admin.db_backups"))
    mirror_dir = (request.form.get("library_mirror_dir") or "").strip()
    if mirror_dir:
        from library_mirror import validate_destination, MirrorError
        try:
            validate_destination(config.config_calibre_dir, mirror_dir)
        except MirrorError as e:
            flash(str(e), category="error")
            return redirect(url_for("admin.db_backups"))
    raw_days = (request.form.get("processed_books_retention_days") or "").strip()
    days = normalize_retention_days(raw_days, default=-1)
    if days < 0 or days > 3650:
        flash(_("Retention must be a whole number of days between 0 and 3650."), category="error")
        return redirect(url_for("admin.db_backups"))
    try:
        with CWA_DB() as cwa_db:
            cwa_db.update_cwa_settings({"db_backup_dir": backup_dir, "library_mirror_dir": mirror_dir,
                                        "processed_books_retention_days": str(days)})
    except Exception as e:
        log.error("Saving backup settings failed: %s", e)
        flash(_("Saving backup settings failed: %(err)s", err=str(e)), category="error")
        return redirect(url_for("admin.db_backups"))
    flash(_("Backup settings saved."), category="success")
    return redirect(url_for("admin.db_backups"))


@admi.route("/admin/db_backups/mirror", methods=["POST"])
@user_login_required
@admin_required
def mirror_library_now():
    """Queues a library mirror run immediately."""
    from .tasks.library_mirror import TaskMirrorLibrary, get_mirror_dir
    if not get_mirror_dir():
        flash(_("Set a library mirror folder first."), category="error")
    else:
        WorkerThread.add(current_user.name, TaskMirrorLibrary())
        flash(Markup(_("Library mirror started. Follow its progress on the %(link)s page.",
                       link=_tasks_page_link())), category="success")
    return redirect(url_for("admin.db_backups"))


@admi.route("/admin/db_backups/download/<name>", methods=["GET"])
@user_login_required
@admin_required
def download_db_snapshot(name):
    """Streams one snapshot's database files as a zip, for keeping an off-box copy."""
    import io
    import zipfile
    from .tasks.db_backup import get_backup_root
    from db_backup import resolve_snapshot, _snapshot_db_files
    try:
        snap_dir = resolve_snapshot(get_backup_root(), name)
    except (ValueError, FileNotFoundError):
        abort(404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for db_file in _snapshot_db_files(snap_dir):
            zf.write(os.path.join(snap_dir, db_file), arcname=db_file)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name="lily-db-%s.zip" % name)


@admi.route("/admin/db_backups/restore", methods=["POST"])
@user_login_required
@admin_required
def restore_db_snapshot():
    """Queue a restore of the selected databases from one snapshot."""
    from .tasks.db_backup import get_backup_root, RESTORABLE_DBS, TaskRestoreDatabaseSnapshot
    from db_backup import resolve_snapshot
    name = request.form.get("snapshot", "")
    databases = [d for d in request.form.getlist("databases") if d in RESTORABLE_DBS]
    wants_json = request.accept_mimetypes.best == "application/json"

    def _reply(ok, message, status=200):
        if wants_json:
            return jsonify({"success": ok, "message": str(message),
                            "tasks_url": url_for("tasks.get_tasks_status")}), status
        flash(message, category="success" if ok else "error")
        return redirect(url_for("admin.db_backups"))

    try:
        resolve_snapshot(get_backup_root(), name)
    except (ValueError, FileNotFoundError):
        return _reply(False, _("Snapshot not found."), 404)
    if not databases:
        return _reply(False, _("Select at least one database to restore."), 400)
    if not mark_restore_queued():
        return _reply(False, _("Restore already in progress."), 409)

    WorkerThread.add(current_user.name, TaskRestoreDatabaseSnapshot(name, databases))
    return _reply(True, Markup(_("Restore of %(dbs)s from %(name)s started. A safety copy of the current "
                                 "databases is taken first. Follow its progress on the %(link)s page.",
                                 dbs=", ".join(databases), name=name, link=_tasks_page_link())))
