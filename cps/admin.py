# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Admin pages: the user list and user editor, plus the admin-only maintenance endpoints."""

import os
import json
from functools import wraps

from flask import Blueprint, flash, redirect, url_for, abort, request, g, jsonify
from .cw_login import current_user
from flask_babel import gettext as _
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.exc import IntegrityError, OperationalError

from . import constants, logger, helper, cli_param
from . import calibre_db, ub, web_server, config
from werkzeug.security import generate_password_hash
from .helper import check_email, valid_email, check_username
from .render_template import render_title_template, get_sidebar_config
from .usermanagement import user_login_required
from .cw_babel import get_available_locale

log = logger.create()

admi = Blueprint('admin', __name__)


def admin_required(f):
    """
    Checks if current_user.role == 1
    """

    @wraps(f)
    def inner(*args, **kwargs):
        if current_user.role_admin():
            return f(*args, **kwargs)
        abort(403)

    return inner


@admi.before_app_request
def before_request():
    # Safety net: if not configured but metadata.db now exists at default location, auto-set without redirect loop
    if not config.db_configured:
        try:
            default_metadata = '/calibre-library/metadata.db'
            if (not config.config_calibre_dir or not os.path.isfile(os.path.join(config.config_calibre_dir, 'metadata.db'))) \
                    and os.path.isfile(default_metadata):
                config.config_calibre_dir = os.path.dirname(default_metadata)
                log.info('[autoconfig] Late-detected calibre library at %s; updating config and rebuilding db session', config.config_calibre_dir)
                try:
                    config.save()
                except Exception as e:
                    log.error('Failed to save late autoconfig: %s', e)
                # Re-run calibre db setup so subsequent handlers see a configured DB
                from . import db as _db, cli_param as _cli_param
                _db.CalibreDB.update_config(config)
                _db.CalibreDB.setup_db(config.config_calibre_dir, _cli_param.settings_path)
        except Exception as e:
            log.error('Autoconfig safety net error: %s', e)
    # If config says DB is configured but session is unavailable, try to recover and force reconfig on failure
    if config.db_configured:
        try:
            calibre_db.ensure_session()
        except Exception as e:
            log.error("ensure_session failed in before_request: %s", e)
        if calibre_db.session is None:
            log.error("Calibre DB session unavailable")
            config.db_configured = False
    g.constants = constants
    g.google_site_verification = os.getenv('GOOGLE_SITE_VERIFICATION', '')
    g.allow_anonymous = config.config_anonbrowse
    g.allow_upload = config.config_uploading
    g.config_authors_max = config.config_authors_max
    # The library lives at /calibre-library (autoconfigured above); there is no page to point
    # Lily elsewhere, so without a usable metadata.db every page but login says so.
    if '/static/' not in request.path and not config.db_configured and \
            request.endpoint not in ('web.login', 'web.login_post', 'web.logout'):
        abort(503, description=_("No Calibre library found at /calibre-library. Mount a library "
                                 "folder containing metadata.db there and restart Lily."))


@admi.route("/shutdown", methods=["POST"])
@user_login_required
@admin_required
def shutdown():
    task = request.get_json().get('parameter', -1)
    show_text = {}
    if task in (0, 1):  # valid commandos received
        # close all database connections
        calibre_db.dispose()
        ub.dispose()

        if task == 0:
            show_text['text'] = _('Server restarted, please reload page.')
        else:
            show_text['text'] = _('Performing Server shutdown, please close window.')
        # stop gevent/tornado server
        web_server.stop(task == 0)
        return json.dumps(show_text)

    if task == 2:
        log.warning("reconnecting to calibre database")
        calibre_db.reconnect_db(config, ub.app_DB_path)
        show_text['text'] = _('Database reconnected')
        return json.dumps(show_text)

    show_text['text'] = _('Unknown command')
    return json.dumps(show_text), 400


# method is available without login and not protected by CSRF to make it easy reachable, is per default switched off
# needed for docker applications, as changes on metadata.db from host are not visible to application
@admi.route("/reconnect", methods=['GET'])
def reconnect():
    if cli_param.reconnect_enable:
        calibre_db.reconnect_db(config, ub.app_DB_path)
        return json.dumps({})
    else:
        log.debug("'/reconnect' was accessed but is not enabled")
        abort(404)


