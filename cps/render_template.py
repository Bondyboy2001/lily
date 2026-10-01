# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""render_title_template: wraps Flask templates with the sidebar, notifications and per-user settings."""

from flask import render_template, g, abort, request, flash
from flask import after_this_request, has_app_context, has_request_context
from flask_babel import gettext as _
from werkzeug.local import LocalProxy
from .cw_login import current_user
from sqlalchemy.sql.expression import or_
from sqlalchemy import func

from . import config, constants, logger, ub
from .ub import User

# CWA specific imports
from datetime import datetime
import os.path

import sys
sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from cwa_db import CWA_DB


log = logger.create()


def get_request_cwa_db():
    """Returns one CWA_DB per request (stored on flask.g) instead of opening a new
    connection for every notification check. Outside a request/app context a fresh
    instance is returned and the caller owns it."""
    if not has_app_context():
        return CWA_DB()
    db = g.get('_lily_cwa_db')
    if db is None:
        db = CWA_DB()
        g._lily_cwa_db = db
        if has_request_context():
            @after_this_request
            def _close_cwa_db(response):
                close_request_cwa_db()
                return response
    return db


def close_request_cwa_db(exc=None):
    """Closes the per-request CWA_DB, if any. Safe to register with app.teardown_appcontext."""
    if not has_app_context():
        return
    db = g.pop('_lily_cwa_db', None)
    if db is not None:
        db.close()


def _duplicate_setup_notice_dismissed():
    notice_file = f"/config/cwa_duplicate_index_setup_notice_{getattr(current_user, 'id', 'unknown')}"
    return os.path.isfile(notice_file)


def duplicate_index_setup_notification(settings, cwa_db=None, cache_data=None, cache_data_loaded=False):
    notice_file = f"/config/cwa_duplicate_index_setup_notice_{getattr(current_user, 'id', 'unknown')}"
    if os.path.isfile(notice_file):
        return False

    try:
        from cps.duplicate_index import duplicate_index_needs_manual_full_scan, library_has_books

        if not library_has_books():
            return False
        # Reuse the caller's DB connection and already-parsed cache when available.
        needs_scan_kwargs = {"cwa_db": cwa_db}
        if cache_data_loaded:
            needs_scan_kwargs["cache_data"] = cache_data
        if not duplicate_index_needs_manual_full_scan(settings, **needs_scan_kwargs):
            return False
    except Exception as e:
        log.debug("[cwa-duplicates] Failed to check duplicate setup notification state: %s", str(e))
        return False

    try:
        message = _(
            "Duplicate scanning needs a one-time full scan before fast duplicate checks can run after imports "
            "and metadata changes. Open the Duplicates page to start it. "
        )
        flash(message, category="duplicate_scan_setup")
        return True
    except Exception as e:
        log.debug("[cwa-duplicates] Failed to show duplicate index setup notification: %s", str(e))
        return False


