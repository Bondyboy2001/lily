# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Authentication helpers: Basic auth for OPDS and the login-required decorators."""

from functools import wraps

from sqlalchemy.sql.expression import func
from .cw_login import login_required

from flask import request, g
from flask_httpauth import HTTPBasicAuth
from werkzeug.datastructures import Authorization
from werkzeug.security import check_password_hash

from . import lm, ub, config, logger, limiter


log = logger.create()
auth = HTTPBasicAuth()


@auth.verify_password
def verify_password(username, password):
    user = ub.session.query(ub.User).filter(func.lower(ub.User.name) == username.lower()).first()
    if user:
        if user.name.lower() == "guest":
            if config.config_anonbrowse == 1:
                return user
        elif user.force_password_change:
            pass
        else:
            limiter.check()
            if check_password_hash(str(user.password), password):
                [limiter.limiter.storage.clear(k.key) for k in limiter.current_limits]
                return user

    # Use request.remote_addr (already corrected by ProxyFix) instead of raw header
    ip_address = request.remote_addr
    log.warning('OPDS Login failed for user "%s" IP-address: %s', username, ip_address)
    return None


def requires_basic_auth_if_no_ano(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        authorisation = auth.get_auth()
        status = None
        if config.config_anonbrowse == 1 and not authorisation:
            authorisation = Authorization(
                b"Basic", {'username': "Guest", 'password': ""})
        user = auth.authenticate(authorisation, "")
        if user in (False, None):
            status = 401
        if status:
            try:
                return auth.auth_error_callback(status)
            except TypeError:
                return auth.auth_error_callback()
        g.flask_httpauth_user = user if user is not True \
            else auth.username if auth else None
        return auth.ensure_sync(f)(*args, **kwargs)
    return decorated


def login_required_if_no_ano(func):
    @wraps(func)
    def decorated_view(*args, **kwargs):
        if config.config_anonbrowse == 1:
            return func(*args, **kwargs)
        return login_required(func)(*args, **kwargs)

    return decorated_view


def user_login_required(func):
    @wraps(func)
    def decorated_view(*args, **kwargs):
        return login_required(func)(*args, **kwargs)

    return decorated_view


@lm.user_loader
def load_user(user_id, random, session_key):
    try:
        # Handle potential invalid user_id
        if not user_id:
            return None
        user = ub.session.query(ub.User).filter(ub.User.id == int(user_id)).first()
        if not user:
            return None

        if session_key:
            entry = ub.session.query(ub.User_Sessions).filter(ub.User_Sessions.random == random,
                                                              ub.User_Sessions.session_key == session_key).first()
            if not entry or entry.user_id != user.id:
                return None
        elif random:
            entry = ub.session.query(ub.User_Sessions).filter(ub.User_Sessions.random == random).first()
            if not entry or entry.user_id != user.id:
                return None
        return user
    except (ValueError, TypeError) as e:
        log.error("Invalid user_id in load_user: %s", e)
        return None