@admi.route("/ajax/updateThumbnails", methods=['POST'])
@user_login_required
@admin_required
def update_thumbnails():
    # Always allow manual thumbnail cache updates
    log.info("Update of Cover cache requested")

    try:
        from .tasks.thumbnail import TaskGenerateCoverThumbnails
        task_id = helper.update_thumbnail_cache()

        # Check if there are any books to process
        books_with_covers = TaskGenerateCoverThumbnails.get_books_with_covers()
        book_count = len(books_with_covers)

        if book_count > 0:
            message = _('Thumbnail cache refresh started for {} book(s). This may take a few minutes.').format(book_count)
        else:
            message = _('No books with covers found to process.')

        return jsonify({
            'success': True,
            'message': message,
            'book_count': book_count,
            'task_id': str(task_id) if task_id else None
        })
    except Exception as e:
        log.error(f"Error starting thumbnail refresh: {e}")
        return jsonify({
            'success': False,
            'message': _('Failed to start thumbnail refresh: {}').format(str(e))
        })


@admi.route("/admin/usertable")
@user_login_required
@admin_required
def edit_user_table():
    all_user = ub.session.query(ub.User)
    if not config.config_anonbrowse:
        all_user = all_user.filter(ub.User.role.op('&')(constants.ROLE_ANONYMOUS) != constants.ROLE_ANONYMOUS)
    return render_title_template("user_table.html", users=all_user.order_by(ub.User.name).all(),
                                 title=_("Users"), page="usertable")


@admi.route("/ajax/loaddialogtexts/<element_id>", methods=['POST'])
@user_login_required
def load_dialogtexts(element_id):
    # The confirm dialog's title, one sentence saying what happens, and a button that names the action.
    texts = {"header": "", "main": "", "button": "", "valid": 1}
    if element_id == "btndeluser":
        texts["header"] = _('Delete User?')
        texts["main"] = _('Their account, shelves and reading progress are removed. Their books stay in the library.')
        texts["button"] = _('Delete user')
    elif element_id == "delete_shelf":
        texts["header"] = _('Delete Shelf?')
        texts["main"] = _('Its books stay in your library.')
        texts["button"] = _('Delete shelf')
    return json.dumps(texts)


@admi.route("/admin/user/new", methods=["GET", "POST"])
@user_login_required
@admin_required
def new_user():
    content = ub.User()
    languages = calibre_db.speaking_language()
    translations = get_available_locale()
    if request.method == "POST":
        to_save = request.form.to_dict()
        _handle_new_user(to_save, content, languages, translations)
    else:
        content.role = config.config_default_role
        content.sidebar_view = config.config_default_show
        content.locale = config.config_default_locale
        content.default_language = config.config_default_language
    opds_context = _build_opds_context(content)
    return render_title_template("user_edit.html", new_user=1, content=content,
                                 config=config, translations=translations,
                                 languages=languages, title=_("Add New User"), page="newuser",
                                 opds_root_order_string=opds_context["opds_root_order_string"],
                                 opds_hidden_entries_string=opds_context["opds_hidden_entries_string"],
                                 opds_root_labels=opds_context["opds_root_labels"])


def _build_opds_context(user):
    from .opds import (
        get_opds_root_order_for_user,
        get_opds_hidden_entries_for_user,
        OPDS_ROOT_ENTRY_DEFS,
        OPDS_ROOT_ORDER_DEFAULT,
    )
    opds_root_order = get_opds_root_order_for_user(user)
    opds_root_order_string = ",".join(opds_root_order)
    opds_hidden_entries = list(get_opds_hidden_entries_for_user(user))
    opds_hidden_entries_string = ",".join(opds_hidden_entries)
    opds_root_labels = [
        {
            "key": key,
            "label": _(OPDS_ROOT_ENTRY_DEFS[key]['title']),
        }
        for key in OPDS_ROOT_ORDER_DEFAULT
        if key in OPDS_ROOT_ENTRY_DEFS
    ]
    return {
        "opds_root_order_string": opds_root_order_string,
        "opds_hidden_entries_string": opds_hidden_entries_string,
        "opds_root_labels": opds_root_labels,
    }


@admi.route("/admin/user/<int:user_id>", methods=["GET", "POST"])
@user_login_required
@admin_required
def edit_user(user_id):
    content = ub.session.query(ub.User).filter(ub.User.id == int(user_id)).first()  # type: ub.User
    if not content or (not config.config_anonbrowse and content.name == "Guest"):
        flash(_("User not found"), category="error")
        return redirect(url_for('admin.edit_user_table'))
    languages = calibre_db.speaking_language(return_all_languages=True)
    translations = get_available_locale()

    if request.method == "POST":
        to_save = request.form.to_dict()
        resp = _handle_edit_user(to_save, content, languages, translations)
        if resp:
            return resp
    opds_context = _build_opds_context(content)
    return render_title_template("user_edit.html",
                                 translations=translations,
                                 languages=languages,
                                 new_user=0,
                                 content=content,
                                 config=config,
                                 opds_root_order_string=opds_context["opds_root_order_string"],
                                 opds_hidden_entries_string=opds_context["opds_hidden_entries_string"],
                                 opds_root_labels=opds_context["opds_root_labels"],
                                 title=_("Edit User %(nick)s", nick=content.name),
                                 page="edituser")


