# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Shared helpers: cover and file paths, downloads, validation, thumbnails and archive handling."""

import os
import io
import mimetypes
import time
import re
import regex
import shutil
import socket
import platform
from datetime import datetime, timezone
import requests
import unidecode

from flask import send_from_directory, make_response, abort
from flask_babel import gettext as _
from .cw_login import current_user
from sqlalchemy.sql.expression import true, false, and_, or_, func
from sqlalchemy.exc import InvalidRequestError, OperationalError
from werkzeug.datastructures import Headers
from urllib.parse import quote

from . import cw_advocate
from .cw_advocate.exceptions import UnacceptableAddressException

from . import calibre_db, cli_param
from .string_helper import strip_whitespaces
from . import logger, config, db, ub, fs
from .constants import (STATIC_DIR as _STATIC_DIR, CACHE_TYPE_THUMBNAILS, THUMBNAIL_TYPE_COVER, EXTENSIONS_AUDIO, is_unknown_author)

# Track books with pending thumbnail generation to prevent duplicate tasks
_pending_thumbnail_books = set()

import sys
sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from cwa_db import CWA_DB
from .services.worker import WorkerThread
from .tasks.thumbnail import TaskClearCoverThumbnailCache, TaskGenerateCoverThumbnails
from .embed_helper import do_calibre_export

log = logger.create()


try:
    from wand.image import Image
    from wand.exceptions import MissingDelegateError, BlobError
    use_IM = True
except (ImportError, RuntimeError) as e:
    log.debug('Cannot import Image, generating covers from non jpg files will not work: %s', e)
    use_IM = False
    MissingDelegateError = BaseException


# Check if a reader is existing for any of the book formats, if not, return empty list, otherwise return
# list with supported formats
READER_PREFERENCE = ('epub', 'pdf', 'djvu', 'djv')


def check_read_formats(entry):
    supported = READER_PREFERENCE + tuple(sorted(EXTENSIONS_AUDIO))
    present = {ele.format.lower() for ele in iter(entry.data)}
    return [fmt for fmt in supported if fmt in present]


def get_valid_filename(value, replace_whitespace=True, chars=128):
    """
    Return a sanitized filename (max length chars) mirroring shared sanitizer.
    Uses cps.utils.filename_sanitizer if available; falls back to legacy logic if import fails.
    """
    get_valid_filename_shared = None
    try:
        from cps.utils.filename_sanitizer import get_valid_filename_shared  # type: ignore
    except ModuleNotFoundError:
        # Attempt path adjustment (similar to scripts/cover_enforcer)
        try:  # pragma: no cover
            import sys as _sys
            import os as _os
            project_root = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), '..'))
            if project_root not in _sys.path:
                _sys.path.insert(0, project_root)
            from cps.utils.filename_sanitizer import get_valid_filename_shared  # type: ignore
        except Exception as ex:  # pragma: no cover
            log.debug('Shared filename sanitizer import failed (%s); using legacy implementation', ex)
            get_valid_filename_shared = None  # type: ignore
    except Exception as ex:  # pragma: no cover
        log.debug('Unexpected sanitizer import error (%s); using legacy implementation', ex)
        get_valid_filename_shared = None  # type: ignore

    if callable(get_valid_filename_shared):  # type: ignore
        try:
            return get_valid_filename_shared(
                value,
                replace_whitespace=replace_whitespace,
                chars=chars,
                unicode_filename=bool(config.config_unicode_filename)
            )
        except Exception as ex:  # pragma: no cover
            log.debug('Shared sanitizer execution failed (%s); falling back to legacy logic', ex)

    # Legacy local implementation (must mirror shared logic)
    if not isinstance(value, str):
        value = str(value) if value is not None else ""
    if value[-1:] == '.':
        value = value[:-1]+'_'
    value = value.replace("/", "_").replace(":", "_").strip('\0')
    if config.config_unicode_filename:
        try:
            value = (unidecode.unidecode(value))
        except Exception:  # pragma: no cover
            pass
    if replace_whitespace:
        value = re.sub(r'[*+:\\"/<>?]+', '_', value, flags=re.U)
        value = re.sub(r'[|]+', ',', value, flags=re.U)
    value = strip_whitespaces(value.encode('utf-8')[:chars].decode('utf-8', errors='ignore'))
    if not value:
        raise ValueError("Filename cannot be empty")
    return value


