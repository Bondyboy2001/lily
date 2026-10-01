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

# Where a personal API token is accepted ('Authorization: Bearer lily_...', or as the OPDS
# Basic-auth password): the OPDS feed and the read-only stats CSV export. Nowhere else, so a
# leaked token can't reach settings, user management or anything that changes data.
TOKEN_AUTH_BLUEPRINTS = frozenset({"opds"})
TOKEN_AUTH_ENDPOINTS = frozenset({"cwa_stats.export_stats_csv"})


def token_auth_allowed():
    return request.blueprint in TOKEN_AUTH_BLUEPRINTS or request.endpoint in TOKEN_AUTH_ENDPOINTS


def token_authenticated():
    """True when this request's user came from an API token rather than a login session."""
    return bool(g.get("lily_token_auth"))


def refuse_token_auth():
    """For admin-only decorators: a token may not stand in for an admin's login session
    (beyond the read-only exports in TOKEN_AUTH_ENDPOINTS)."""
    return token_authenticated() and request.endpoint not in TOKEN_AUTH_ENDPOINTS


def _bearer_token(req):
    header = req.headers.get("Authorization", "")
    if header[:7].lower() != "bearer ":
        return None
    return header[7:].strip()


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
                g.lily_token_auth = True
                return user
            # An account still on the shipped default password has a publicly known password
            if user.force_password_change:
                log.warning('OPDS login refused for user "%s": the password must be changed first', username)
                return None
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
        token = _bearer_token(request)
        status = None
        if token is not None:
            user = user_for_api_token(token)
            if user is not None:
                g.lily_token_auth = True
        else:
            authorisation = auth.get_auth()
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
    """Lets scripts fetch the endpoints in TOKEN_AUTH_ENDPOINTS with 'Authorization: Bearer lily_...'."""
    token = _bearer_token(req)
    if token is None or not token_auth_allowed():
        return None
    try:
        user = user_for_api_token(token)
    except Exception as e:
        log.error("API token lookup failed: %s", e)
        return None
    if user is not None:
        g.lily_token_auth = True
    return user


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