def _handle_new_user(to_save, content, languages, translations):
    content.default_language = to_save.get("default_language", config.config_default_language)
    content.locale = to_save.get("locale", content.locale)

    shown = [int(key[5:]) for key in to_save if key.startswith('show_')]
    content.sidebar_view = sum(shown) if shown else config.config_default_show

    content.role = constants.selected_roles(to_save)
    try:
        if not to_save["name"] or not to_save["email"] or not to_save["password"]:
            log.info("Missing entries on new user")
            raise Exception(_("Fill in the name, email and password to add a user."))
        content.password = generate_password_hash(helper.valid_password(to_save.get("password", "")))
        content.email = check_email(to_save["email"])
        # Query username, if not existing, change
        content.name = check_username(to_save["name"])
    except Exception as ex:
        flash(str(ex), category="error")
        opds_context = _build_opds_context(content)
        return render_title_template("user_edit.html", new_user=1, content=content,
                                     config=config,
                                     translations=translations,
                                     languages=languages, title=_("Add New User"), page="newuser",
                                     opds_root_order_string=opds_context["opds_root_order_string"],
                                     opds_hidden_entries_string=opds_context["opds_hidden_entries_string"],
                                     opds_root_labels=opds_context["opds_root_labels"])
    try:
        content.allowed_tags = config.config_allowed_tags
        content.denied_tags = config.config_denied_tags
        content.allowed_column_value = config.config_allowed_column_value
        content.denied_column_value = config.config_denied_column_value
        ub.session.add(content)
        ub.session.commit()
        flash(_("User '%(user)s' created", user=content.name), category="success")
        log.debug("User {} created".format(content.name))
        return redirect(url_for('admin.edit_user_table'))
    except IntegrityError:
        ub.session.rollback()
        log.error("Found an existing account for {} or {}".format(content.name, content.email))
        flash(_("A user with that name or email already exists. Choose a different one."), category="error")
    except OperationalError as e:
        ub.session.rollback()
        log.error_or_exception("Settings Database error: {}".format(e))
        flash(_("Couldn't add the user. Try again; if it keeps failing, check Logs in Settings."),
              category="error")


def _delete_user(content):
    if ub.session.query(ub.User).filter(ub.User.role.op('&')(constants.ROLE_ADMIN) == constants.ROLE_ADMIN,
                                        ub.User.id != content.id).count():
        if content.name != "Guest":
            # Delete all books in shelfs belonging to user, all shelfs of user, downloadstat of user, read status
            # and user itself
            ub.session.query(ub.ReadBook).filter(content.id == ub.ReadBook.user_id).delete()
            ub.session.query(ub.Downloads).filter(content.id == ub.Downloads.user_id).delete()
            for us in ub.session.query(ub.Shelf).filter(content.id == ub.Shelf.user_id):
                ub.session.query(ub.BookShelf).filter(us.id == ub.BookShelf.shelf).delete()
            ub.session.query(ub.Shelf).filter(content.id == ub.Shelf.user_id).delete()
            ub.session.query(ub.Bookmark).filter(content.id == ub.Bookmark.user_id).delete()
            ub.session.query(ub.User).filter(ub.User.id == content.id).delete()
            ub.session.query(ub.User_Sessions).filter(ub.User_Sessions.user_id == content.id).delete()
            ub.session_commit()
            log.info("User {} deleted".format(content.name))
            return _("User '%(nick)s' deleted", nick=content.name)
        else:
            raise Exception(_("Can't delete Guest User"))
    else:
        raise Exception(_("No admin user remaining, can't delete user"))