def split_authors(values):
    authors_list = []
    for value in values:
        authors = re.split('[&;]', value)
        for author in authors:
            commas = author.count(',')
            if commas == 1:
                author_split = author.split(',')
                authors_list.append(strip_whitespaces(author_split[1]) + ' ' + strip_whitespaces(author_split[0]))
            elif commas > 1:
                authors_list.extend([strip_whitespaces(x) for x in author.split(',')])
            else:
                authors_list.append(strip_whitespaces(author))
    return authors_list


def get_sorted_author(value):
    value2 = None
    try:
        if ',' not in value:
            regexes = [r"^(JR|SR)\.?$", r"^I{1,3}\.?$", r"^IV\.?$"]
            combined = "(" + ")|(".join(regexes) + ")"
            value = value.split(" ")
            if re.match(combined, value[-1].upper()):
                if len(value) > 1:
                    value2 = value[-2] + ", " + " ".join(value[:-2]) + " " + value[-1]
                else:
                    value2 = value[0]
            elif len(value) == 1:
                value2 = value[0]
            else:
                value2 = value[-1] + ", " + " ".join(value[:-1])
        else:
            value2 = value
    except Exception as ex:
        log.error("Sorting author %s failed: %s", value, ex)
        if isinstance(list, value2):
            value2 = value[0]
        else:
            value2 = value
    return value2


def edit_book_read_status(book_id, read_status=None):
    if not config.config_read_column:
        book = ub.session.query(ub.ReadBook).filter(and_(ub.ReadBook.user_id == int(current_user.id),
                                                         ub.ReadBook.book_id == book_id)).first()
        if book:
            if read_status is None:
                if book.read_status == ub.ReadBook.STATUS_FINISHED:
                    book.read_status = ub.ReadBook.STATUS_UNREAD
                else:
                    book.read_status = ub.ReadBook.STATUS_FINISHED
            else:
                book.read_status = ub.ReadBook.STATUS_FINISHED if read_status else ub.ReadBook.STATUS_UNREAD
        else:
            read_book = ub.ReadBook(user_id=current_user.id, book_id=book_id)
            read_book.read_status = ub.ReadBook.STATUS_FINISHED
            book = read_book
        ub.session.merge(book)
        ub.session_commit("Book {} readbit toggled".format(book_id))
    else:
        try:
            calibre_db.create_functions(config)
            book = calibre_db.get_filtered_book(book_id, True)
            book_read_status = getattr(book, 'custom_column_' + str(config.config_read_column))
            if len(book_read_status):
                if read_status is None:
                    book_read_status[0].value = not book_read_status[0].value
                else:
                    book_read_status[0].value = read_status is True
                calibre_db.session.commit()
            else:
                cc_class = db.cc_classes[config.config_read_column]
                new_cc = cc_class(value=read_status or 1, book=book_id)
                calibre_db.session.add(new_cc)
                calibre_db.session.commit()
        except (KeyError, AttributeError, IndexError):
            log.error(
                "Custom Column No.{} does not exist in calibre database".format(config.config_read_column))
            return "Custom Column No.{} does not exist in calibre database".format(config.config_read_column)
        except (OperationalError, InvalidRequestError) as ex:
            calibre_db.session.rollback()
            log.error("Read status could not set: {}".format(ex))
            return _("Read status could not set: {}".format(ex.orig))
    return ""


def _same_entry(a, b):
    """True when two different spellings name one file or folder, as a case-only change does
    on a case-insensitive disk (macOS, a colima bind mount)."""
    try:
        return a != b and os.path.samefile(a, b)
    except OSError:
        return False


def _rename_in_place(src, dst):
    """Give src the spelling dst when both name the same entry. Goes through a temporary name,
    since some filesystems ignore a rename that only changes case."""
    temp = dst + ".lily-rename"
    os.rename(src, temp)
    os.rename(temp, dst)


