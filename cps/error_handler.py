# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""HTML and JSON error pages for HTTP errors."""

import secrets
import traceback

from flask import render_template, request
from .cw_login import current_user
from werkzeug.exceptions import default_exceptions

from . import config, app, logger


log = logger.create()

# custom error page

def error_http(error):
    if error.code == 413 and request.endpoint == "edit-book.upload":
        # The CSRF check parses the form before the upload view runs, so an oversize
        # upload usually ends here; give it the same plain message the view would.
        from .editbooks_upload import upload_too_large_response
        return upload_too_large_response()
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

