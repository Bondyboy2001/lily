# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Content-Security-Policy and the other security headers added to every response.

web.py registers ``add_security_headers`` on the app once. Inline scripts and styles still
need 'unsafe-inline'; moving them to nonces is deferred."""

from flask import request

from . import config

# Pages whose scripts build functions from strings (underscore templates in the metadata
# search, the djvu and unrar reader engines). Everything else runs without 'unsafe-eval'.
_EVAL_ENDPOINTS = frozenset({"web.read_book", "edit-book.show_edit_book"})

# Directives that hold on every page: no <base> hijacking, forms only post back to Lily,
# only Lily may frame its pages, and no plugins.
_FIXED_DIRECTIVES = "base-uri 'self'; form-action 'self'; frame-ancestors 'self'; object-src 'none'"


def build_csp(endpoint):
    default_src = ([host.strip() for host in config.config_trustedhosts.split(',') if host] +
                   ["'self'", "'unsafe-inline'"])
    if endpoint in _EVAL_ENDPOINTS:
        default_src.append("'unsafe-eval'")
    csp = "default-src " + ' '.join(default_src)
    if endpoint == "web.read_book" and config.config_use_google_drive:
        csp += " blob: "
    csp += "; font-src 'self' data:"
    if endpoint == "web.read_book":
        csp += " blob: "
    csp += "; img-src 'self' data:"
    if endpoint == "edit-book.show_edit_book" or config.config_use_google_drive:
        csp += " *"
    if endpoint == "web.read_book":
        csp += " blob: ; style-src-elem 'self' blob: 'unsafe-inline'"
    return csp + "; " + _FIXED_DIRECTIVES + ";"


def add_security_headers(resp):
    # A route may set its own, stricter policy (book files served by /show/ are sandboxed)
    resp.headers.setdefault('Content-Security-Policy', build_csp(request.endpoint))
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    resp.headers['Referrer-Policy'] = 'same-origin'
    resp.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return resp
