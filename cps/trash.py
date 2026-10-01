# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Deleted books go to the Trash; the admin Trash page restores them or deletes them now.

``trash_book``/``trash_format`` are called by helper.delete_book_file (single, bulk and
merge deletes, and duplicate resolution all end up there). Files and rows are kept by
cps/trash_store.py; this module wires it to the live sessions, settings and routes.

Restore puts the folder back and re-inserts the saved rows (same book id when it is
still free, which it is unless metadata.db was replaced since). That keeps the uuid,
dates, custom columns and app.db links exactly as they were, needs no calibredb, and
runs in one metadata.db transaction: if anything fails the folder goes back to the Trash.
"""

import os
import shutil
import sys

from flask import Blueprint, abort, flash, redirect, request, url_for
from flask_babel import gettext as _
from sqlalchemy import text

from . import calibre_db, config, logger, ub
from . import trash_store as store
from . import library_orphans
from .admin import admin_required
from .render_template import render_title_template
from .usermanagement import user_login_required

log = logger.create()
trash = Blueprint("trash", __name__)

# metadata.db is ATTACHed as "calibre" on the calibre session (see db.CalibreDB.setup_db)
CALIBRE_SCHEMA = "calibre"


class SessionExecutor:
    """trash_store executor over a SQLAlchemy session."""

    def __init__(self, session):
        self.session = session

    def fetch(self, sql, params=None):
        return [dict(r) for r in self.session.execute(text(sql), params or {}).mappings().all()]

    def execute(self, sql, params=None):
        return self.session.execute(text(sql), params or {}).lastrowid


# ---------------------------------------------------------------------------- settings

def _config_dir():
    return os.environ.get("CWA_DB_PATH", "/config")


def _cwa_db():
    if '/app/calibre-web-automated/scripts/' not in sys.path:
        sys.path.insert(1, '/app/calibre-web-automated/scripts/')
    from cwa_db import CWA_DB
    return CWA_DB()


def normalize_days(value, default=store.DEFAULT_RETENTION_DAYS):
    """Days as a non-negative int (0 = keep forever); invalid values fall back to default."""
    if isinstance(value, list):  # get_cwa_settings() splits strings on commas
        value = ",".join(value)
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return days if days >= 0 else default


def get_retention_days():
    try:
        with _cwa_db() as cwa_db:
            return normalize_days(cwa_db.cwa_settings.get("trash_retention_days"))
    except Exception:
        return store.DEFAULT_RETENTION_DAYS


def books_dir():
    return config.get_book_path()


def get_trash_root():
    return store.trash_root(books_dir())


def _actor():
    try:
        from .cw_login import current_user
        if current_user and current_user.is_authenticated:
            return current_user.name
    except Exception:
        pass
    return "System"


# ---------------------------------------------------------------------------- trash

def trash_book(book, library_path, reason="delete"):
    """Saves the book's rows and moves its folder to the Trash. Raises on failure, in
    which case nothing was moved. Returns the entry id."""
    calibre_db.ensure_session()
    metadata = store.capture_book_rows(SessionExecutor(calibre_db.session), book.id, CALIBRE_SCHEMA)
    app_rows = store.capture_app_rows(SessionExecutor(ub.session), book.id)
    root = store.trash_root(library_path)
    os.makedirs(root, exist_ok=True)
    entry_id = store.new_entry_id(root, book.id)
    dest, manifest_path = store.entry_paths(root, entry_id)
    folder = os.path.join(library_path, book.path) if book.path and book.path.count('/') == 1 else None
    has_files = bool(folder and os.path.isdir(folder))
    store.write_manifest(manifest_path, store.new_manifest(
        "book", book.id, title=book.title, authors=[a.name for a in book.authors],
        formats=[d.format for d in book.data], path=book.path, reason=reason,
        trashed_by=_actor(), has_files=has_files, metadata=metadata, app=app_rows))
    if has_files:
        try:
            store.move_path(folder, dest)
        except OSError:
            os.remove(manifest_path)
            raise
        store.remove_empty_dir(os.path.dirname(folder))
    log.info("Book %s (%s) moved to the Trash as %s", book.id, book.title, entry_id)
    return entry_id


def undo_trash_book(library_path, book_id):
    """Puts the newest Trash entry of a book back when deleting its database rows failed
    right after its folder was moved. Returns True when something was moved back."""
    root = store.trash_root(library_path)
    candidates = []
    for entry in store.list_entries(root, retention_days=0):
        if entry["kind"] == "book" and entry["book_id"] == book_id:
            candidates.append(entry["id"])
    if not candidates:
        return False
    entry_id = candidates[0]  # list_entries is newest first
    folder, manifest_path = store.entry_paths(root, entry_id)
    try:
        book_path = store.read_manifest(root, entry_id)["path"]
        if os.path.isdir(folder):
            store.move_path(folder, os.path.join(library_path, book_path))
        os.remove(manifest_path)
        return True
    except (OSError, KeyError, ValueError) as e:
        log.error("Could not put book %s back from the Trash entry %s: %s", book_id, entry_id, e)
        return False


def trash_format(book, library_path, book_format, reason="delete"):
    """Moves one format's file(s) of a book to the Trash. Returns the entry id."""
    book_format = book_format.upper()
    folder = os.path.join(library_path, book.path)
    files = [f for f in os.listdir(folder) if f.upper().endswith("." + book_format)] if os.path.isdir(folder) else []
    calibre_db.ensure_session()
    rows = SessionExecutor(calibre_db.session).fetch(
        "SELECT * FROM calibre.data WHERE book = :id AND format = :fmt", {"id": book.id, "fmt": book_format})
    root = store.trash_root(library_path)
    os.makedirs(root, exist_ok=True)
    entry_id = store.new_entry_id(root, book.id, suffix=book_format)
    dest, manifest_path = store.entry_paths(root, entry_id)
    store.write_manifest(manifest_path, store.new_manifest(
        "format", book.id, title=book.title, authors=[a.name for a in book.authors], formats=[book_format],
        format=book_format, files=files, path=book.path, reason=reason, trashed_by=_actor(),
        data=[{k: v for k, v in r.items() if k not in ("id", "book")} for r in rows]))
    moved = []
    try:
        for name in files:
            store.move_path(os.path.join(folder, name), os.path.join(dest, name))
            moved.append(name)
    except OSError:
        for name in moved:
            store.move_path(os.path.join(dest, name), os.path.join(folder, name))
        store.remove_empty_dir(dest)
        os.remove(manifest_path)
        raise
    return entry_id