def rename_all_files_on_change(one_book, new_path, old_path, all_new_name):
    for file_format in one_book.data:
        if not os.path.exists(new_path):
            os.makedirs(new_path)

        old_file = os.path.join(old_path, file_format.name + '.' + file_format.format.lower())
        new_file = os.path.join(new_path, all_new_name + '.' + file_format.format.lower())

        # Skip if source and destination are the same
        if old_file == new_file:
            log.debug("Skipping file rename - source and destination are identical: %s", old_file)
            continue

        # Check if source file exists
        if not os.path.exists(old_file):
            log.warning("Source file not found for rename: %s", old_file)
            # Check if the file already has the new name (perhaps from a previous partial operation)
            if os.path.exists(new_file):
                log.info("File already exists at destination: %s", new_file)
                file_format.name = all_new_name
                continue
            else:
                log.error("Neither old nor new file exists - cannot rename %s to %s", old_file, new_file)
                continue

        # A case-only change on a case-insensitive disk: the "destination" is this same file, so
        # removing it would delete the book
        if _same_entry(old_file, new_file):
            _rename_in_place(old_file, new_file)
            file_format.name = all_new_name
            continue

        # Check if destination already exists
        if os.path.exists(new_file) and old_file != new_file:
            log.warning("Destination file already exists, will overwrite: %s", new_file)
            try:
                os.remove(new_file)
            except OSError as ex:
                log.error("Could not remove existing destination file %s: %s", new_file, ex)

        # Attempt to rename the file
        try:
            shutil.move(old_file, new_file)
            log.debug("Successfully renamed %s to %s", old_file, new_file)
        except OSError as ex:
            log.error("Failed to rename file from %s to %s: %s", old_file, new_file, ex)
            # Try copy+delete as fallback for permission issues (e.g., network shares)
            try:
                log.info("Attempting copy+delete fallback for %s", old_file)
                shutil.copy2(old_file, new_file)
                os.remove(old_file)
                log.info("Successfully copied and removed old file: %s", old_file)
            except (OSError, IOError) as fallback_ex:
                log.error("Copy+delete fallback also failed for %s: %s", old_file, fallback_ex)
                # Don't update the database name if we failed to rename the file
                continue

        # change name in Database
        file_format.name = all_new_name


def rename_author_path(first_author, old_author_dir, renamed_author, calibre_path=""):
    # Create new_author_dir from parameter or from database
    # Create new title_dir from database and add id
    new_authordir = get_valid_filename(first_author, chars=96)
    new_author_rename_dir = get_valid_filename(renamed_author, chars=96)
    if os.path.isdir(os.path.join(calibre_path, old_author_dir)):
        old_author_path = os.path.join(calibre_path, old_author_dir)
        new_author_path = os.path.join(calibre_path, new_author_rename_dir)
        try:
            os.rename(old_author_path, new_author_path)
        except OSError:
            try:
                shutil.move(old_author_path, new_author_path)
            except OSError as ex:
                log.error("Rename author from: %s to %s: %s", old_author_path, new_author_path, ex)
                log.error_or_exception(ex)
                raise Exception(_("Rename author from: '%(src)s' to '%(dest)s' failed with error: %(error)s",
                         src=old_author_path, dest=new_author_path, error=str(ex)))
    return new_authordir

# Moves files in file storage during author/title rename, or from temp dir to file storage
def update_dir_structure_file(book_id, calibre_path, original_filepath, new_author, db_filename, book=None):
    # get book database entry from id (or take the caller's, from its own session),
    # if original path overwrite source with original_filepath
    local_book = book or calibre_db.get_book(book_id)
    if original_filepath:
        path = original_filepath
    else:
        path = os.path.join(calibre_path, local_book.path)

    # Create (current) author_dir and title_dir from database
    author_dir = local_book.path.split('/')[0]
    title_dir = local_book.path.split('/')[1]

    new_title_dir = get_valid_filename(local_book.title, chars=96) + " (" + str(book_id) + ")"
    if new_author:
        new_author_dir = get_valid_filename(new_author, chars=96)
    else:
        new_author = new_author_dir = author_dir

    if title_dir != new_title_dir or author_dir != new_author_dir or original_filepath:
        error = move_files_on_change(calibre_path,
                                     new_author_dir,
                                     new_title_dir,
                                     local_book,
                                     db_filename,
                                     original_filepath,
                                     path)
        if error:
            # The folder stayed where it was; renaming files in the new one would only create it
            return error
        new_path = os.path.join(calibre_path, new_author_dir, new_title_dir).replace('\\', '/')
        all_new_name = get_valid_filename(local_book.title, chars=42) + ' - ' \
                       + get_valid_filename(new_author, chars=42)
        # Book folder already moved, only files need to be renamed
        rename_all_files_on_change(local_book, new_path, new_path, all_new_name)
    return False


