# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import sys

from . import create_app, limiter
from .jinjia import jinjia
from flask import request


def request_username():
    return request.authorization.username


def main():
    app = create_app()

    from .cwa_functions import library_refresh, cwa_stats, cwa_check_status, cwa_settings, cwa_internal
    from .web import web
    from .opds import opds
    from .admin import admi
    from .gdrive import gdrive
    from .editbooks import editbook
    from .about import about
    from .search import search
    from .search_metadata import meta
    from .shelf import shelf
    from .tasks_status import tasks
    from .error_handler import init_errorhandler
    from .duplicates import duplicates

    from . import web_server
    init_errorhandler()


    # CWA Blueprints
    app.register_blueprint(library_refresh)
    app.register_blueprint(cwa_stats)
    app.register_blueprint(cwa_check_status)
    app.register_blueprint(cwa_settings)
    app.register_blueprint(cwa_internal)

    # Stock CW
    app.register_blueprint(search)
    app.register_blueprint(tasks)
    app.register_blueprint(web)
    app.register_blueprint(opds)
    limiter.limit("3/minute", key_func=request_username)(opds)
    app.register_blueprint(jinjia)
    app.register_blueprint(about)
    app.register_blueprint(shelf)
    app.register_blueprint(admi)
    app.register_blueprint(meta)
    app.register_blueprint(gdrive)
    app.register_blueprint(editbook)
    app.register_blueprint(duplicates)
    success = web_server.start()
    sys.exit(0 if success else 1)