# ---------------------------------------------------------------------------- restore

def restore_entry(entry_id):
    """Puts a trashed book or format back. Returns (book id, message); raises
    store.TrashError with a readable reason when it can't."""
    root = get_trash_root()
    manifest = store.read_manifest(root, entry_id)
    kind = manifest.get("kind")
    if kind == "book":
        return _restore_book(root, entry_id, manifest)
    if kind == "format":
        return _restore_format(root, entry_id, manifest)
    if kind == "orphan":
        return _restore_orphan(root, entry_id, manifest)
    raise store.TrashError(_("Unknown Trash entry type."))


def _restore_book(root, entry_id, manifest):
    folder, manifest_path = store.entry_paths(root, entry_id)
    if manifest.get("has_files") and not os.path.isdir(folder):
        raise store.TrashError(_("The book's files are missing from the Trash."))
    calibre_db.ensure_session()
    session = calibre_db.session
    target = None
    try:
        new_id, path = store.restore_book_rows(SessionExecutor(session), manifest["metadata"], CALIBRE_SCHEMA)
        if os.path.isdir(folder):
            target = os.path.join(books_dir(), path)
            if os.path.exists(target):
                raise store.TrashError(_("A folder already exists at %(path)s.", path=path))
            store.move_path(folder, target)
        if new_id != manifest["book_id"]:
            calibre_db.set_metadata_dirty(new_id)  # its metadata.opf still names the old id
        session.commit()
    except Exception:
        session.rollback()
        if target and os.path.isdir(target) and not os.path.exists(folder):
            store.move_path(target, folder)
        raise
    try:
        store.restore_app_rows(SessionExecutor(ub.session), manifest.get("app", {}), new_id)
        ub.session.commit()
    except Exception as e:
        ub.session.rollback()
        log.error("Restored book %s but could not re-link its shelves and reading data: %s", new_id, e)
    os.remove(manifest_path)
    _after_library_change([new_id])
    log.info("Restored book %s from the Trash entry %s as book %s", manifest["book_id"], entry_id, new_id)
    return new_id, _("Restored %(title)s.", title=manifest.get("title", ""))