def move_files_on_change(calibre_path, new_author_dir, new_titledir, localbook, db_filename, original_filepath, path):
    new_path = os.path.join(calibre_path, new_author_dir, new_titledir)
    try:
        if original_filepath:
            if not os.path.isdir(new_path):
                os.makedirs(new_path)
            try:
                shutil.move(original_filepath, os.path.join(new_path, db_filename))
            except OSError as ex:
                log.error("Rename title from %s to %s failed with error: %s, trying copy+delete fallback",
                         path, new_path, ex)
                try:
                    shutil.copy2(original_filepath, os.path.join(new_path, db_filename))
                    os.remove(original_filepath)
                except (OSError, IOError) as fallback_ex:
                    log.error("Copy+delete fallback also failed: %s", fallback_ex)
                    raise
            log.debug("Moving title: %s to %s", original_filepath, new_path)
        else:
            # Check new path is not valid path
            if not os.path.exists(new_path):
                # move original path to new path
                log.debug("Moving title: %s to %s", path, new_path)
                try:
                    shutil.move(path, new_path)
                except OSError as ex:
                    log.error("Failed to move directory %s to %s: %s, trying copy tree approach", path, new_path, ex)
                    # Fallback: try to copy tree and then remove original
                    try:
                        shutil.copytree(path, new_path, dirs_exist_ok=True)
                        shutil.rmtree(path)
                    except (OSError, IOError) as fallback_ex:
                        log.error("Copy tree fallback also failed: %s", fallback_ex)
                        raise
            elif _same_entry(path, new_path):
                # A case-only change on a case-insensitive disk: rename the folder itself rather
                # than "merging" it into itself
                log.debug("Renaming title in place: %s to %s", path, new_path)
                _rename_in_place(path, new_path)
            else:  # path is valid copy only files to new location (merge)
                log.info("Moving title: %s into existing: %s", path, new_path)
                # Take all files and subfolder from old path (strange command)
                for dir_name, __, file_list in os.walk(path):
                    for file in file_list:
                        src_file = os.path.join(dir_name, file)
                        dest_dir = new_path + dir_name[len(path):]
                        dest_file = os.path.join(dest_dir, file)

                        # Create destination directory if it doesn't exist
                        if not os.path.exists(dest_dir):
                            os.makedirs(dest_dir)

                        try:
                            shutil.move(src_file, dest_file)
                        except OSError as ex:
                            log.error("Failed to move file %s to %s: %s, trying copy+delete", src_file, dest_file, ex)
                            try:
                                shutil.copy2(src_file, dest_file)
                                os.remove(src_file)
                            except (OSError, IOError) as fallback_ex:
                                log.error("Copy+delete fallback failed for %s: %s", src_file, fallback_ex)
                                # Continue with other files even if one fails
                                continue

            # Try to remove old author directory if empty
            if os.path.exists(os.path.split(path)[0]) and not os.listdir(os.path.split(path)[0]):
                try:
                    shutil.rmtree(os.path.split(path)[0])
                except (IOError, OSError) as ex:
                    log.error("Deleting authorpath for book %s failed: %s", localbook.id, ex)

        # change location in database to new author/title path
        localbook.path = os.path.join(new_author_dir, new_titledir).replace('\\', '/')
    except OSError as ex:
        log.error_or_exception("Rename title from {} to {} failed with error: {}".format(path, new_path, ex))
        return _("Rename title from: '%(src)s' to '%(dest)s' failed with error: %(error)s",
                 src=path, dest=new_path, error=str(ex))
    return False


def uniq(inpt):
    output = []
    inpt = [" ".join(inp.split()) for inp in inpt]
    for x in inpt:
        if x not in output:
            output.append(x)
    return output


def check_email(email):
    email = valid_email(email)
    if ub.session.query(ub.User).filter(func.lower(ub.User.email) == email.lower()).first():
        log.error("Found an existing account for this Email address")
        raise Exception(_("Found an existing account for this Email address"))
    return email


def check_username(username):
    username = strip_whitespaces(username)
    if ub.session.query(ub.User).filter(func.lower(ub.User.name) == username.lower()).scalar():
        log.error("This username is already taken")
        raise Exception(_("This username is already taken"))
    return username


def valid_email(emails):
    valid_emails = []
    for email in emails.split(','):
        email = strip_whitespaces(email)
        # if email is not deleted
        if email:
            # Regex according to https://developer.mozilla.org/en-US/docs/Web/HTML/Element/input/email#validation
            if not re.search(r"^[\w.!#$%&'*+\\/=?^_`{|}~-]+@[\w](?:[\w-]{0,61}[\w])?(?:\.[\w](?:[\w-]{0,61}[\w])?)*$",
                             email):
                log.error("Invalid Email address format for {}".format(email))
                raise Exception(_("Invalid Email address format"))
            valid_emails.append(email)
    return ",".join(valid_emails)


