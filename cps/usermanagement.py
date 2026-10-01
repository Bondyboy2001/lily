# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Authentication helpers: Basic auth for OPDS, API-token and Bearer lookup, login-required decorators."""

from functools import wraps

from sqlalchemy.sql.expression import func
from .cw_login import login_required

from flask import request, g
from flask_httpauth import HTTPBasicAuth
from werkzeug.datastructures import Authorization
from werkzeug.security import check_password_hash

from . import lm, ub, config, logger, limiter, totp


log = logger.create()
auth = HTTPBasicAuth()


def user_for_api_token(token):
    """The user owning this personal API token, or None."""
    if not totp.looks_like_api_token(token):
        return None
    return ub.session.query(ub.User).filter(ub.User.api_token_hash == totp.hash_api_token(token)).first()


@auth.verify_password
def verify_password(username, password):
    user = ub.session.query(ub.User).filter(func.lower(ub.User.name) == username.lower()).first()
    if user:
        if user.name.lower() == "guest":
            if config.config_anonbrowse == 1:
                return user
        else:
            limiter.check()
            # A personal API token is accepted in place of the password. It is the only way
            # in for OPDS clients once the account has two-factor auth turned on, because
            # Basic auth has no place to type a code.
            token_owner = user_for_api_token(password)
            if token_owner is not None and token_owner.id == user.id:
                [limiter.limiter.storage.clear(k.key) for k in limiter.current_limits]
                return user
            if not user.totp_enabled and check_password_hash(str(user.password), password):
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


@lm.request_loader
def load_user_from_bearer_token(req):
    """Lets scripts call the web and stats endpoints with 'Authorization: Bearer lily_...'."""
    header = req.headers.get("Authorization", "")
    if header[:7].lower() != "bearer ":
        return None
    try:
        return user_for_api_token(header[7:].strip())
    except Exception as e:
        log.error("API token lookup failed: %s", e)
        return None


@lm.user_loader
def load_user(user_id, random, session_key):
    try:
        # Handle potential invalid user_id
        if not user_id:
            return None
        # Every login stores a User_Sessions row keyed by a random value; a session or remember
        # cookie without one (or whose row was deleted at logout or password change) is refused.
        if not random:
            return None
        user = ub.session.query(ub.User).filter(ub.User.id == int(user_id)).first()
        if not user:
            return None

        query = ub.session.query(ub.User_Sessions).filter(ub.User_Sessions.random == random,
                                                          ub.User_Sessions.user_id == user.id)
        if session_key:
            query = query.filter(ub.User_Sessions.session_key == session_key)
        if query.first() is None:
            return None
        return user
    except (ValueError, TypeError) as e:
        log.error("Invalid user_id in load_user: %s", e)
        return None

