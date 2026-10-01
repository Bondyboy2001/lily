# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Account security page: optional TOTP second factor and a personal API token."""

from flask import Blueprint, flash, redirect, request, url_for, session as flask_session
from flask_babel import gettext as _
from werkzeug.security import check_password_hash

from . import ub, logger, totp
from .cw_login import current_user
from .render_template import render_title_template
from .usermanagement import user_login_required

account_security = Blueprint('account_security', __name__)
log = logger.create()

_SETUP_KEY = '_2fa_setup_secret'
_NEW_TOKEN_KEY = '_new_api_token'


def _me():
    """The signed-in user's row, or None for the guest/anonymous account."""
    if current_user.role_anonymous() or current_user.name == "Guest":
        return None
    return ub.session.query(ub.User).filter(ub.User.id == current_user.id).first()


def _password_ok(user):
    return check_password_hash(str(user.password), request.form.get('password', ''))


def _back():
    return redirect(url_for('account_security.security_page'))


@account_security.route('/account/security', methods=['GET'])
@user_login_required
def security_page():
    user = _me()
    if user is None:
        return redirect(url_for('web.profile'))
    setup_secret = flask_session.get(_SETUP_KEY)
    locked = []
    if current_user.role_admin():
        from .web_auth import _2fa_failures
        ids = _2fa_failures.locked_keys()
        if ids:
            locked = ub.session.query(ub.User).filter(ub.User.id.in_(ids)).all()
    return render_title_template(
        'account_security.html', title=_("Security"), page="account_security",
        totp_enabled=bool(user.totp_enabled), setup_secret=setup_secret,
        setup_uri=totp.provisioning_uri(setup_secret, user.name) if setup_secret else None,
        has_token=bool(user.api_token_hash), new_token=flask_session.pop(_NEW_TOKEN_KEY, None),
        locked_users=locked)


@account_security.route('/account/security/2fa/start', methods=['POST'])
@user_login_required
def start_2fa():
    user = _me()
    if user is None or user.totp_enabled:
        return _back()
    flask_session[_SETUP_KEY] = totp.generate_secret()
    return _back()


@account_security.route('/account/security/2fa/enable', methods=['POST'])
@user_login_required
def enable_2fa():
    user = _me()
    secret = flask_session.get(_SETUP_KEY)
    if user is None or not secret:
        return _back()
    step = totp.verify_code(secret, request.form.get('code', ''))
    if step is None:
        flash(_("That code did not match. Check the time on your phone and try again."), category="error")
        return _back()
    user.totp_secret, user.totp_enabled, user.totp_last_step = secret, True, step
    ub.session_commit()
    # Sessions signed in elsewhere never passed the new second factor
    ub.delete_other_user_sessions(user.id, flask_session.get('_random', ''))
    flask_session.pop(_SETUP_KEY, None)
    log.info("User '%s' enabled two-factor authentication", user.name)
    flash(_("Two-factor authentication is on. OPDS apps now need an API token instead of your password."),
          category="success")
    return _back()


@account_security.route('/account/security/2fa/cancel', methods=['POST'])
@user_login_required
def cancel_2fa_setup():
    flask_session.pop(_SETUP_KEY, None)
    return _back()


@account_security.route('/account/security/2fa/disable', methods=['POST'])
@user_login_required
def disable_2fa():
    user = _me()
    if user is None or not user.totp_enabled:
        return _back()
    step = totp.verify_code(user.totp_secret, request.form.get('code', ''), user.totp_last_step)
    if not _password_ok(user) or step is None:
        flash(_("Wrong password or code."), category="error")
        return _back()
    user.totp_secret, user.totp_enabled, user.totp_last_step = None, False, 0
    ub.session_commit()
    log.info("User '%s' disabled two-factor authentication", user.name)
    flash(_("Two-factor authentication is off."), category="success")
    return _back()


@account_security.route('/account/security/token/create', methods=['POST'])
@user_login_required
def create_api_token():
    user = _me()
    if user is None:
        return _back()
    if not _password_ok(user):
        flash(_("Wrong password."), category="error")
        return _back()
    token = totp.new_api_token()
    user.api_token_hash = totp.hash_api_token(token)
    ub.session_commit()
    flask_session[_NEW_TOKEN_KEY] = token  # shown once on the next page load
    log.info("User '%s' created an API token", user.name)
    return _back()


@account_security.route('/account/security/token/revoke', methods=['POST'])
@user_login_required
def revoke_api_token():
    user = _me()
    if user is not None and user.api_token_hash:
        user.api_token_hash = None
        ub.session_commit()
        log.info("User '%s' revoked their API token", user.name)
        flash(_("API token revoked."), category="success")
    return _back()


@account_security.route('/account/security/unlock/<int:user_id>', methods=['POST'])
@user_login_required
def unlock_2fa(user_id):
    if not current_user.role_admin():
        return redirect(url_for('web.index'))
    from .web_auth import _2fa_failures
    _2fa_failures.success(user_id)
    flash(_("Account unlocked."), category="success")
    return _back()
