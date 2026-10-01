# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Cover images, serving book files to readers, and book downloads (including the EPUB container repair).

Routes are attached to the web blueprint; web.py imports this module at its end."""

import os
import mimetypes
import chardet  # dependency of requests
import importlib
import re
import zipfile
import xml.etree.ElementTree as ET

from flask import request, send_from_directory, send_file, make_response, abort
from .cw_login import current_user
from werkzeug.datastructures import Headers

from . import constants
from . import config
from . import calibre_db
from .gdriveutils import getFileFromEbooksFolder, do_gdrive_download
from .helper import get_book_cover, get_series_cover_thumbnail, get_download_link, EXTENSIONS_READER
from .usermanagement import login_required_if_no_ano

# CWA Imports
import time

import sys
sys.path.insert(1, '/app/calibre-web-automated/scripts/')


try:
    from natsort import natsorted as sort
except ImportError:
    sort = sorted  # Just use regular sort then, may cause issues with badly named pages in cbz/cbr files


sql_version = importlib.metadata.version("sqlalchemy")
sqlalchemy_version2 = ([int(x) for x in sql_version.split('.')] >= [2, 0, 0])

_start_time = time.time()

from .web import web, log, download_required, viewer_required


# ################################### Download/Send ##################################################################


@web.route("/cover/<int:book_id>")
@web.route("/cover/<int:book_id>/<string:resolution>")
@login_required_if_no_ano
def get_cover(book_id, resolution=None):
    resolutions = {
        'og': constants.COVER_THUMBNAIL_ORIGINAL,
        'sm': constants.COVER_THUMBNAIL_SMALL,
        'md': constants.COVER_THUMBNAIL_MEDIUM,
        'lg': constants.COVER_THUMBNAIL_LARGE,
    }
    cover_resolution = resolutions.get(resolution, None)
    return get_book_cover(book_id, cover_resolution)


@web.route("/series_cover/<int:series_id>")
@web.route("/series_cover/<int:series_id>/<string:resolution>")
@login_required_if_no_ano
def get_series_cover(series_id, resolution=None):
    resolutions = {
        'og': constants.COVER_THUMBNAIL_ORIGINAL,
        'sm': constants.COVER_THUMBNAIL_SMALL,
        'md': constants.COVER_THUMBNAIL_MEDIUM,
        'lg': constants.COVER_THUMBNAIL_LARGE,
    }
    cover_resolution = resolutions.get(resolution, None)
    return get_series_cover_thumbnail(series_id, cover_resolution)



def _is_valid_container_xml(container_bytes):
    try:
        ET.fromstring(container_bytes)
        return True
    except Exception:
        return False


def _sanitize_container_xml(container_bytes):
    try:
        text = container_bytes.decode("utf-8", errors="replace")
    except Exception:
        return container_bytes

    decl_pattern = re.compile(r"<\?xml[^>]*\?>")
    decls = list(decl_pattern.finditer(text))
    if len(decls) <= 1:
        return container_bytes

    first = decls[0]
    cleaned = text[:first.end()] + decl_pattern.sub("", text[first.end():])
    return cleaned.encode("utf-8")


def _get_fixed_epub_path(book_id, original_path):
    fix_dir = os.path.join(constants.CONFIG_DIR, "epub_fixes")
    try:
        os.makedirs(fix_dir, exist_ok=True)
    except Exception:
        return None

    try:
        mtime = int(os.path.getmtime(original_path))
    except Exception:
        mtime = 0
    return os.path.join(fix_dir, f"{book_id}_{mtime}.epub")


def _repair_epub_container_if_needed(book_id, original_path):
    try:
        with zipfile.ZipFile(original_path, "r") as zin:
            container_bytes = zin.read("META-INF/container.xml")
            if _is_valid_container_xml(container_bytes):
                return None

        fixed_path = _get_fixed_epub_path(book_id, original_path)
        if not fixed_path:
            return None
        if os.path.exists(fixed_path):
            return fixed_path

        temp_path = fixed_path + ".tmp"
        with zipfile.ZipFile(original_path, "r") as zin, zipfile.ZipFile(temp_path, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "META-INF/container.xml":
                    data = _sanitize_container_xml(data)

                zi = zipfile.ZipInfo(item.filename)
                zi.date_time = item.date_time
                zi.compress_type = item.compress_type
                zi.external_attr = item.external_attr
                zi.internal_attr = item.internal_attr
                zi.extra = item.extra
                zi.comment = item.comment
                zout.writestr(zi, data, compress_type=item.compress_type)

        os.replace(temp_path, fixed_path)
        return fixed_path
    except KeyError:
        return None
    except Exception as ex:
        log.error("Failed to repair EPUB container.xml for book %s: %s", book_id, ex)
        return None


def _guard_untrusted_format(response, book_format):
    """/show/ serves uploaded files from Lily's own origin. Formats no reader or the audio
    player fetches (html, mobi, docx, ...) are downloaded, never rendered as a page here."""
    fmt = book_format.lower()
    if fmt.upper() not in EXTENSIONS_READER and fmt not in constants.EXTENSIONS_AUDIO:
        response.headers['Content-Security-Policy'] = "sandbox"
        response.headers['Content-Disposition'] = 'attachment; filename="book.{}"'.format(
            re.sub(r'[^a-z0-9]', '', fmt) or "bin")
    return response


@web.route("/show/<int:book_id>/<book_format>", defaults={'anyname': 'None'})
@web.route("/show/<int:book_id>/<book_format>/<anyname>")
@login_required_if_no_ano
@viewer_required
def serve_book(book_id, book_format, anyname):
    book_format = book_format.split(".")[0]
    return _guard_untrusted_format(make_response(_serve_book_file(book_id, book_format)), book_format)


def _serve_book_file(book_id, book_format):
    # Respect the user's tag / language / custom column restrictions
    book = calibre_db.get_filtered_book(book_id, allow_show_archived=True)
    if not book:
        log.debug("Book %s is not accessible for user %s", book_id, current_user.name)
        abort(404)
    data = calibre_db.get_book_format(book_id, book_format.upper())
    if not data:
        return "File not in Database"
    range_header = request.headers.get('Range', None)

    if config.config_use_google_drive:
        try:
            headers = Headers()
            headers["Content-Type"] = mimetypes.types_map.get('.' + book_format, "application/octet-stream")
            if book_format.upper() == 'TXT':
                headers["Content-Type"] = "text/plain; charset=utf-8"  # re-encoded to UTF-8 while streaming
            if not range_header:
                log.info('Serving book: %s', data.name)
                headers['Accept-Ranges'] = 'bytes'
            df = getFileFromEbooksFolder(book.path, data.name + "." + book_format)
            return do_gdrive_download(df, headers, (book_format.upper() == 'TXT'))
        except AttributeError as ex:
            log.error_or_exception(ex)
            return "File Not Found"
    else:
        if book_format.upper() in ('EPUB', 'KEPUB'):
            original_path = os.path.join(config.get_book_path(), book.path, data.name + "." + book_format)
            fixed_path = _repair_epub_container_if_needed(book_id, original_path)
            if fixed_path:
                response = make_response(send_file(fixed_path, mimetype="application/epub+zip"))
                if not range_header:
                    log.info('Serving repaired book: %s', data.name)
                    response.headers['Accept-Ranges'] = 'bytes'
                return response
        if book_format.upper() == 'TXT':
            log.info('Serving book: %s', data.name)
            try:
                rawdata = open(os.path.join(config.get_book_path(), book.path, data.name + "." + book_format),
                               "rb").read()
                result = chardet.detect(rawdata)
                try:
                    text_data = rawdata.decode(result['encoding']).encode('utf-8')
                except UnicodeDecodeError as e:
                    log.error("Encoding error in text file {}: {}".format(book.id, e))
                    if "surrogate" in e.reason:
                        text_data = rawdata.decode(result['encoding'], 'surrogatepass').encode('utf-8', 'surrogatepass')
                    else:
                        text_data = rawdata.decode(result['encoding'], 'ignore').encode('utf-8', 'ignore')
                response = make_response(text_data)
                response.headers['Content-Type'] = 'text/plain; charset=utf-8'
                return response
            except FileNotFoundError:
                log.error("File Not Found")
                return "File Not Found"
        # enable byte range read of pdf
        response = make_response(
            send_from_directory(os.path.join(config.get_book_path(), book.path), data.name + "." + book_format))
        if not range_header:
            log.info('Serving book: %s', data.name)
            response.headers['Accept-Ranges'] = 'bytes'
        return response


@web.route("/download/<int:book_id>/<book_format>", defaults={'anyname': 'None'})
@web.route("/download/<int:book_id>/<book_format>/<anyname>")
@login_required_if_no_ano
@download_required
def download_link(book_id, book_format, anyname):
    client = "kobo" if "Kobo" in request.headers.get('User-Agent', '') else ""
    return get_download_link(book_id, book_format, client)


@web.route("/reader-sw.js")
def reader_service_worker():
    """The reader's offline service worker (static/js/reading/reader-sw.js).

    Served from the app root rather than /static/ because a worker may only control pages
    at or below its own path; the reader pages register it with scope <root>/read/. Public
    like any static file, so the browser's update check never hits a login redirect."""
    response = make_response(send_from_directory(os.path.join(constants.STATIC_DIR, "js", "reading"),
                                                 "reader-sw.js", mimetype="text/javascript"))
    response.headers["Cache-Control"] = "no-cache"
    return response
