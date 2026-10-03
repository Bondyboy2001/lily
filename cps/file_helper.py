# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

from tempfile import gettempdir
import os
import shutil
import zipfile
import mimetypes
from io import BytesIO

from . import logger

log = logger.create()

try:
    import magic
    error = None
except ImportError as e:
    error = f"Cannot import python-magic, checking uploaded file metadata will not work: {e}"


def get_mimetype(ext):
    """Return the mimetype for a file extension (including the leading dot).

    Raises KeyError for unknown extensions; validate_mime_type catches that and
    skips the extension. The formats Lily supports are registered with
    `mimetypes.add_type` in cps/__init__.py.
    """
    return mimetypes.types_map[ext]


def get_temp_dir():
    tmp_dir = os.path.join(gettempdir(), 'calibre_web')
    if not os.path.isdir(tmp_dir):
        os.mkdir(tmp_dir)
    return tmp_dir


def del_temp_dir():
    tmp_dir = os.path.join(gettempdir(), 'calibre_web')
    shutil.rmtree(tmp_dir)


def validate_mime_type(file_buffer, allowed_extensions):
    if error:
        log.error(error)
        return False
    mime = magic.Magic(mime=True)
    allowed_mimetypes = []
    for x in allowed_extensions:
        try:
            allowed_mimetypes.append(get_mimetype("." + x))
        except KeyError:
            log.error(f"Unkown mimetype for Extension: {x}")
    tmp_mime_type = mime.from_buffer(file_buffer.read())
    file_buffer.seek(0)
    if any(mime_type in tmp_mime_type for mime_type in allowed_mimetypes):
        return True
    # Some epubs show up as zip mimetypes
    if "zip" in tmp_mime_type:
        try:
            with zipfile.ZipFile(BytesIO(file_buffer.read()), 'r') as epub:
                file_buffer.seek(0)
                if "mimetype" in epub.namelist():
                    return True
        except (zipfile.BadZipFile, OSError, ValueError):
            file_buffer.seek(0)
    log.error(f"Mimetype '{tmp_mime_type}' not found in allowed types")
    return False
