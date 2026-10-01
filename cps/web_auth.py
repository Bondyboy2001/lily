# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Sign-in, second factor, sign-out, forced password change, password change and the user's own profile.

Routes are attached to the web blueprint; web.py imports this module at its end."""

import importlib

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
from .helper import check_username, valid_password
from .redirect import get_redirect_location
from .cw_babel import get_available_locale
from .render_template import render_title_template
from . import limiter, totp
from limits import parse_many
from .usermanagement import user_login_required
from .string_helper import strip_whitespaces

# CWA Imports
import time

import sys
sys.path.insert(1, '/app/calibre-web-automated/scripts/')


try:
    from natsort import natsorted as sort
except ImportError:
    sort = sorted  # Just use regular sort then, may cause issues with badly named pages in cbz/cbr files


sql_version = importlib.metadata.version("sqlalchemy")
sqlalchemy_version2 = ([int(x) for x in sql_version.split('.')] >= [2, 0, 0])

_start_time = time.time()

from .web import web, log


# ################################### Login Logout ##################################################################

def handle_login_user(user, remember, message, category, next_url=None):
    login_user(user, remember=remember)

    # Track login activity
    try:
        from scripts.cwa_db import CWA_DB
        cwa_db = CWA_DB()
        cwa_db.log_activity(
            user_id=int(user.id),
            user_name=user.name,
            event_type='LOGIN'
        )
    except Exception as e:
        log.debug(f"Failed to log login activity: {e}")

    flash(message, category=category)
    [limiter.limiter.storage.clear(k.key) for k in limiter.current_limits]

    # Clear login redirect count on successful login
    flask_session.pop('_login_redirect_count', None)

    if next_url is None:
        next_url = request.form.get('next', None)
    return redirect(get_redirect_location(next_url, "web.index"))


def render_login(username="", second_factor=False, status=200):
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
                                 second_factor=second_factor,
                                 page="login"), status


@web.route('/login', methods=['GET'])
def login():
    if current_user is not None and current_user.is_authenticated:
        return redirect(url_for('web.index'))
    return render_login()


# Failed password logins allowed per client address and per username (only failures count;
# a successful login clears both). Off when the admin turns off "Limit failed login attempts".
_LOGIN_LIMITS = parse_many("5/minute;40/day")


def _login_limit_keys(username):
    return ("login-ip", request.remote_addr or "unknown"), ("login-user", username)


def _login_limits_active():
    return limiter.enabled and limiter.initialized


def _login_blocked(username):
    if not _login_limits_active():
        return False
    return any(not limiter.limiter.test(item, *key)
               for key in _login_limit_keys(username) for item in _LOGIN_LIMITS)


def _count_login_failure(username):
    if _login_limits_active():
        for key in _login_limit_keys(username):
            for item in _LOGIN_LIMITS:
                limiter.limiter.hit(item, *key)


def _clear_login_failures(username):
    if _login_limits_active():
        for key in _login_limit_keys(username):
            for item in _LOGIN_LIMITS:
                limiter.limiter.clear(item, *key)


@web.route('/login', methods=['POST'])
def login_post():
    form = request.form.to_dict()
    username = strip_whitespaces(form.get('username', "")).lower().replace("\n","").replace("\r","")
    if current_user is not None and current_user.is_authenticated:
        return redirect(url_for('web.index'))
    if _login_blocked(username):
        log.warning('Login rate limit reached for user "%s" IP-address: %s', username, request.remote_addr)
        flash(_("Too many failed sign-in attempts. Please wait a while and try again."), category="error")
        return render_login(username, status=429)
    user = ub.session.query(ub.User).filter(func.lower(ub.User.name) == username).first()
    remember_me = bool(form.get('remember_me'))

    # Use request.remote_addr (already corrected by ProxyFix) instead of raw header
    ip_address = request.remote_addr
    if user and check_password_hash(str(user.password), form.get('password', '')) and user.name != "Guest":
        config.config_is_initial = False
        _clear_login_failures(username)
        if user.totp_enabled and user.totp_secret:
            # Password accepted; the account still needs its second factor
            flask_session['_2fa_pending'] = {'uid': user.id, 'remember': remember_me,
                                             'next': form.get('next', None), 'ts': time.time()}
            return redirect(url_for('web.login_2fa'))
        log.debug(u"You are now logged in as: '{}'".format(user.name))
        return handle_login_user(user,
                                 remember_me,
                                 _(u"You are now logged in as: '%(nickname)s'", nickname=user.name),
                                 "success")
    else:
        log.warning('Login failed for user "{}" IP-address: {}'.format(username, ip_address))
        _count_login_failure(username)

        # Track failed login attempt
        try:
            from scripts.cwa_db import CWA_DB
            import json
            cwa_db = CWA_DB()
            cwa_db.log_activity(
                user_id=None,
                user_name='Anonymous',
                event_type='LOGIN_FAILED',
                item_id=None,
                item_title=None,
                extra_data=json.dumps({'username_attempted': username, 'ip': ip_address, 'method': 'standard'})
            )
        except Exception as e:
            log.debug(f"Failed to log failed login attempt: {e}")

        flash(_(u"Wrong Username or Password"), category="error")
    # The attempted password is never sent back into the page
    return render_login(username)


_2FA_PENDING_SECONDS = 5 * 60


class _UserLockoutStore:
    """totp.FailureTracker state kept in the user table, so restarting Lily doesn't lift
    a wrong-code lockout."""

    @staticmethod
    def _user(user_id):
        return ub.session.query(ub.User).filter(ub.User.id == user_id).first()

    def get(self, user_id, default=None):
        user = self._user(user_id)
        if user is None or not (user.totp_failures or user.totp_lockouts or user.totp_locked_until):
            return default
        return {"count": user.totp_failures or 0, "until": user.totp_locked_until or 0,
                "lockouts": user.totp_lockouts or 0}

    def __setitem__(self, user_id, entry):
        user = self._user(user_id)
        if user is not None:
            user.totp_failures = entry.get("count", 0)
            user.totp_locked_until = entry.get("until", 0)
            user.totp_lockouts = entry.get("lockouts", 0)
            ub.session_commit()

    def pop(self, user_id, default=None):
        entry = self.get(user_id)
        if entry is not None:
            self[user_id] = {}
        return entry if entry is not None else default

    def __iter__(self):
        rows = ub.session.query(ub.User.id).filter(ub.User.totp_locked_until > 0).all()
        return iter([row[0] for row in rows])


_2fa_failures = totp.FailureTracker(store=_UserLockoutStore())


def _pending_2fa_user():
    pending = flask_session.get('_2fa_pending')
    if not pending or time.time() - pending.get('ts', 0) > _2FA_PENDING_SECONDS:
        flask_session.pop('_2fa_pending', None)
        return None, None
    return ub.session.query(ub.User).filter(ub.User.id == pending['uid']).first(), pending


@web.route('/login/2fa', methods=['GET', 'POST'])
def login_2fa():
    user, pending = _pending_2fa_user()
    if user is None or not user.totp_enabled:
        flash(_("Your sign-in expired. Please log in again."), category="error")
        return redirect(url_for('web.login'))
    if request.method == 'GET':
        return render_login(second_factor=True)
    if _2fa_failures.locked(user.id):
        flask_session.pop('_2fa_pending', None)
        log.warning('2FA locked out for user "%s" IP-address: %s', user.name, request.remote_addr)
        minutes = max(1, (_2fa_failures.seconds_left(user.id) + 59) // 60)
        flash(_("Too many wrong codes. Try again in %(minutes)s minutes.", minutes=minutes), category="error")
        return redirect(url_for('web.login'))
    step = totp.verify_code(user.totp_secret, request.form.get('code', ''), user.totp_last_step)
    if step is None:
        _2fa_failures.failure(user.id)
        log.warning('Wrong 2FA code for user "%s" IP-address: %s', user.name, request.remote_addr)
        flash(_("Wrong code. Please try again."), category="error")
        return render_login(second_factor=True)
    _2fa_failures.success(user.id)
    user.totp_last_step = step
    ub.session_commit()
    flask_session.pop('_2fa_pending', None)
    return handle_login_user(user, pending.get('remember', False),
                             _(u"You are now logged in as: '%(nickname)s'", nickname=user.name), "success",
                             next_url=pending.get('next') or '')


# POST only (with the CSRF token), so another site can't sign the user out with a link or image
@web.route('/logout', methods=['POST'])
@user_login_required
def logout():
    if current_user is not None and current_user.is_authenticated:
        ub.delete_user_session(current_user.id, flask_session.get('_id', ""), flask_session.get('_random', ""))
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
# /change-password on every web request. Device and machine endpoints are not redirected (internal
# services keep working); OPDS still refuses the default password itself (usermanagement).
_FORCE_PW_EXEMPT_BLUEPRINTS = {"opds", "cwa_internal"}
_FORCE_PW_EXEMPT_ENDPOINTS = {"static", "web.login", "web.login_post", "web.login_2fa", "web.logout",
                              "web.change_password", "web.health_check",
                              "gdrive.on_received_watch_confirmation"}


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
                # Everywhere else that was signed in with the old password is signed out
                ub.delete_other_user_sessions(user.id, flask_session.get('_random', ''))
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
    current_user.random_books = 0
    password_changed = False
    try:
        if current_user.role_passwd() or current_user.role_admin():
            if to_save.get("password", "") != "":
                # A borrowed or stolen session must not be enough to take over the account
                if not check_password_hash(str(current_user.password), to_save.get("current_password", "")):
                    raise Exception(_("Current password is incorrect"))
                current_user.password = generate_password_hash(valid_password(to_save.get("password")))
                password_changed = True
        if current_user.role_admin():
            if to_save.get("name", current_user.name) != current_user.name:
                # Query username, if not existing, change
                current_user.name = check_username(to_save.get("name"))
        current_user.random_books = 1 if to_save.get("show_random") == "on" else 0
        current_user.default_language = to_save.get("default_language", "all")
        current_user.locale = to_save.get("locale", "en")
        if "hardcover_token" in to_save:
            current_user.hardcover_token = to_save["hardcover_token"].replace("Bearer ", "") or None
        current_user.auto_metadata_fetch = to_save.get("auto_metadata_fetch") == "on"

        # OPDS root order
        opds_order_raw = to_save.get("opds_root_order", "").strip()
        if opds_order_raw:
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
        if opds_hidden_raw:
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

    val = 0
    for key, __ in to_save.items():
        if key.startswith('show'):
            try:
                val += int(key[5:])
            except (ValueError, IndexError):
                log.warning(f"Skipping invalid sidebar checkbox key: {key}")
                continue
    current_user.sidebar_view = val
    if to_save.get("Show_detail_random"):
        current_user.sidebar_view += constants.DETAIL_RANDOM

    try:
        ub.session.commit()
        if password_changed:
            ub.delete_other_user_sessions(current_user.id, flask_session.get('_random', ''))
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