def _handle_edit_user(to_save, content, languages, translations):
    if to_save.get("delete"):
        try:
            flash(_delete_user(content), category="success")
        except Exception as ex:
            log.error(ex)
            flash(str(ex), category="error")
        return redirect(url_for('admin.edit_user_table'))
    if not ub.session.query(ub.User).filter(ub.User.role.op('&')(constants.ROLE_ADMIN) == constants.ROLE_ADMIN,
                                            ub.User.id != content.id).count() and 'admin_role' not in to_save:
        log.warning("No admin user remaining, can't remove admin role from {}".format(content.name))
        flash(_("No admin user remaining, can't remove admin role"), category="error")
        return redirect(url_for('admin.edit_user_table'))

    # The user form no longer shows sidebar or OPDS options, so those keep their values.
    val = [int(k[5:]) for k in to_save if k.startswith('show_')]
    if val:
        sidebar, __ = get_sidebar_config()
        for element in sidebar:
            value = element['visibility']
            if value in val and not content.check_visibility(value):
                content.sidebar_view |= value
            elif value not in val and content.check_visibility(value):
                content.sidebar_view &= ~value

    # OPDS root order
    opds_order_raw = to_save.get("opds_root_order", "").strip()
    if "opds_root_order" not in to_save:
        pass
    elif opds_order_raw:
        from .opds import normalize_opds_root_order
        opds_order_list = [item.strip() for item in opds_order_raw.split(',') if item.strip()]
        normalized_order = normalize_opds_root_order(opds_order_list)
        if content.view_settings is None:
            content.view_settings = {}
        content.view_settings.setdefault('opds', {})['root_order'] = normalized_order
        flag_modified(content, "view_settings")
    else:
        if content.view_settings and content.view_settings.get('opds', {}).get('root_order'):
            content.view_settings['opds'].pop('root_order', None)
            if not content.view_settings['opds']:
                content.view_settings.pop('opds', None)
            flag_modified(content, "view_settings")

    # OPDS hidden entries
    opds_hidden_raw = to_save.get("opds_hidden_entries", "").strip()
    if "opds_hidden_entries" not in to_save:
        pass
    elif opds_hidden_raw:
        from .opds import OPDS_ROOT_ENTRY_DEFS
        hidden_entries = [item.strip() for item in opds_hidden_raw.split(',') if item.strip()]
        hidden_entries = [key for key in hidden_entries if key in OPDS_ROOT_ENTRY_DEFS]
        if content.view_settings is None:
            content.view_settings = {}
        content.view_settings.setdefault('opds', {})['hidden_entries'] = hidden_entries
        flag_modified(content, "view_settings")
    else:
        if content.view_settings and content.view_settings.get('opds', {}).get('hidden_entries'):
            content.view_settings['opds'].pop('hidden_entries', None)
            if not content.view_settings['opds']:
                content.view_settings.pop('opds', None)
            flag_modified(content, "view_settings")

    if to_save.get("default_language"):
        content.default_language = to_save["default_language"]
    if to_save.get("locale"):
        content.locale = to_save["locale"]
    try:
        anonymous = content.is_anonymous
        content.role = constants.selected_roles(to_save)
        if anonymous:
            content.role |= constants.ROLE_ANONYMOUS
        else:
            content.role &= ~constants.ROLE_ANONYMOUS
            if to_save.get("password", ""):
                content.password = generate_password_hash(helper.valid_password(to_save.get("password", "")))

        new_email = valid_email(to_save.get("email", content.email))
        if not new_email:
            raise Exception(_("Email can't be empty and has to be a valid Email"))
        if new_email != content.email:
            content.email = check_email(new_email)
        # Query username, if not existing, change
        if to_save.get("name", content.name) != content.name:
            if to_save.get("name") == "Guest":
                raise Exception(_("Guest Name can't be changed"))
            content.name = check_username(to_save["name"])

    except Exception as ex:
        log.error(ex)
        flash(str(ex), category="error")
        opds_context = _build_opds_context(content)
        return render_title_template("user_edit.html",
                                     translations=translations,
                                     languages=languages,
                                     new_user=0,
                                     content=content,
                                     config=config,
                                     opds_root_order_string=opds_context["opds_root_order_string"],
                                     opds_hidden_entries_string=opds_context["opds_hidden_entries_string"],
                                     opds_root_labels=opds_context["opds_root_labels"],
                                     title=_("Edit User %(nick)s", nick=content.name),
                                     page="edituser")
    try:
        ub.session_commit()
        flash(_("User '%(nick)s' updated", nick=content.name), category="success")
    except IntegrityError as ex:
        ub.session.rollback()
        log.error("An unknown error occurred while changing user: {}".format(str(ex)))
        flash(_("Couldn't save the user. Try again; if it keeps failing, check Logs in Settings."),
              category="error")
    except OperationalError as e:
        ub.session.rollback()
        log.error_or_exception("Settings Database error: {}".format(e))
        flash(_("Couldn't save the user. Try again; if it keeps failing, check Logs in Settings."),
              category="error")
    return ""
