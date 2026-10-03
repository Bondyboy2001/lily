# SPDX-License-Identifier: GPL-3.0-or-later
"""Gzip for text responses (HTML, JSON, CSS, JS, SVG, XML, OPDS feeds).

The WSGI servers don't compress, so every page and script went over the wire raw. Fingerprinted
static files are compressed once and kept in memory; dynamic responses are compressed per request
at a low level, which costs far less than the bytes it saves on anything slower than loopback.
"""

import gzip

from flask import request

_TYPES = ("text/", "application/json", "application/javascript", "application/xml",
          "application/atom+xml", "application/opensearchdescription+xml", "image/svg+xml")
_MIN_SIZE = 500
_MAX_STATIC = 4 * 1024 * 1024
_static_cache = {}  # (path, etag) -> gzipped bytes


def _compressible(response):
    mimetype = response.mimetype or ""
    return mimetype.startswith(_TYPES)


def init_compression(app):
    @app.after_request
    def gzip_response(response):
        if (response.status_code != 200
                or "gzip" not in request.headers.get("Accept-Encoding", "").lower()
                or "Content-Encoding" in response.headers
                or request.headers.get("Range")
                or response.is_streamed and not response.direct_passthrough
                or not _compressible(response)):
            return response

        is_static = request.endpoint == "static"
        if response.direct_passthrough:
            # send_file bodies: only worth buffering for fingerprinted static assets of sane size
            if not is_static or (response.content_length or 0) > _MAX_STATIC:
                return response
            key = (request.path, response.headers.get("ETag"))
            data = _static_cache.get(key)
            if data is None:
                response.direct_passthrough = False
                data = gzip.compress(response.get_data(), compresslevel=6)
                _static_cache[key] = data
        else:
            raw = response.get_data()
            if len(raw) < _MIN_SIZE:
                return response
            data = gzip.compress(raw, compresslevel=4)

        response.set_data(data)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(data))
        response.vary.add("Accept-Encoding")
        return response
