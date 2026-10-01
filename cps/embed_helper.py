# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

from uuid import uuid4
import os

from .file_helper import get_temp_dir
from .subproc_wrapper import process_communicate
from . import logger, config
from .constants import SUPPORTED_CALIBRE_BINARIES

log = logger.create()

# Formats the metadata enforcer (scripts/cover_enforcer.py) already writes metadata into
# whenever it changes, so a download can be served as stored.
ENFORCED_FORMATS = ("epub", "azw3")


def download_needs_calibre_export(book_format):
    """Whether a download in this format needs `calibredb export` to embed current metadata.

    False for EPUB/AZW3 while automatic metadata enforcement is on: the file on disk already
    carries it, and the export (a separate calibre process, seconds on a NAS) is skipped.
    """
    if (book_format or "").lower() not in ENFORCED_FORMATS:
        return True
    try:
        from .render_template import get_request_cwa_db
        return not get_request_cwa_db().cwa_settings.get("auto_metadata_enforcement", 1)
    except Exception as ex:
        log.debug("Could not read the metadata enforcement setting: %s", ex)
        return True


def do_calibre_export(book_id, book_format):
    try:
        quotes = [4, 6]
        tmp_dir = get_temp_dir()
        calibredb_binarypath = get_calibre_binarypath("calibredb")
        temp_file_name = str(uuid4())
        my_env = os.environ.copy()
        if config.config_calibre_split:
            my_env['CALIBRE_OVERRIDE_DATABASE_PATH'] = os.path.join(config.config_calibre_dir, "metadata.db")
        library_path = config.get_book_path()
        opf_command = [calibredb_binarypath, 'export', '--dont-write-opf', '--with-library', library_path,
                       '--to-dir', tmp_dir, '--formats', book_format, "--template", "{}".format(temp_file_name),
                       str(book_id)]
        # off the gevent hub: calibredb takes seconds and would otherwise stall every request
        _, _, err = process_communicate(opf_command, quotes, my_env)
        if err:
            log.error('Metadata embedder encountered an error: %s', err)

        # calibredb export with --template may create either:
        # 1. A subdirectory with the template name containing the file
        # 2. A file directly with a modified name

        # First check if a subdirectory was created
        export_dir = os.path.join(tmp_dir, temp_file_name)
        if os.path.isdir(export_dir):
            # Look for the book file with the specified format
            for filename in os.listdir(export_dir):
                if filename.lower().endswith('.' + book_format.lower()):
                    # Found the exported file - return the directory and the filename without extension
                    actual_filename = os.path.splitext(filename)[0]
                    return export_dir, actual_filename

            log.warning(f'No {book_format} file found in export directory: {export_dir}')
        else:
            # No subdirectory - look for files directly in tmp_dir
            # STRICT CHECK: Only look for the file we requested
            expected_filename = temp_file_name + '.' + book_format.lower()
            for filename in os.listdir(tmp_dir):
                if filename.lower() == expected_filename.lower():
                    actual_filename = os.path.splitext(filename)[0]
                    return tmp_dir, actual_filename

            log.warning(f'No file named {expected_filename} found in {tmp_dir}')

        # Fallback to original behavior
        return tmp_dir, temp_file_name
    except OSError as ex:
        # ToDo real error handling
        log.error_or_exception(ex)
        return None, None


def get_calibre_binarypath(binary):
    binariesdir = config.config_binariesdir
    if binariesdir:
        try:
            return os.path.join(binariesdir, SUPPORTED_CALIBRE_BINARIES[binary])
        except KeyError:
            log.error("Binary not supported by Lily: %s", SUPPORTED_CALIBRE_BINARIES[binary])
            pass
    return ""
