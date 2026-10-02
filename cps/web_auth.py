# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Sign-in, second factor, sign-out, forced password change, password change and the user's own profile.

Routes are attached to the web blueprint; web.py imports this module at its end."""


from flask import request, redirect, flash, abort, url_for
from flask import session as flask_session
from flask_babel import gettext as _
from .cw_login import login_user, logout_user, current_user
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.sql.expression import func
from sqlalchemy.orm.attributes import flag_modified
from werkzeug.security import generate_password_hash, check_password_hash

from . import constants
from . import ub, config
from . import calibre_db
from .helper import check_email, check_username, \
    valid_email, \
    valid_password
from .redirect import get_redirect_location
from .cw_babel import get_available_locale
from .render_template import render_title_template
from . import limiter
from .usermanagement import user_login_required
from .string_helper import strip_whitespaces

from .web import web, log


# ################################### Login Logout ##################################################################

def handle_login_user(user, remember, message, category, next_url=None):
    login_user(user, remember=remember)

    flash(message, category=category)
    [limiter.limiter.storage.clear(k.key) for k in limiter.current_limits]

    # Clear login redirect count on successful login
    flask_session.pop('_login_redirect_count', None)

    if next_url is None:
        next_url = request.form.get('next', None)
    return redirect(get_redirect_location(next_url, "web.index"))


def render_login(username="", password=""):
    # Detect authentication redirect loops
    redirect_count = flask_session.get('_login_redirect_count', 0)
    if redirect_count > 3:
        flask_session.pop('_login_redirect_count', None)
        log.warning("Authentication redirect loop detected from IP: %s", request.remote_addr)
        flash(_("Authentication loop detected. If you're experiencing login issues, please contact your administrator."), category="error")
    else:
        flask_session['_login_redirect_count'] = redirect_count + 1

    next_url = request.args.get('next', default=url_for("web.index"), type=str)
    if url_for("web.logout") == next_url:
        next_url = url_for("web.index")

    return render_title_template('login.html',
                                 title=_("Login"),
                                 next_url=next_url,
                                 config=config,
                                 username=username,
                                 password=password,
                                 page="login")


@web.route('/login', methods=['GET'])
def login():
    if current_user is not None and current_user.is_authenticated:
        return redirect(url_for('web.index'))
    return render_login()


@web.route('/login', methods=['POST'])
def login_post():
    form = request.form.to_dict()
    username = strip_whitespaces(form.get('username', "")).lower().replace("\n","").replace("\r","")
    if current_user is not None and current_user.is_authenticated:
        return redirect(url_for('web.index'))
    user = ub.session.query(ub.User).filter(func.lower(ub.User.name) == username).first()
    remember_me = bool(form.get('remember_me'))

    # Use request.remote_addr (already corrected by ProxyFix) instead of raw header
    ip_address = request.remote_addr
    if user and check_password_hash(str(user.password), form.get('password', '')) and user.name != "Guest":
        config.config_is_initial = False
        log.debug(u"You are now logged in as: '{}'".format(user.name))
        return handle_login_user(user,
                                 remember_me,
                                 _(u"You are now logged in as: '%(nickname)s'", nickname=user.name),
                                 "success")
    else:
        log.warning('Login failed for user "{}" IP-address: {}'.format(username, ip_address))

        flash(_(u"Wrong Username or Password"), category="error")
    return render_login(username, form.get("password", ""))


@web.route('/logout')
@user_login_required
def logout():
    if current_user is not None and current_user.is_authenticated:
        ub.delete_user_session(current_user.id, flask_session.get('_id', ""))
        logout_user()

    # Clear login redirect count on logout to prevent false positives
    flask_session.pop('_login_redirect_count', None)

    log.debug("User logged out")
    if config.config_anonbrowse:
        location = get_redirect_location(request.args.get('next', None), "web.login")
    else:
        location = None
    if location:
        return redirect(location)
    else:
        return redirect(url_for('web.login'))


# ################################### Forced password change ########################################################
# Accounts still on the shipped default password (ub.User.force_password_change) are sent to
# /change-password on every web request. Device and machine endpoints keep working so e-readers
# and internal services are not locked out while the admin picks a new password.
_FORCE_PW_EXEMPT_BLUEPRINTS = {"opds", "cwa_internal"}
_FORCE_PW_EXEMPT_ENDPOINTS = {"static", "web.login", "web.login_post", "web.logout",
                              "web.change_password", "web.health_check"}


def _force_password_change_exempt(endpoint, blueprint):
    if not endpoint or endpoint in _FORCE_PW_EXEMPT_ENDPOINTS or endpoint.endswith(".static"):
        return True
    return blueprint in _FORCE_PW_EXEMPT_BLUEPRINTS


@web.before_app_request
def enforce_forced_password_change():
    if _force_password_change_exempt(request.endpoint, request.blueprint):
        return None
    try:
        if not (current_user and current_user.is_authenticated
                and getattr(current_user, "force_password_change", False)):
            return None
    except Exception:
        return None
    if request.method in ("GET", "HEAD"):
        return redirect(url_for("web.change_password"))
    abort(403)


@web.route('/change-password', methods=['GET', 'POST'])
@user_login_required
def change_password():
    forced = bool(getattr(current_user, "force_password_change", False))
    if not forced and not (current_user.role_passwd() or current_user.role_admin()):
        abort(403)
    if request.method == "POST":
        form = request.form
        current_pw = form.get("current_password", "")
        new_pw = form.get("new_password", "")
        confirm_pw = form.get("confirm_password", "")
        if not current_user.password or not check_password_hash(str(current_user.password), current_pw):
            flash(_("Current password is incorrect"), category="error")
        elif not new_pw or new_pw != confirm_pw:
            flash(_("New passwords do not match"), category="error")
        elif new_pw == constants.DEFAULT_PASSWORD or check_password_hash(str(current_user.password), new_pw):
            flash(_("Please choose a password different from the current one"), category="error")
        else:
            try:
                user = ub.session.query(ub.User).filter(ub.User.id == current_user.id).first()
                # Assigning the password also clears force_password_change (ub listener)
                user.password = generate_password_hash(valid_password(new_pw))
                user.force_password_change = False
                ub.session_commit()
                log.info("User '%s' changed their password", user.name)
                flash(_("Password changed"), category="success")
                return redirect(url_for("web.index"))
            except Exception as ex:
                ub.session.rollback()
                flash(str(ex), category="error")
    # bodyClass "login" hides the shell, as on the login page (the nav would only redirect back here)
    return render_title_template("change_password.html", title=_("Change Password"),
                                 page="change_password", bodyClass="login", forced=forced)


# ################################### Users own configuration #########################################################
def change_profile(translations, languages):
    to_save = request.form.to_dict()
    try:
        if current_user.role_passwd() or current_user.role_admin():
            if to_save.get("password", "") != "":
                current_user.password = generate_password_hash(valid_password(to_save.get("password")))
        new_email = valid_email(to_save.get("email", current_user.email))
        if not new_email:
            raise Exception(_("Email can't be empty and has to be a valid Email"))
        if new_email != current_user.email:
            current_user.email = check_email(new_email)
        if current_user.role_admin():
            if to_save.get("name", current_user.name) != current_user.name:
                # Query username, if not existing, change
                current_user.name = check_username(to_save.get("name"))
        # The profile form only shows account fields; anything it doesn't send keeps its value.
        if "default_language" in to_save:
            current_user.default_language = to_save["default_language"]
        if "locale" in to_save:
            current_user.locale = to_save["locale"]
        if "hardcover_token" in to_save:
            current_user.hardcover_token = to_save["hardcover_token"].replace("Bearer ", "") or None
        if "auto_metadata_fetch" in to_save:
            current_user.auto_metadata_fetch = to_save.get("auto_metadata_fetch") == "on"

        # OPDS root order
        opds_order_raw = to_save.get("opds_root_order", "").strip()
        if "opds_root_order" not in to_save:
            pass
        elif opds_order_raw:
            from .opds import normalize_opds_root_order
            opds_order_list = [item.strip() for item in opds_order_raw.split(',') if item.strip()]
            normalized_order = normalize_opds_root_order(opds_order_list)
            if current_user.view_settings is None:
                current_user.view_settings = {}
            current_user.view_settings.setdefault('opds', {})['root_order'] = normalized_order
            flag_modified(current_user, "view_settings")
        else:
            if current_user.view_settings and current_user.view_settings.get('opds', {}).get('root_order'):
                current_user.view_settings['opds'].pop('root_order', None)
                if not current_user.view_settings['opds']:
                    current_user.view_settings.pop('opds', None)
                flag_modified(current_user, "view_settings")

        # OPDS hidden entries
        opds_hidden_raw = to_save.get("opds_hidden_entries", "").strip()
        if "opds_hidden_entries" not in to_save:
            pass
        elif opds_hidden_raw:
            from .opds import OPDS_ROOT_ENTRY_DEFS
            hidden_entries = [item.strip() for item in opds_hidden_raw.split(',') if item.strip()]
            hidden_entries = [key for key in hidden_entries if key in OPDS_ROOT_ENTRY_DEFS]
            if current_user.view_settings is None:
                current_user.view_settings = {}
            current_user.view_settings.setdefault('opds', {})['hidden_entries'] = hidden_entries
            flag_modified(current_user, "view_settings")
        else:
            if current_user.view_settings and current_user.view_settings.get('opds', {}).get('hidden_entries'):
                current_user.view_settings['opds'].pop('hidden_entries', None)
                if not current_user.view_settings['opds']:
                    current_user.view_settings.pop('opds', None)
                flag_modified(current_user, "view_settings")

    except Exception as ex:
        flash(str(ex), category="error")
        from .opds import (
            get_opds_root_order_for_user,
            get_opds_hidden_entries_for_user,
            OPDS_ROOT_ENTRY_DEFS,
            OPDS_ROOT_ORDER_DEFAULT,
        )
        opds_root_order = get_opds_root_order_for_user(current_user)
        opds_root_order_string = ",".join(opds_root_order)
        opds_hidden_entries = list(get_opds_hidden_entries_for_user(current_user))
        opds_hidden_entries_string = ",".join(opds_hidden_entries)
        opds_root_labels = [
            {
                "key": key,
                "label": _(OPDS_ROOT_ENTRY_DEFS[key]['title']),
            }
            for key in OPDS_ROOT_ORDER_DEFAULT
            if key in OPDS_ROOT_ENTRY_DEFS
        ]

        return render_title_template("user_edit.html",
                                     content=current_user,
                                     config=config,
                                     translations=translations,
                                     profile=1,
                                     languages=languages,
                                     opds_root_order_string=opds_root_order_string,
                                     opds_hidden_entries_string=opds_hidden_entries_string,
                                     opds_root_labels=opds_root_labels,
                                     title=_("%(name)s's Profile", name=current_user.name.capitalize()),
                                     page="me")

    try:
        ub.session.commit()
        flash(_("Success! Profile Updated"), category="success")
        log.debug("Profile updated")
        return redirect(url_for('web.profile'))
    except IntegrityError:
        ub.session.rollback()
        flash(_("Oops! An account already exists for this Email."), category="error")
        log.debug("Found an existing account for this Email")
    except OperationalError as e:
        ub.session.rollback()
        log.error("Database error: %s", e)
        flash(_("Oops! Database Error: %(error)s.", error=e), category="error")


@web.route("/me", methods=["GET", "POST"])
@user_login_required
def profile():
    languages = calibre_db.speaking_language()
    translations = get_available_locale()
    if request.method == "POST":
        return change_profile(translations, languages)

    from .opds import get_opds_root_order_for_user, get_opds_hidden_entries_for_user, OPDS_ROOT_ENTRY_DEFS, OPDS_ROOT_ORDER_DEFAULT
    opds_root_order = get_opds_root_order_for_user(current_user)
    opds_root_order_string = ",".join(opds_root_order)
    opds_hidden_entries = list(get_opds_hidden_entries_for_user(current_user))
    opds_hidden_entries_string = ",".join(opds_hidden_entries)
    opds_root_labels = [
        {
            "key": key,
            "label": _(OPDS_ROOT_ENTRY_DEFS[key]['title'])
        }
        for key in OPDS_ROOT_ORDER_DEFAULT
        if key in OPDS_ROOT_ENTRY_DEFS
    ]

    return render_title_template("user_edit.html",
                                 translations=translations,
                                 profile=1,
                                 languages=languages,
                                 content=current_user,
                                 config=config,
                                 opds_root_order_string=opds_root_order_string,
                                 opds_hidden_entries_string=opds_hidden_entries_string,
                                 opds_root_labels=opds_root_labels,
                                 title=_("%(name)s's Profile", name=current_user.name.capitalize()),
                                 page="me")