def get_sidebar_config(kwargs=None):
    kwargs = kwargs or []
    simple = bool([e for e in ['kindle', 'tolino', "kobo", "bookeen"]
                   if (e in request.headers.get('User-Agent', "").lower())])
    if 'content' in kwargs:
        content = kwargs['content']
        content = isinstance(content, (User, LocalProxy)) and not content.role_anonymous()
    else:
        content = 'conf' in kwargs
    sidebar = list()
    sidebar.append({"glyph": "glyphicon-book", "text": _('Books'), "link": 'web.index', "id": "new",
                    "visibility": constants.SIDEBAR_RECENT, 'public': True, "page": "root",
                    "show_text": _('Show recent books'), "config_show":False})
    sidebar.append({"glyph": "glyphicon-fire", "text": _('Hot Books'), "link": 'web.books_list', "id": "hot",
                    "visibility": constants.SIDEBAR_HOT, 'public': True, "page": "hot",
                    "show_text": _('Show Hot Books'), "config_show": True})
    if current_user.role_admin():
        sidebar.append({"glyph": "glyphicon-download", "text": _('Downloaded Books'), "link": 'web.download_list',
                        "id": "download", "visibility": constants.SIDEBAR_DOWNLOAD, 'public': (not current_user.is_anonymous),
                        "page": "download", "show_text": _('Show Downloaded Books'),
                        "config_show": content})
    else:
        sidebar.append({"glyph": "glyphicon-download", "text": _('Downloaded Books'), "link": 'web.books_list',
                        "id": "download", "visibility": constants.SIDEBAR_DOWNLOAD, 'public': (not current_user.is_anonymous),
                        "page": "download", "show_text": _('Show Downloaded Books'),
                        "config_show": content})
    sidebar.append(
        {"glyph": "glyphicon-star", "text": _('Top Rated Books'), "link": 'web.books_list', "id": "rated",
         "visibility": constants.SIDEBAR_BEST_RATED, 'public': True, "page": "rated",
         "show_text": _('Show Top Rated Books'), "config_show": True})
    # Reading and Finished always show for signed-in users (SIDEBAR_RECENT is the always-visible flag).
    sidebar.append({"glyph": "glyphicon-education", "text": _('Reading'), "link": 'web.books_list', "id": "inprogress",
                    "visibility": constants.SIDEBAR_RECENT, 'public': (not current_user.is_anonymous),
                    "page": "inprogress", "show_text": _('Show Reading'), "config_show": False})
    sidebar.append({"glyph": "glyphicon-ok-circle", "text": _('Finished'), "link": 'web.books_list', "id": "read",
                    "visibility": constants.SIDEBAR_RECENT, 'public': (not current_user.is_anonymous),
                    "page": "read", "show_text": _('Show Read and Unread'), "config_show": content})
    sidebar.append(
        {"glyph": "glyphicon-eye-close", "text": _('Unread Books'), "link": 'web.books_list', "id": "unread",
         "visibility": constants.SIDEBAR_READ_AND_UNREAD, 'public': (not current_user.is_anonymous), "page": "unread",
         "show_text": _('Show unread'), "config_show": False})
    sidebar.append({"glyph": "glyphicon-inbox", "text": _('Categories'), "link": 'web.category_list', "id": "cat",
                    "visibility": constants.SIDEBAR_CATEGORY, 'public': True, "page": "category",
                    "show_text": _('Show Category Section'), "config_show": True})
    sidebar.append({"glyph": "glyphicon-bookmark", "text": _('Series'), "link": 'web.series_list', "id": "serie",
                    "visibility": constants.SIDEBAR_SERIES, 'public': True, "page": "series",
                    "show_text": _('Show Series Section'), "config_show": True})
    sidebar.append({"glyph": "glyphicon-user", "text": _('Authors'), "link": 'web.author_list', "id": "author",
                    "visibility": constants.SIDEBAR_AUTHOR, 'public': True, "page": "author",
                    "show_text": _('Show Author Section'), "config_show": True})
    sidebar.append(
        {"glyph": "glyphicon-text-size", "text": _('Publishers'), "link": 'web.publisher_list', "id": "publisher",
         "visibility": constants.SIDEBAR_PUBLISHER, 'public': True, "page": "publisher",
         "show_text": _('Show Publisher Section'), "config_show":True})
    sidebar.append({"glyph": "glyphicon-flag", "text": _('Languages'), "link": 'web.language_overview', "id": "lang",
                    "visibility": constants.SIDEBAR_LANGUAGE, 'public': (current_user.filter_language() == 'all'),
                    "page": "language",
                    "show_text": _('Show Language Section'), "config_show": True})
    sidebar.append({"glyph": "glyphicon-star-empty", "text": _('Ratings'), "link": 'web.ratings_list', "id": "rate",
                    "visibility": constants.SIDEBAR_RATING, 'public': True,
                    "page": "rating", "show_text": _('Show Ratings Section'), "config_show": True})
    sidebar.append({"glyph": "glyphicon-file", "text": _('File formats'), "link": 'web.formats_list', "id": "format",
                    "visibility": constants.SIDEBAR_FORMAT, 'public': True,
                    "page": "format", "show_text": _('Show File Formats Section'), "config_show": True})
    sidebar.append(
        {"glyph": "glyphicon-trash", "text": _('Archived Books'), "link": 'web.books_list', "id": "archived",
         "visibility": constants.SIDEBAR_ARCHIVED, 'public': (not current_user.is_anonymous), "page": "archived",
         "show_text": _('Show Archived Books'), "config_show": content})
    if not simple:
        sidebar.append(
            {"glyph": "glyphicon-th-list", "text": _('Books List'), "link": 'web.books_table', "id": "list",
             "visibility": constants.SIDEBAR_LIST, 'public': (not current_user.is_anonymous), "page": "list",
             "show_text": _('Show Books List'), "config_show": content})
    g.shelves_access = ub.session.query(ub.Shelf).filter(
        or_(ub.Shelf.is_public == 1, ub.Shelf.user_id == current_user.id)).order_by(ub.Shelf.name).all()
    g.shelf_book_counts = shelf_book_counts([shelf.id for shelf in g.shelves_access])

    return sidebar, simple


def shelf_book_counts(shelf_ids):
    """Book count per shelf id in one grouped query (the sidebar used to run one count per shelf)."""
    if not shelf_ids:
        return {}
    rows = (ub.session.query(ub.BookShelf.shelf, func.count(ub.BookShelf.id))
            .filter(ub.BookShelf.shelf.in_(shelf_ids))
            .group_by(ub.BookShelf.shelf).all())
    return {shelf_id: count for shelf_id, count in rows}

