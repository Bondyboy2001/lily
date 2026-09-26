# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import secrets
import traceback

from flask import render_template
from .cw_login import current_user
from werkzeug.exceptions import default_exceptions
try:
    from werkzeug.exceptions import FailedDependency
except ImportError:
    from werkzeug.exceptions import UnprocessableEntity as FailedDependency

from . import config, app, logger, services


log = logger.create()

# custom error page

def error_http(error):
    headers = {'WWW-Authenticate': f'Basic realm="{config.config_calibre_web_title or "lily"}"'} if error.code == 401 else {}
    return render_template('http_error.html',
                           error_code="Error {0}".format(error.code),
                           error_name=error.name,
                           issue=False,
                           unconfigured=not config.db_configured,
                           instance=config.config_calibre_web_title
                           ), error.code, headers


def _is_admin():
    # current_user may be unavailable (no request/app context, broken user DB, anonymous proxy, ...)
    try:
        return bool(current_user and current_user.is_authenticated and current_user.role_admin())
    except Exception:
        return False


def internal_error(error):
    error_id = secrets.token_hex(4)
    stack = traceback.format_exc()
    log.error("Internal server error [error id %s]:\n%s", error_id, stack)
    return render_template('http_error.html',
                           error_code="500 Internal Server Error",
                           error_name='Something went wrong on our side and the request could not be completed.',
                           issue=True,
                           unconfigured=False,
                           error_id=error_id,
                           error_stack=stack.split("\n") if _is_admin() else [],
                           instance=config.config_calibre_web_title
                           ), 500


def init_errorhandler():
    # http error handling
    for ex in default_exceptions:
        if ex < 500:
            app.register_error_handler(ex, error_http)
        elif ex == 500:
            app.register_error_handler(ex, internal_error)

    if services.ldap:
        # Only way of catching the LDAPException upon logging in with LDAP server down
        @app.errorhandler(services.ldap.LDAPException)
        # pylint: disable=unused-variable
        def handle_exception(e):
            log.debug('LDAP server not accessible while trying to login to opds feed')
            return error_http(FailedDependency())