def _restore_format(root, entry_id, manifest):
    folder, manifest_path = store.entry_paths(root, entry_id)
    book = calibre_db.get_book(manifest["book_id"])
    if not book:
        raise store.TrashError(_("The book this format belonged to is no longer in the library."))
    fmt = manifest["format"]
    if any(d.format.upper() == fmt for d in book.data):
        raise store.TrashError(_("The book already has a %(format)s file.", format=fmt))
    book_folder = os.path.join(books_dir(), book.path)
    moved = []
    try:
        for name in manifest.get("files", []):
            store.move_path(os.path.join(folder, name), os.path.join(book_folder, name))
            moved.append(name)
        ex = SessionExecutor(calibre_db.session)
        cols = [c["name"] for c in ex.fetch("PRAGMA calibre.table_info(data)")]
        for row in manifest.get("data", []):
            store.insert_row(ex, CALIBRE_SCHEMA, "data", dict(row, book=book.id), cols, or_ignore=True)
        calibre_db.session.commit()
    except Exception:
        calibre_db.session.rollback()
        for name in moved:
            store.move_path(os.path.join(book_folder, name), os.path.join(folder, name))
        raise
    store.remove_empty_dir(folder)
    os.remove(manifest_path)
    _after_library_change([book.id])
    return book.id, _("Restored the %(format)s file of %(title)s.", format=fmt, title=book.title)


def _restore_orphan(root, entry_id, manifest):
    folder, manifest_path = store.entry_paths(root, entry_id)
    target = os.path.join(books_dir(), manifest["path"])
    if os.path.exists(target):
        raise store.TrashError(_("A folder already exists at %(path)s.", path=manifest["path"]))
    store.move_path(folder, target)
    os.remove(manifest_path)
    return None, _("Moved the folder back to %(path)s. It is still not in the library database.",
                   path=manifest["path"])


def _after_library_change(book_ids):
    try:
        from .editbooks import _queue_duplicate_scan_after_change
        _queue_duplicate_scan_after_change(book_ids)
    except Exception as e:
        log.debug("Could not queue a duplicate scan after a Trash restore: %s", e)


# ---------------------------------------------------------------------------- unreferenced folders

def reimport_folder(rel_path):
    """Copies a folder's ebook files into the ingest folder (so ingest adds them as new
    books) and moves the folder itself to the Trash. Returns the number of files queued."""
    from .cwa_functions.ingest import get_ingest_dir
    library = books_dir()
    folder = os.path.normpath(os.path.join(library, rel_path))
    if not folder.startswith(os.path.normpath(library) + os.sep) or not os.path.isdir(folder):
        raise store.TrashError(_("Folder not found: %(path)s", path=rel_path))
    ingest_dir = get_ingest_dir()
    files = library_orphans.book_files(folder)
    for name in files:
        stem, ext = os.path.splitext(name)
        final = os.path.join(ingest_dir, name)
        n = 1
        while os.path.exists(final):
            n += 1
            final = os.path.join(ingest_dir, "%s (%d)%s" % (stem, n, ext))
        tmp = final + ".uploading"  # the ingest watcher ignores these until renamed
        shutil.copy2(os.path.join(folder, name), tmp)
        os.replace(tmp, final)
    root = store.trash_root(library)
    os.makedirs(root, exist_ok=True)
    entry_id = store.new_entry_id(root, 0, suffix="orphan")
    dest, manifest_path = store.entry_paths(root, entry_id)
    store.write_manifest(manifest_path, store.new_manifest(
        "orphan", None, title=rel_path.split("/")[-1], authors=[rel_path.split("/")[0]],
        path=rel_path, reason="reimport", trashed_by=_actor(), has_files=True))
    store.move_path(folder, dest)
    store.remove_empty_dir(os.path.dirname(folder))
    return len(files)


# ---------------------------------------------------------------------------- routes

def _format_size(num_bytes):
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return ("%d %s" % (size, unit)) if unit == "B" else ("%.1f %s" % (size, unit))
        size /= 1024