def valid_password(check_password):
    if config.config_password_policy:
        verify = ""
        if config.config_password_min_length > 0:
            verify += r"^(?=.{" + str(config.config_password_min_length) + ",}$)"
        if config.config_password_number:
            verify += r"(?=.*?\d)"
        if config.config_password_lower:
            verify += r"(?=.*?[\p{Ll}])"
        if config.config_password_upper:
            verify += r"(?=.*?[\p{Lu}])"
        if config.config_password_character:
            verify += r"(?=.*?[\p{Letter}])"
        if config.config_password_special:
            verify += r"(?=.*?[^\p{Letter}\s0-9])"
        match = regex.match(verify, check_password)
        if not match:
            raise Exception(_("Password doesn't comply with password validation rules"))
    return check_password
# ################################# External interface #################################


def update_dir_structure(book_id,
                         calibre_path,
                         first_author=None,     # change author of book to this author
                         original_filepath=None,
                         db_filename=None,
                         book=None):            # the book row, when the caller has its own session
    return update_dir_structure_file(book_id,
                                     calibre_path,
                                     original_filepath,
                                     first_author,
                                     db_filename,
                                     book)


def get_cover_on_failure():
    try:
        return send_from_directory(_STATIC_DIR, "generic_cover.svg")
    except PermissionError:
        log.error("No permission to access generic_cover.svg file.")
        abort(403)


def get_book_cover(book_id, resolution=None):
    # Only id/has_cover/path are needed to serve a cover; avoid building a full
    # Books row with all its eager-loaded relationships. Same permission filter.
    book = calibre_db.get_filtered_book_cover_info(book_id)
    return get_book_cover_internal(book, resolution=resolution)


# One year; cover URLs carry a ?c=<last_modified> cache-buster (see jinjia.get_cover_srcset)
_COVER_CACHE_MAX_AGE = 31536000


def _apply_cover_cache_headers(resp):
    """Mark a real (non-fallback) cover response as long-lived cacheable when
    the request carries the ``c`` cache-buster param. Private, since covers are
    served to authenticated users."""
    try:
        from flask import has_request_context, request
        if (resp is not None and has_request_context()
                and request.endpoint == 'web.get_cover' and request.args.get('c')):
            resp.cache_control.no_cache = None
            resp.cache_control.public = False
            resp.cache_control.private = True
            resp.cache_control.max_age = _COVER_CACHE_MAX_AGE
            resp.expires = int(time.time() + _COVER_CACHE_MAX_AGE)
    except Exception as ex:
        log.debug('Failed to set cover cache headers: %s', ex)
    return resp


def get_book_cover_internal(book, resolution=None):
    """Serve book cover with improved thumbnail generation fallback.

    When a thumbnail is requested but missing, generate it synchronously
    instead of falling back to the original cover.jpg.
    """
    if book and book.has_cover:

        # Send the book cover thumbnail if it exists in cache
        if resolution:
            cache = fs.FileSystem()
            # Check for both webp and jpg thumbnails, generate missing ones
            thumbs = get_book_cover_thumbnails_by_formats(book, resolution, ('webp', 'jpg'))
            webp_thumb = thumbs.get('webp')
            jpg_thumb = thumbs.get('jpg')

            # Check if files actually exist on disk
            webp_exists = webp_thumb and cache.get_cache_file_exists(webp_thumb.filename, CACHE_TYPE_THUMBNAILS)
            jpg_exists = jpg_thumb and cache.get_cache_file_exists(jpg_thumb.filename, CACHE_TYPE_THUMBNAILS)

            # Generate missing thumbnails on-demand
            if not webp_exists or not jpg_exists:
                try:
                    if use_IM:
                        from .tasks.thumbnail import TaskGenerateCoverThumbnails
                        from .services.worker import WorkerThread

                        # Queue thumbnail generation task if not already pending (prevents duplicate tasks)
                        if book.id not in _pending_thumbnail_books:
                            thumbnail_task = TaskGenerateCoverThumbnails(book_id=book.id)
                            try:
                                WorkerThread.add(None, thumbnail_task, hidden=True)
                                # CRITICAL: Only add to pending set AFTER successful queue
                                _pending_thumbnail_books.add(book.id)
                                log.debug(f'Queued background thumbnail generation for book {book.id}')
                            except Exception as queue_ex:
                                # If queueing fails, don't add to pending set
                                log.error(f'Failed to queue thumbnail task for book {book.id}: {queue_ex}')

                        # Note: Thumbnails will be generated in background
                        # Current request will fall back to serving original cover.jpg
                except Exception as ex:
                    log.debug(f'Failed to prepare thumbnail generation for book {book.id}: {ex}')

            thumbnail_to_serve = webp_thumb if webp_exists else (jpg_thumb if jpg_exists else None)
            if thumbnail_to_serve:
                return _apply_cover_cache_headers(
                    send_from_directory(cache.get_cache_file_dir(thumbnail_to_serve.filename, CACHE_TYPE_THUMBNAILS),
                                        thumbnail_to_serve.filename))

        # When a thumbnail was requested but not available yet, it may be generated in the
        # background; don't pin the full-size fallback in the browser cache for a year then.
        cover_is_final = not (resolution and use_IM)

        cover_file_path = os.path.join(config.get_book_path(), book.path)
        if os.path.isfile(os.path.join(cover_file_path, "cover.jpg")):
            resp = send_from_directory(cover_file_path, "cover.jpg")
            return _apply_cover_cache_headers(resp) if cover_is_final else resp
        else:
            return get_cover_on_failure()
    else:
        return get_cover_on_failure()


