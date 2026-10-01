# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import sys
import platform
import sqlite3
import importlib
from collections import OrderedDict

from . import converter, uploader, dep_check

modules = dict()
req = dep_check.load_dependencies(False)
opt = dep_check.load_dependencies(True)
for i in (req + opt):
    modules[i[1]] = i[0]
modules['Jinja2'] = importlib.metadata.version("jinja2")
if sys.version_info < (3, 12):
    modules['pySqlite'] = sqlite3.version
modules['SQLite'] = sqlite3.sqlite_version
sorted_modules = OrderedDict(sorted(modules.items(), key=lambda x: x[0].casefold()))


def collect_stats():
    try:
        with open("/app/CWA_RELEASE", "r") as f:
            cwa_version = f.read()
    except Exception:
        cwa_version = "Unknown"

    _VERSIONS = {'Lily': cwa_version}
    _VERSIONS.update(OrderedDict(
        Python=sys.version,
        Platform='{0[0]} {0[2]} {0[3]} {0[4]} {0[5]}'.format(platform.uname()),
    ))
    _VERSIONS['Ebook converter'] = converter.get_calibre_version()
    _VERSIONS.update(uploader.get_magick_version())
    _VERSIONS.update(sorted_modules)
    return _VERSIONS
