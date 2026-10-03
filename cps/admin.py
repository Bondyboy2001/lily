# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Admin pages: the user list and user editor, plus the admin-only maintenance endpoints."""

import os
import json
from functools import wraps

from flask import Blueprint, flash, redirect, url_for, abort, request, g
from .cw_login import current_user
from flask_babel import gettext as _
from sqlalchemy.exc import IntegrityError, OperationalError

from . import constants, logger, helper, cli_param
from . import calibre_db, ub, config
from werkzeug.security import generate_password_hash
from .helper import check_email, valid_email, check_username
from .render_template import render_title_template
from .usermanagement import user_login_required

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
    g.allow_anonymous = config.config_anonbrowse
    g.allow_upload = config.config_uploading
    g.config_authors_max = config.config_authors_max
    # The library lives at /calibre-library (autoconfigured above); there is no page to point
    # Lily elsewhere, so without a usable metadata.db every page but login says so.
    if '/static/' not in request.path and not config.db_configured and \
            request.endpoint not in ('web.login', 'web.login_post', 'web.logout'):
        abort(503, description=_("No Calibre library found at /calibre-library. Mount a library "
                                 "folder containing metadata.db there and restart Lily."))


# method is available without login and not protected by CSRF to make it easy reachable, is per default switched off
# needed for docker applications, as changes on metadata.db from host are not visible to application
@admi.route("/reconnect", methods=['GET'])
def reconnect():
    if cli_param.reconnect_enable:
        calibre_db.reconnect_db(config, ub.app_DB_path)
        return json.dumps({})
    log.debug("'/reconnect' was accessed but is not enabled")
    abort(404)


@admi.route("/admin/usertable")
@user_login_required
@admin_required
def edit_user_table():
    all_user = ub.session.query(ub.User)
    if not config.config_anonbrowse:
        all_user = all_user.filter(ub.User.role.op('&')(constants.ROLE_ANONYMOUS) != constants.ROLE_ANONYMOUS)
    users = all_user.order_by(ub.User.name).all()
    # Each card says how many of the editor's permission boxes are ticked.
    granted = {user.id: sum(1 for role in constants.ALL_ROLES.values() if user.role & role) for user in users}
    return render_title_template("user_table.html", users=users, granted=granted,
                                 permission_count=len(constants.ALL_ROLES),
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
    if request.method == "POST":
        to_save = request.form.to_dict()
        _handle_new_user(to_save, content)
    else:
        content.role = config.config_default_role
        content.sidebar_view = config.config_default_show
        content.default_language = config.config_default_language
    return render_title_template("user_edit.html", new_user=1, content=content,
                                 config=config, title=_("Add New User"), page="newuser")


@admi.route("/admin/user/<int:user_id>", methods=["GET", "POST"])
@user_login_required
@admin_required
def edit_user(user_id):
    content = ub.session.query(ub.User).filter(ub.User.id == int(user_id)).first()  # type: ub.User
    if not content or (not config.config_anonbrowse and content.name == "Guest"):
        flash(_("User not found"), category="error")
        return redirect(url_for('admin.edit_user_table'))
    if request.method == "POST":
        to_save = request.form.to_dict()
        resp = _handle_edit_user(to_save, content)
        if resp:
            return resp
    return render_title_template("user_edit.html",
                                 new_user=0,
                                 content=content,
                                 config=config,
                                 title=_("Edit User %(nick)s", nick=content.name),
                                 page="edituser")


def _handle_new_user(to_save, content):
    content.default_language = config.config_default_language
    content.sidebar_view = config.config_default_show

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
        return render_title_template("user_edit.html", new_user=1, content=content,
                                     config=config, title=_("Add New User"), page="newuser")
    try:
        content.allowed_tags = config.config_allowed_tags
        content.denied_tags = config.config_denied_tags
        content.allowed_column_value = config.config_allowed_column_value
        content.denied_column_value = config.config_denied_column_value
        ub.session.add(content)
        ub.session.commit()
        flash(_("User '%(user)s' created", user=content.name), category="success")
        log.debug(f"User {content.name} created")
        return redirect(url_for('admin.edit_user_table'))
    except IntegrityError:
        ub.session.rollback()
        log.error(f"Found an existing account for {content.name} or {content.email}")
        flash(_("A user with that name or email already exists. Choose a different one."), category="error")
    except OperationalError as e:
        ub.session.rollback()
        log.error_or_exception(f"Settings Database error: {e}")
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
            log.info(f"User {content.name} deleted")
            return _("User '%(nick)s' deleted", nick=content.name)
        raise Exception(_("Can't delete Guest User"))
    raise Exception(_("No admin user remaining, can't delete user"))


def _handle_edit_user(to_save, content):
    if to_save.get("delete"):
        try:
            flash(_delete_user(content), category="success")
        except Exception as ex:
            log.error(ex)
            flash(str(ex), category="error")
        return redirect(url_for('admin.edit_user_table'))
    if not ub.session.query(ub.User).filter(ub.User.role.op('&')(constants.ROLE_ADMIN) == constants.ROLE_ADMIN,
                                            ub.User.id != content.id).count() and 'admin_role' not in to_save:
        log.warning(f"No admin user remaining, can't remove admin role from {content.name}")
        flash(_("No admin user remaining, can't remove admin role"), category="error")
        return redirect(url_for('admin.edit_user_table'))

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
        return render_title_template("user_edit.html",
                                     new_user=0,
                                     content=content,
                                     config=config,
                                     title=_("Edit User %(nick)s", nick=content.name),
                                     page="edituser")
    try:
        ub.session_commit()
        flash(_("User '%(nick)s' updated", nick=content.name), category="success")
    except IntegrityError as ex:
        ub.session.rollback()
        log.error(f"An unknown error occurred while changing user: {str(ex)}")
        flash(_("Couldn't save the user. Try again; if it keeps failing, check Logs in Settings."),
              category="error")
    except OperationalError as e:
        ub.session.rollback()
        log.error_or_exception(f"Settings Database error: {e}")
        flash(_("Couldn't save the user. Try again; if it keeps failing, check Logs in Settings."),
              category="error")
    return ""