def get_book_cover_thumbnails_by_formats(book, resolution, formats):
    """Fetch the cover thumbnails of ``book`` at ``resolution`` for several formats
    in a single query. Returns {format: Thumbnail}; the first matching row per
    format wins (same as .first() on a per-format query)."""
    result = {}
    if book and book.has_cover:
        rows = (ub.session
                .query(ub.Thumbnail)
                .filter(ub.Thumbnail.type == THUMBNAIL_TYPE_COVER)
                .filter(ub.Thumbnail.entity_id == book.id)
                .filter(ub.Thumbnail.resolution == resolution)
                .filter(ub.Thumbnail.format.in_(list(formats)))
                .filter(or_(ub.Thumbnail.expiration.is_(None), ub.Thumbnail.expiration > datetime.now(timezone.utc)))
                .all())
        for row in rows:
            result.setdefault(row.format, row)
    return result


# saves book cover from url
def _get_cover_download_limit():
    default_mb = 15
    max_mb = default_mb
    try:
        cwa_db = CWA_DB()
        max_mb = int(cwa_db.cwa_settings.get("cover_download_max_mb", default_mb))
    except Exception:
        max_mb = default_mb

    env_override = os.getenv("CWA_COVER_DOWNLOAD_MAX_BYTES")
    if env_override and env_override.isdigit():
        max_bytes = int(env_override)
        return max_bytes, round(max_bytes / (1024 * 1024), 1)

    max_mb = max(1, min(200, max_mb))
    return max_mb * 1024 * 1024, max_mb


def save_cover_from_url(url, book_path):
    max_cover_bytes, max_cover_mb = _get_cover_download_limit()
    img = None
    download_start = time.monotonic()
    try:
        fetch = requests.get if cli_param.allow_localhost else cw_advocate.get
        # Follow redirects by hand so every hop goes back through advocate's address check
        # (e.g. covers.openlibrary.org answers with a 302 to archive.org)
        for _hop in range(6):
            img = fetch(url, timeout=(10, 30), allow_redirects=False, stream=True)
            if not img.is_redirect:
                break
            url = requests.compat.urljoin(url, img.headers["location"])
            img.close()
        else:
            log.error("Cover download exceeded redirect limit")
            return False, _("Error Downloading Cover")
        img.raise_for_status()

        content_length = img.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > max_cover_bytes:
            return False, _("Cover image exceeds maximum size of %(size)s MB", size=max_cover_mb)

        content = bytearray()
        for chunk in img.iter_content(chunk_size=8192):
            if not chunk:
                continue
            content.extend(chunk)
            if len(content) > max_cover_bytes:
                return False, _("Cover image exceeds maximum size of %(size)s MB", size=max_cover_mb)
        img._content = bytes(content)
        log.debug("Cover download ok: %s bytes in %.3fs", len(img._content), time.monotonic() - download_start)
        return save_cover(img, book_path)
    except (socket.gaierror,
            requests.exceptions.HTTPError,
            requests.exceptions.InvalidURL,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout) as ex:
        # "Invalid host" can be the result of a redirect response
        log.error(u'Cover Download Error %s', ex)
        return False, _("Error Downloading Cover")
    except MissingDelegateError as ex:
        log.info(u'File Format Error %s', ex)
        return False, _("Cover Format Error")
    except UnacceptableAddressException:
        log.error("Localhost or local network was accessed for cover upload")
        return False, _("You are not allowed to access localhost or the local network for cover uploads")
    finally:
        try:
            if img is not None:
                img.close()
        except Exception:
            pass