# Checks if an update for CWA is available, returning True if yes
def cwa_update_available() -> tuple[bool, str, str]:
    try:
        current_version = constants.INSTALLED_VERSION
        tag_name = constants.STABLE_VERSION

        def _normalize_version(value: str) -> str:
            return (value or "").lstrip("vV")

        current_normalized = _normalize_version(current_version)
        tag_normalized = _normalize_version(tag_name)

        if current_normalized in ("", "0.0.0") or tag_normalized in ("", "0.0.0"):
            return False, "0.0.0", "0.0.0"

        return (tag_normalized != current_normalized), current_version, tag_name
    except Exception as e:
        print(f"[cwa-update-notification-service] Error checking for CWA updates: {e}", flush=True)
        return False, "0.0.0", "0.0.0"

UPDATE_NOTICE_FILE = '/app/cwa_update_notice'
# Date the update notice was last handled in this process; saves a file read per render
_update_notice_done_date = None


# Displays a notification to admins that an update for Lily is available, no matter which page they're on
# Only displays once per calendar day
def cwa_update_notification() -> None:
    global _update_notice_done_date
    current_date = datetime.now().strftime("%Y-%m-%d")
    if _update_notice_done_date == current_date:
        return
    if not get_request_cwa_db().cwa_settings['cwa_update_notifications']:
        return
    try:
        with open(UPDATE_NOTICE_FILE, 'r') as f:
            last_notification = f.read().strip()
    except OSError:
        last_notification = ""
    if last_notification != current_date:
        update_available, current_version, tag_name = cwa_update_available()
        if update_available:
            message = _("Lily update available: %(current)s → %(newest)s. Rebuild or re-pull the image to update.",
                        current=current_version, newest=tag_name)
            flash(message, category="cwa_update")
            print(f"[cwa-update-notification-service] {message}", flush=True)
        with open(UPDATE_NOTICE_FILE, 'w') as f:
            f.write(current_date)
    _update_notice_done_date = current_date

# Returns the template for rendering and includes the instance name
def render_title_template(*args, **kwargs):
    sidebar, simple = get_sidebar_config(kwargs)
    if current_user.role_admin():
        try:
            cwa_update_notification()
        except Exception as e:
            print(f"[cwa-update-notification-service] The following error occurred when checking for available updates:\n{e}", flush=True)
    duplicate_notification = {
        "enabled": False,
        "count": 0,
        "preview": [],
        "cached": False,
        "stale": False,
    }
    try:
        if current_user.is_authenticated and (current_user.role_admin() or current_user.role_edit()):
            cwa_db = get_request_cwa_db()
            detection_enabled = cwa_db.cwa_settings.get('duplicate_detection_enabled', 1)
            notifications_enabled = bool(cwa_db.cwa_settings.get('duplicate_notifications_enabled', 1))
            if detection_enabled:
                cache_data = cwa_db.get_duplicate_cache()
                duplicate_setup_notice_dismissed = _duplicate_setup_notice_dismissed()
                duplicate_setup_notice_shown = False
                if not duplicate_setup_notice_dismissed:
                    duplicate_setup_notice_shown = duplicate_index_setup_notification(
                        cwa_db.cwa_settings,
                        cwa_db=cwa_db,
                        cache_data=cache_data,
                        cache_data_loaded=True,
                    )

                if duplicate_setup_notice_shown:
                    duplicate_notification = {
                        "enabled": notifications_enabled,
                        "count": 0,
                        "preview": [],
                        "cached": False,
                        "stale": True,
                    }
                elif cache_data and cache_data.get('duplicate_groups') is not None:
                    duplicate_groups = cache_data.get('duplicate_groups') or []
                    try:
                        dismissed_groups = ub.session.query(ub.DismissedDuplicateGroup.group_hash)\
                            .filter(ub.DismissedDuplicateGroup.user_id == current_user.id)\
                            .all()
                        dismissed_hashes = {row[0] for row in dismissed_groups}
                        if dismissed_hashes:
                            duplicate_groups = [
                                group for group in duplicate_groups
                                if group.get('group_hash') not in dismissed_hashes
                            ]
                    except Exception as e:
                        log.debug("Could not filter dismissed duplicate groups: %s", e)

                    preview = []
                    for group in duplicate_groups[:3]:
                        preview.append({
                            'title': group.get('title', ''),
                            'author': group.get('author', ''),
                            'count': group.get('count', 0),
                            'hash': group.get('group_hash', '')
                        })

                    duplicate_notification = {
                        "enabled": notifications_enabled,
                        "count": len(duplicate_groups),
                        "preview": preview,
                        "cached": True,
                        "stale": bool(cache_data.get('scan_pending')),
                    }
                else:
                    duplicate_notification = {
                        "enabled": notifications_enabled,
                        "count": 0,
                        "preview": [],
                        "cached": False,
                        "stale": True,
                    }
    except Exception as e:
        log.debug("[cwa-duplicates] Failed to build duplicate notification context: %s", str(e))
    try:
        return render_template(instance=config.config_calibre_web_title, sidebar=sidebar, simple=simple,
                       accept=config.config_upload_formats.split(','),
                       duplicate_notification=duplicate_notification,
                       *args, **kwargs)
    except PermissionError:
        log.error("No permission to access {} file.".format(args[0]))
        abort(403)
