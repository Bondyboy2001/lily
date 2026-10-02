# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline reading: the service worker (/sw.js) and the page it opens when there's no network.

The worker is rendered rather than served from /static so it can sit at the app root (its scope
is the whole app) and carry this deploy's cache-busted asset URLs. Books are only kept on a
device that has them in Continue Reading; offline.js on the library page tells the worker which. Browsers only run service workers on HTTPS (or
localhost), so on plain http://host:port none of this switches on.
"""

import hashlib
import os

from flask import Blueprint, current_app, make_response, render_template, url_for

offline = Blueprint('offline', __name__)

# Files the readers load by themselves, so a kept book's page doesn't name them: pdf.js' strings and
# standard fonts, the DjVu viewer's generated scripts, and the fonts the reader chrome uses.
# Directories are walked; cmaps (CJK PDFs only, 1.6 MB) are left out.
READER_EXTRAS = {
    "pdf": ["locale/locale.json", "locale/en-US", "standard_fonts"],
    "djvu": ["js/libs/djvu_html5/djvu_html5"],
    "djv": ["js/libs/djvu_html5/djvu_html5"],
    "all": ["fonts/literata"],
}


def _static_files(rel):
    root = current_app.static_folder
    path = os.path.join(root, rel)
    if os.path.isfile(path):
        return [rel]
    found = []
    for folder, _dirs, files in os.walk(path):
        for name in sorted(files):
            if name.endswith((".map", ".txt")) or name.startswith("."):
                continue
            found.append(os.path.relpath(os.path.join(folder, name), root).replace(os.sep, "/"))
    return sorted(found)


def _extras():
    return {fmt: [url_for('static', filename=f) for rel in rels for f in _static_files(rel)]
            for fmt, rels in READER_EXTRAS.items()}


@offline.route("/sw.js")
def service_worker():
    extras = _extras()
    shell = url_for('offline.offline_page')
    # A new deploy (new asset hashes) is a new worker, which refreshes the shell and its assets.
    version = hashlib.sha256(repr((shell, extras)).encode("utf-8")).hexdigest()[:12]
    body = render_template('sw.js', version=version, shell=shell, extras=extras,
                           scope=url_for('web.index'))
    resp = make_response(body)
    resp.headers['Content-Type'] = 'application/javascript; charset=utf-8'
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Service-Worker-Allowed'] = url_for('web.index')
    return resp


@offline.route("/offline")
def offline_page():
    """What the app shows with no connection: the books kept on this device, read from the
    worker's own index by offline.js, so the page holds nothing of the server's."""
    return render_template('offline.html')