def save_cover_from_filestorage(filepath, saved_filename, img):
    # check if file path exists, otherwise create it, copy file to calibre path and delete temp file
    if not os.path.exists(filepath):
        try:
            os.makedirs(filepath)
        except OSError:
            log.error("Failed to create path for cover")
            return False, _("Failed to create path for cover")
    try:
        # upload of jpg file without wand
        if isinstance(img, requests.Response):
            with open(os.path.join(filepath, saved_filename), 'wb') as f:
                f.write(img.content)
        else:
            if hasattr(img, "metadata"):
                # upload of jpg/png... via url
                img.save(filename=os.path.join(filepath, saved_filename))
                img.close()
            else:
                # upload of jpg/png... from hdd
                img.save(os.path.join(filepath, saved_filename))
    except (IOError, OSError):
        log.error("Cover-file is not a valid image file, or could not be stored")
        return False, _("Cover-file is not a valid image file, or could not be stored")
    return True, None


# saves book cover to the library folder
def save_cover(img, book_path):
    content_type = img.headers.get('content-type')

    # Clean content-type by removing charset and other parameters
    if content_type:
        separator = ';' if ';' in content_type else ',' if ',' in content_type else None
        if separator:
            content_type = content_type.split(separator)[0].strip()

    if use_IM:
        if content_type not in ('image/jpeg', 'image/jpg', 'image/png', 'image/webp', 'image/bmp'):
            log.error("Only jpg/jpeg/png/webp/bmp files are supported as coverfile")
            return False, _("Only jpg/jpeg/png/webp/bmp files are supported as coverfile")
        # Skip conversion for JPEG to avoid unnecessary ImageMagick work
        if content_type not in ('image/jpeg', 'image/jpg'):
            # convert to jpg because calibre only supports jpg
            try:
                if hasattr(img, 'stream'):
                    imgc = Image(blob=img.stream)
                else:
                    imgc = Image(blob=io.BytesIO(img.content))
                imgc.format = 'jpeg'
                imgc.transform_colorspace("srgb")
                img = imgc
            except (BlobError, MissingDelegateError):
                log.error("Invalid cover file content")
                return False, _("Invalid cover file content")
    else:
        if content_type not in ['image/jpeg', 'image/jpg']:
            log.error("Only jpg/jpeg files are supported as coverfile")
            return False, _("Only jpg/jpeg files are supported as coverfile")

    return save_cover_from_filestorage(os.path.join(config.get_book_path(), book_path), "cover.jpg", img)


def save_cover_with_thumbnail_update(img, book_path, book_id=None):
    """Save cover and force thumbnail regeneration."""
    result, message = save_cover(img, book_path)

    # If cover save was successful and we have a book_id, force thumbnail regeneration
    # Use replace_cover_thumbnail_cache to ensure fresh thumbnails even if generation is pending
    if result and book_id:
        replace_cover_thumbnail_cache(book_id)

    return result, message


def do_download_file(book, book_format, data, headers):
    book_name = data.name
    download_name = filename = None

    filename = os.path.join(config.get_book_path(), book.path)
    if not os.path.isfile(os.path.join(filename, book_name + "." + book_format)):
        # ToDo: improve error handling
        log.error('File not found: %s', os.path.join(filename, book_name + "." + book_format))

    if config.config_binariesdir and config.config_embed_metadata:
        filename, download_name = do_calibre_export(book.id, book_format)

        # Rename the exported file to match the expected download name (from Content-Disposition)
        if filename and download_name:
            uuid_file = os.path.join(filename, download_name + "." + book_format)
            expected_file = os.path.join(filename, book_name + "." + book_format)

            if os.path.exists(uuid_file) and uuid_file != expected_file:
                try:
                    # Remove the target file if it already exists
                    if os.path.exists(expected_file):
                        os.remove(expected_file)
                    # Rename UUID file to expected name
                    os.rename(uuid_file, expected_file)
                    download_name = book_name
                    log.info(f'Renamed exported file to match expected name: {book_name}.{book_format}')
                except Exception as e:
                    log.error(f'Failed to rename exported file: {e}')
    else:
        download_name = book_name

    response = make_response(send_from_directory(filename, download_name + "." + book_format))
    # ToDo Check headers parameter
    for element in headers:
        response.headers[element[0]] = element[1]
    return response