@trash.route("/admin/trash", methods=["GET"])
@user_login_required
@admin_required
def trash_page():
    days = get_retention_days()
    entries = store.list_entries(get_trash_root(), retention_days=days)
    for entry in entries:
        entry["size_text"] = _format_size(entry["size"])
    report = library_orphans.load_report(_config_dir())
    return render_title_template("trash.html", title=_("Trash"), page="trash", entries=entries,
                                 retention_days=days, total_size=_format_size(sum(e["size"] for e in entries)),
                                 orphans=report.get("folders", []), orphan_source=report.get("source", ""))


@trash.route("/admin/trash/restore/<entry_id>", methods=["POST"])
@user_login_required
@admin_required
def trash_restore(entry_id):
    if not store.valid_entry_id(entry_id):
        abort(404)
    try:
        book_id, message = restore_entry(entry_id)
    except FileNotFoundError:
        abort(404)
    except store.TrashError as e:
        flash(str(e), category="error")
    except Exception as e:
        log.error_or_exception("Restoring Trash entry %s failed: %s" % (entry_id, e))
        flash(_("Restore failed: %(err)s", err=str(e)), category="error")
    else:
        flash(message, category="success")
    return redirect(url_for("trash.trash_page"))


@trash.route("/admin/trash/delete/<entry_id>", methods=["POST"])
@user_login_required
@admin_required
def trash_delete(entry_id):
    if not store.valid_entry_id(entry_id):
        abort(404)
    try:
        store.delete_entry(get_trash_root(), entry_id)
        flash(_("Deleted for good."), category="success")
    except OSError as e:
        log.error("Deleting Trash entry %s failed: %s", entry_id, e)
        flash(_("Could not delete it: %(err)s", err=str(e)), category="error")
    return redirect(url_for("trash.trash_page"))


@trash.route("/admin/trash/empty", methods=["POST"])
@user_login_required
@admin_required
def trash_empty():
    root = get_trash_root()
    failed = 0
    entries = store.list_entries(root)
    for entry in entries:
        try:
            store.delete_entry(root, entry["id"])
        except OSError as e:
            failed += 1
            log.error("Deleting Trash entry %s failed: %s", entry["id"], e)
    if failed:
        flash(_("%(n)d item(s) could not be deleted; see the server log.", n=failed), category="error")
    else:
        flash(_("The Trash is empty."), category="success")
    return redirect(url_for("trash.trash_page"))


@trash.route("/admin/trash/settings", methods=["POST"])
@user_login_required
@admin_required
def trash_settings():
    days = normalize_days(request.form.get("trash_retention_days", ""), default=-1)
    if days < 0 or days > 3650:
        flash(_("Retention must be a whole number of days between 0 and 3650."), category="error")
        return redirect(url_for("trash.trash_page"))
    try:
        with _cwa_db() as cwa_db:
            cwa_db.update_cwa_settings({"trash_retention_days": str(days)})
    except Exception as e:
        log.error("Saving Trash settings failed: %s", e)
        flash(_("Saving failed: %(err)s", err=str(e)), category="error")
        return redirect(url_for("trash.trash_page"))
    flash(_("Trash settings saved."), category="success")
    return redirect(url_for("trash.trash_page"))


@trash.route("/admin/trash/unreferenced", methods=["POST"])
@user_login_required
@admin_required
def trash_unreferenced():
    """Acts on the folders listed after a metadata.db restore: re-import or dismiss."""
    config_dir = _config_dir()
    report = library_orphans.load_report(config_dir)
    listed = report.get("folders", [])
    action = request.form.get("action")
    if action == "dismiss":
        library_orphans.clear_report(config_dir)
        return redirect(url_for("trash.trash_page"))
    if action != "reimport":
        abort(400)
    chosen = [p for p in request.form.getlist("folders") if p in listed] or listed
    done, queued = [], 0
    for rel in chosen:
        try:
            queued += reimport_folder(rel)
            done.append(rel)
        except (OSError, store.TrashError) as e:
            log.error("Re-importing %s failed: %s", rel, e)
            flash(_("Could not re-import %(path)s: %(err)s", path=rel, err=str(e)), category="error")
    library_orphans.save_report(config_dir, [p for p in listed if p not in done], report.get("source", ""))
    if done:
        flash(_("Sent %(files)d file(s) from %(n)d folder(s) to the import folder; the folders are in the Trash.",
                files=queued, n=len(done)), category="success")
    return redirect(url_for("trash.trash_page"))
