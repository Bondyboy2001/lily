# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Blueprints, logger and paths shared by every cwa_functions module."""

from flask import Blueprint

from .. import logger

import sys
sys.path.insert(1, '/app/calibre-web-automated/scripts/')

library_refresh = Blueprint('library_refresh', __name__)
cwa_check_status = Blueprint('cwa_check_status', __name__)
cwa_settings = Blueprint('cwa_settings', __name__)
cwa_internal = Blueprint('cwa_internal', __name__)

log = logger.create()

DIRS_JSON = "/app/calibre-web-automated/dirs.json"