##################################


def check_architecture():
    arch = platform.machine()
    # amd64/arm64 are the Windows/macOS names for the same two architectures
    if arch.lower() not in ['x86_64', 'amd64', 'aarch64', 'arm64']:
        return _("Lily is built for x86_64 and ARM64, but this machine is %(arch)s. If book imports "
                 "fail, run Lily on an x86_64 or ARM64 machine.", arch=arch)
    return None


def tags_filters():
    negtags_list = current_user.list_denied_tags()
    postags_list = current_user.list_allowed_tags()
    neg_content_tags_filter = false() if negtags_list == [''] else db.Tags.name.in_(negtags_list)
    pos_content_tags_filter = true() if postags_list == [''] else db.Tags.name.in_(postags_list)
    return and_(pos_content_tags_filter, ~neg_content_tags_filter)


def parse_partial_date(value):
    """A "2016-05-03", "2016-05" or "2016" as a datetime (a year or month begins on its first
    day), or None. Providers often know only the year a book came out."""
    for fmt in ('%Y-%m-%d', '%Y-%m', '%Y'):
        try:
            return datetime.strptime(str(value or '').strip(), fmt)
        except ValueError:
            continue
    return None


def get_download_link(book_id, book_format):
    book_format = book_format.split(".")[0]
    # Try filtered view first to respect user restrictions
    book = calibre_db.get_filtered_book(book_id)

    # If not found but user is admin, fall back to unfiltered direct lookup
    if not book and getattr(current_user, 'role_admin', lambda: False)():
        log.debug(f"Admin fallback: get_book used for download of id={book_id}")
        book = calibre_db.get_book(book_id)

    if not book:
        log.error("Book id {} not found for downloading".format(book_id))
        abort(404)

    data1 = calibre_db.get_book_format(book.id, book_format.upper())
    if not data1:
        log.error("Requested format %s for book id %s not found in database", book_format.upper(), book_id)
        abort(404)

    # collect downloaded books only for registered user and not for anonymous user
    if current_user.is_authenticated:
        ub.update_download(book_id, int(current_user.id))

    file_name = book.title
    # calibre's "Unknown" stand-in is no author to name the file after
    if len(book.authors) > 0 and not is_unknown_author(book.authors[0].name):
        file_name = file_name + ' - ' + book.authors[0].name
    file_name = get_valid_filename(file_name, replace_whitespace=False)
    headers = Headers()
    headers["Content-Type"] = mimetypes.types_map.get('.' + book_format, "application/octet-stream")
    headers["Content-Disposition"] = "attachment; filename=%s.%s; filename*=UTF-8''%s.%s" % (
        quote(file_name), book_format, quote(file_name), book_format)
    return do_download_file(book, book_format, data1, headers)


def clear_cover_thumbnail_cache(book_id):
    # Always allow clearing thumbnail cache
    # Remove from pending set when clearing cache (e.g., during book deletion)
    _pending_thumbnail_books.discard(book_id)
    WorkerThread.add(None, TaskClearCoverThumbnailCache(book_id), hidden=True)


def replace_cover_thumbnail_cache(book_id, book_path=None, last_modified=None):
    # Always allow replacing thumbnail cache
    # Remove from pending set to allow regeneration
    _pending_thumbnail_books.discard(book_id)
    # Try to queue clear task (not critical if it fails)
    try:
        WorkerThread.add(None, TaskClearCoverThumbnailCache(book_id), hidden=True)
    except Exception as e:
        log.error(f'Failed to queue thumbnail clear for book {book_id}: {e}')
    # Queue generation task and add to pending set only if successful
    try:
        WorkerThread.add(
            None,
            TaskGenerateCoverThumbnails(
                book_id,
                book_path=book_path,
                last_modified=last_modified,
            ),
            hidden=True,
        )
        _pending_thumbnail_books.add(book_id)
    except Exception as e:
        log.error(f'Failed to queue thumbnail generation for book {book_id}: {e}')
