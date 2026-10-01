# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Editing a book's metadata and files, deleting books, and the helpers the bulk and upload modules share."""

import os
import sys
import time
from datetime import datetime, timezone
import json

from markupsafe import escape, Markup  # dependency of flask
from functools import wraps

from flask import Blueprint, request, flash, redirect, url_for, abort, Response
from flask_babel import gettext as _
from flask_babel import lazy_gettext as N_
from flask_babel import get_locale
from .cw_login import current_user
from sqlalchemy.exc import OperationalError, IntegrityError, InterfaceError, InvalidRequestError
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.sql.expression import func, or_

from . import logger, isoLanguages, gdriveutils, uploader, helper
from .clean_html import clean_string
from . import config, ub, db, calibre_db
from .services.worker import WorkerThread
from .tasks.upload import TaskUpload
from .render_template import render_title_template
from .helper import change_archived_books
from .redirect import get_redirect_location
from .shelf import check_shelf_edit_permissions
from .file_helper import validate_mime_type
from .usermanagement import user_login_required, login_required_if_no_ano
from .string_helper import strip_whitespaces

editbook = Blueprint('edit-book', __name__)
log = logger.create()


def upload_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        if current_user.role_upload():
            return f(*args, **kwargs)
        abort(403)

    return inner


def edit_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        if current_user.role_edit() or current_user.role_admin():
            return f(*args, **kwargs)
        abort(403)

    return inner


@editbook.route("/ajax/delete/<int:book_id>", methods=["POST"])
@user_login_required
def delete_book_from_details(book_id):
    return Response(delete_book_from_table(book_id, "", True), mimetype='application/json')


@editbook.route("/delete/<int:book_id>", defaults={'book_format': ""}, methods=["POST"])
@editbook.route("/delete/<int:book_id>/<string:book_format>", methods=["POST"])
@user_login_required
def delete_book_ajax(book_id, book_format):
    return delete_book_from_table(book_id, book_format, False, request.form.to_dict().get('location', ""))


@editbook.route("/admin/book/<int:book_id>", methods=['GET'])
@login_required_if_no_ano
@edit_required
def show_edit_book(book_id):
    return render_edit_book(book_id)


@editbook.route("/admin/book/<int:book_id>", methods=['POST'])
@login_required_if_no_ano
@edit_required
def edit_book(book_id):
    return do_edit_book(book_id)


@editbook.route("/ajax/getcustomenum/<int:c_id>")
@user_login_required
def table_get_custom_enum(c_id):
    ret = list()
    cc = (calibre_db.session.query(db.CustomColumns)
          .filter(db.CustomColumns.id == c_id)
          .filter(db.CustomColumns.datatype.notin_(db.cc_exceptions)).one_or_none())
    ret.append({'value': "", 'text': ""})
    for idx, en in enumerate(cc.get_display_dict()['enum_values']):
        ret.append({'value': en, 'text': en})
    return json.dumps(ret)


@editbook.route("/ajax/editbooks/<param>", methods=['POST'])
@login_required_if_no_ano
@edit_required
def edit_list_book(param):
    vals = request.form.to_dict()
    return edit_book_param(param, vals)

@editbook.route("/ajax/editselectedbooks", methods=['POST'])
@login_required_if_no_ano
@edit_required
def edit_selected_books():
    d = request.get_json()
    selections = d.get('selections')
    checkA = d.get('checkA')

    if len(selections) != 0:
        for book_id in selections:
            vals = {
                "pk": book_id,
                "value": None,
                "checkA": checkA,
            }
            book = calibre_db.get_book(book_id)
            if not book:
                continue

            # Collect all changes
            changes = {key: d.get(key) for key in ['title', 'title_sort', 'author_sort', 'authors', 'categories', 'series', 'languages', 'publishers', 'comments'] if d.get(key)}

            metadata_changed = False
            log_payload = {}

            # Apply changes without committing or updating directory structure yet
            title_changed = False
            authors_changed = False
            input_authors = [author.name for author in book.authors]

            if 'title' in changes:
                vals['value'] = changes['title']
                if handle_title_on_edit(book, vals.get('value', "")):
                    title_changed = True
                    metadata_changed = True
                    log_payload['title'] = vals.get('value', "")

            if 'title_sort' in changes:
                vals['value'] = changes['title_sort']
                book.sort = vals['value']

            if 'author_sort' in changes:
                vals['value'] = changes['author_sort']
                book.author_sort = vals['value']

            if 'authors' in changes:
                vals['value'] = changes['authors']
                input_authors, author_change = handle_author_on_edit(book, vals['value'], vals.get('checkA', None) == "true")
                if author_change:
                    authors_changed = True
                    metadata_changed = True
                    log_payload['authors'] = vals.get('value', "")

            if 'categories' in changes:
                vals['value'] = changes['categories']
                if edit_book_tags(vals['value'], book):
                    metadata_changed = True
                    log_payload['tags'] = vals.get('value', "")

            if 'series' in changes:
                vals['value'] = changes['series']
                if edit_book_series(vals['value'], book):
                    metadata_changed = True
                    log_payload['series'] = vals.get('value', "")

            if 'languages' in changes:
                vals['value'] = changes['languages']
                invalid = list()
                if edit_book_languages(vals['value'], book, invalid=invalid):
                    metadata_changed = True
                    log_payload['languages'] = vals.get('value', "")

            if 'publishers' in changes:
                vals['value'] = changes['publishers']
                if edit_book_publisher(vals['value'], book):
                    metadata_changed = True
                    log_payload['publisher'] = vals.get('value', "")

            if 'comments' in changes:
                vals['value'] = changes['comments']
                if edit_book_comments(vals['value'], book):
                    metadata_changed = True
                    log_payload['comments'] = vals.get('value', "")

            # Update directory structure once if title or authors changed
            if title_changed or authors_changed:
                rename_error = helper.update_dir_structure(book.id, config.get_book_path(), input_authors[0])
                if rename_error:
                    # Handle error appropriately, maybe flash a message
                    calibre_db.session.rollback()
                    continue # or return an error response

            book.last_modified = datetime.now(timezone.utc)

            if metadata_changed:
                calibre_db.set_metadata_dirty(book.id)
            try:
                calibre_db.session.commit()
            except (OperationalError, IntegrityError, StaleDataError) as e:
                calibre_db.session.rollback()
                log.error_or_exception("Database error: {}".format(e))
                # Handle error appropriately
                continue

            if metadata_changed and log_payload:
                try:
                    log_payload.setdefault('title', book.title)
                    log_payload.setdefault('authors', ' & '.join([a.name for a in book.authors]))
                    log_payload['_cwa_meta'] = {
                        'modify_date': True,
                        'change_count': len([k for k in log_payload.keys() if not k.startswith('_')]),
                        'has_content': any(v != '' for k, v in log_payload.items() if not k.startswith('_')),
                        'timestamp': datetime.now().isoformat()
                    }

                    now = datetime.now()
                    log_path = f'/app/calibre-web-automated/metadata_change_logs/{now.strftime("%Y%m%d%H%M%S")}-{book.id}.json'
                    with open(log_path, 'w', encoding='utf-8') as f:
                        json.dump(log_payload, f, indent=4, ensure_ascii=False)
                    log.debug(f"Created metadata change log for book {book.id} with changes: {list(log_payload.keys())}")
                except Exception as e:
                    log.error_or_exception(f"Failed to write metadata change log for book {book.id}: {e}")

        return json.dumps({'success': True})
    return ""

# Separated from /editbooks so that /editselectedbooks can also use this
#
# param: the property of the book to be changed
# vals - JSON Object:
#   {
#       'pk': "the book id",
#       'value': "changes value of param to what's passed here"
#       'checkA': "Optional. Used to check if autosort author is enabled. Assumed as true if not passed"
#       'checkT': "Optional. Used to check if autotitle author is enabled. Assumed as true if not passed"
#   }
#
@login_required_if_no_ano
@edit_required
def edit_book_param(param, vals):
    book = calibre_db.get_book(vals['pk'])
    sort_param = ""
    ret = ""
    metadata_changed = False
    log_key = None
    log_value = None
    try:
        if param == 'series_index':
            edit_book_series_index(vals['value'], book)
            ret = Response(json.dumps({'success': True, 'newValue': book.series_index}), mimetype='application/json')
            metadata_changed = True
            log_key = 'series_index'
            log_value = vals.get('value', '')
        elif param == 'tags':
            edit_book_tags(vals['value'], book)
            ret = Response(json.dumps({'success': True, 'newValue': ', '.join([tag.name for tag in book.tags])}),
                           mimetype='application/json')
            metadata_changed = True
            log_key = 'tags'
            log_value = vals.get('value', '')
        elif param == 'series':
            edit_book_series(vals['value'], book)
            ret = Response(json.dumps({'success': True, 'newValue':  ', '.join([serie.name for serie in book.series])}),
                           mimetype='application/json')
            metadata_changed = True
            log_key = 'series'
            log_value = vals.get('value', '')
        elif param == 'publishers':
            edit_book_publisher(vals['value'], book)
            ret = Response(json.dumps({'success': True,
                                       'newValue': ', '.join([publisher.name for publisher in book.publishers])}),
                           mimetype='application/json')
            metadata_changed = True
            log_key = 'publisher'
            log_value = vals.get('value', '')
        elif param == 'languages':
            invalid = list()
            edit_book_languages(vals['value'], book, invalid=invalid)
            if invalid:
                ret = Response(json.dumps({'success': False,
                                           'msg': 'Invalid languages in request: {}'.format(','.join(invalid))}),
                               mimetype='application/json')
            else:
                lang_names = list()
                for lang in book.languages:
                    lang_names.append(isoLanguages.get_language_name(get_locale(), lang.lang_code))
                ret = Response(json.dumps({'success': True, 'newValue':  ', '.join(lang_names)}),
                               mimetype='application/json')
                metadata_changed = True
                log_key = 'languages'
                log_value = vals.get('value', '')
        elif param == 'author_sort':
            book.author_sort = vals['value']
            ret = Response(json.dumps({'success': True, 'newValue':  book.author_sort}),
                           mimetype='application/json')
        elif param == 'title':
            sort_param = book.sort
            if handle_title_on_edit(book, vals.get('value', "")):
                # Pass the current author to prevent directory structure issues
                current_author = book.authors[0].name if book.authors else None
                rename_error = helper.update_dir_structure(book.id, config.get_book_path(), current_author)
                if not rename_error:
                    ret = Response(json.dumps({'success': True, 'newValue':  book.title}),
                                   mimetype='application/json')
                    metadata_changed = True
                    log_key = 'title'
                    log_value = vals.get('value', '')
                else:
                    ret = Response(json.dumps({'success': False,
                                               'msg': rename_error}),
                                   mimetype='application/json')
        elif param == 'sort':
            book.sort = vals['value']
            ret = Response(json.dumps({'success': True, 'newValue':  book.sort}),
                           mimetype='application/json')
        elif param == 'comments':
            edit_book_comments(vals['value'], book)
            ret = Response(json.dumps({'success': True, 'newValue':  book.comments[0].text}),
                           mimetype='application/json')
            metadata_changed = True
            log_key = 'comments'
            log_value = vals.get('value', '')
        elif param == 'authors':
            input_authors, __ = handle_author_on_edit(book, vals['value'], vals.get('checkA', None) == "true")
            rename_error = helper.update_dir_structure(book.id, config.get_book_path(), input_authors[0])
            if not rename_error:
                ret = Response(json.dumps({
                    'success': True,
                    'newValue':  ' & '.join([author.replace('|', ',') for author in input_authors])}),
                    mimetype='application/json')
                metadata_changed = True
                log_key = 'authors'
                log_value = vals.get('value', '')
            else:
                ret = Response(json.dumps({'success': False,
                                           'msg': rename_error}),
                               mimetype='application/json')
        elif param == 'rating':
            rating_changed = edit_book_ratings({'rating': vals.get('value', '')}, book)
            ret = Response(json.dumps({'success': True, 'newValue': vals.get('value', '')}),
                           mimetype='application/json')
            metadata_changed = rating_changed
            log_key = 'rating'
            log_value = vals.get('value', '')
        elif param == 'is_archived':
            change_archived_books(book.id, vals['value'] == "True",
                                  message="Book {} archive bit set to: {}".format(book.id, vals['value']))
            return ""
        elif param == 'read_status':
            ret = helper.edit_book_read_status(book.id, vals['value'] == "True")
            if ret:
                return ret, 400
        elif param.startswith("custom_column_"):
            new_val = dict()
            new_val[param] = vals['value']
            edit_single_cc_data(book.id, book, param[14:], new_val)
            # ToDo: Very hacky find better solution
            if vals['value'] in ["True", "False"]:
                ret = ""
            else:
                ret = Response(json.dumps({'success': True, 'newValue': vals['value']}),
                               mimetype='application/json')
            metadata_changed = True
            log_key = param
            log_value = vals.get('value', '')
        else:
            return _("Parameter not found"), 400
        book.last_modified = datetime.now(timezone.utc)

        if metadata_changed:
            calibre_db.set_metadata_dirty(book.id)

        calibre_db.session.commit()
        # revert change for sort if automatic fields link is deactivated
        if param == 'title' and vals.get('checkT') == "false":
            book.sort = sort_param
            calibre_db.session.commit()

        if metadata_changed and log_key is not None:
            try:
                payload = {log_key: log_value}
                payload.setdefault('title', book.title)
                payload.setdefault('authors', ' & '.join([a.name for a in book.authors]))
                payload['_cwa_meta'] = {
                    'modify_date': True,
                    'change_count': 1,
                    'has_content': log_value != '',
                    'timestamp': datetime.now().isoformat()
                }

                now = datetime.now()
                log_path = f'/app/calibre-web-automated/metadata_change_logs/{now.strftime("%Y%m%d%H%M%S")}-{book.id}.json'
                with open(log_path, 'w', encoding='utf-8') as f:
                    json.dump(payload, f, indent=4, ensure_ascii=False)
                log.debug(f"Created metadata change log for book {book.id} with changes: {list(payload.keys())}")
            except Exception as e:
                log.error_or_exception(f"Failed to write metadata change log for book {book.id}: {e}")
    except (OperationalError, IntegrityError, StaleDataError, InvalidRequestError) as e:
        calibre_db.session.rollback()
        log.error_or_exception("Database error: {}".format(e))
        ret = Response(json.dumps({'success': False,
                                   'msg': 'Database error: {}'.format(e.orig if hasattr(e, "orig") else e)}),
                       mimetype='application/json')
    return ret


def _queue_duplicate_scan_after_change(book_ids=None):
    """Queue a debounced duplicate scan after manual changes."""
    try:
        sys.path.insert(1, '/app/calibre-web-automated/scripts/')
        from cwa_db import CWA_DB
        from .cwa_functions import queue_debounced_duplicate_scan

        cwa_db = CWA_DB()
        delay_seconds = int(cwa_db.cwa_settings.get('duplicate_scan_debounce_seconds', 60))
        delay_seconds = max(5, min(600, delay_seconds))
        result = queue_debounced_duplicate_scan(delay_seconds=delay_seconds, book_ids=book_ids or [])
        if result.get("skipped"):
            log.debug("Duplicate scan scheduling skipped after change: %s", result.get("reason"))
    except Exception as e:
        log.error("Failed to queue duplicate scan after change: %s", str(e))


PAPERS_SHELF = "Papers"


def _editable_shelves():
    return [shelf for shelf in ub.session.query(ub.Shelf).filter(
                or_(ub.Shelf.is_public == 1, ub.Shelf.user_id == current_user.id)).order_by(ub.Shelf.name)
            if check_shelf_edit_permissions(shelf)]


def _book_shelf_ids(book_id):
    return [link.shelf for link in ub.session.query(ub.BookShelf).filter(ub.BookShelf.book_id == book_id)]


def _shelf_names(raw):
    """The Shelves chip editor posts its names as a JSON list; drop blanks and repeats."""
    try:
        names = json.loads(raw or "[]")
    except ValueError:
        return []
    if not isinstance(names, list):
        return []
    seen, result = set(), []
    for name in names:
        name = strip_whitespaces(str(name))
        if name and name.lower() not in seen:
            seen.add(name.lower())
            result.append(name)
    return result


def _update_shelves(book_id, to_save):
    """Put the book on exactly the shelves named in the edit form's Shelves chips. Names that
    match none of the user's editable shelves are ignored (shelves are created from the
    sidebar), except Papers, which Fetch Metadata creates the first time it files a paper."""
    try:
        shelves = _editable_shelves()
        if not to_save.get("shelves_present"):
            return
        wanted = set()
        for name in _shelf_names(to_save.get("shelves")):
            matches = [shelf for shelf in shelves if shelf.name.lower() == name.lower()]
            # Same name on a public shelf and the user's own: the user's own wins
            shelf = next((m for m in matches if m.user_id == current_user.id), None) or \
                next(iter(matches), None)
            if not shelf:
                if name.lower() != PAPERS_SHELF.lower():
                    continue
                shelf = ub.Shelf(name=PAPERS_SHELF, is_public=0, user_id=current_user.id)
                ub.session.add(shelf)
                ub.session.flush()
                shelves.append(shelf)
            wanted.add(shelf.id)

        current = set(_book_shelf_ids(book_id))
        now = datetime.now(timezone.utc)
        for shelf in shelves:
            if shelf.id in wanted and shelf.id not in current:
                max_order = ub.session.query(func.max(ub.BookShelf.order)).filter(
                    ub.BookShelf.shelf == shelf.id).scalar()
                shelf.books.append(ub.BookShelf(shelf=shelf.id, book_id=book_id, order=(max_order or 0) + 1))
                shelf.last_modified = now
            elif shelf.id in current and shelf.id not in wanted:
                ub.session.query(ub.BookShelf).filter(ub.BookShelf.shelf == shelf.id,
                                                      ub.BookShelf.book_id == book_id).delete()
                shelf.last_modified = now
        ub.session.commit()
    except (OperationalError, InvalidRequestError) as e:
        ub.session.rollback()
        log.error_or_exception("Could not update shelves for book %s: %s", book_id, e)
        flash(_("Oops! Database Error: %(error)s.", error=e), category="error")


def do_edit_book(book_id, upload_formats=None):
    request_start = time.monotonic()
    log.debug("[edit_book] start book_id=%s user=%s upload_formats=%s", book_id, getattr(current_user, "name", "unknown"), bool(upload_formats))
    modify_date = False
    edit_error = False
    refresh_cover_thumbnail_after_commit = False

    # create the function for sorting...
    calibre_db.create_functions(config)

    book = calibre_db.get_filtered_book(book_id, allow_show_archived=True)
    # Book not found
    if not book:
        flash(_("Oops! Selected book is unavailable. File does not exist or is not accessible"),
              category="error")
        return redirect(url_for("web.index"))

    to_save = request.form.to_dict()

    try:
        title_change = False
        author_change = False
        title_author_error = None
        input_authors = [author.name for author in book.authors]

        # Stage 1: Apply title/author changes before touching the remaining metadata fields.
        if "title" in to_save:
            title_change = handle_title_on_edit(book, to_save["title"])

        if not upload_formats:
            new_input_authors, author_change = handle_author_on_edit(book, to_save["authors"])
            if author_change:
                input_authors = new_input_authors
            # Keep the filesystem path in sync before staging relationship-heavy metadata.
            if title_change or author_change:
                title_author_error = helper.update_dir_structure(book.id, config.get_book_path(), input_authors[0])
                if title_author_error:
                    flash(title_author_error, category="error")
                    calibre_db.session.rollback()
                    return render_edit_book(book_id)
                modify_date = True
            modify_date |= edit_book_ratings(to_save, book)
        else:
            to_save, edit_error = upload_book_formats(upload_formats, book, book_id, book.has_cover)

        cover_upload_success = upload_cover(request, book)
        if cover_upload_success or to_save.get("format_cover"):
            book.has_cover = 1
            modify_date = True

        if to_save.get("cover_url"):
            if not current_user.role_edit():
                edit_error = True
                flash(_("User has no rights to upload cover"), category="error")
            elif to_save["cover_url"].endswith('/static/generic_cover.svg'):
                book.has_cover = 0
            else:
                cover_start = time.monotonic()
                result, error = helper.save_cover_from_url(to_save["cover_url"].strip(), book.path)
                if result:
                    book.has_cover = 1
                    modify_date = True
                    refresh_cover_thumbnail_after_commit = True
                    log.debug("[edit_book] cover saved book_id=%s duration=%.3fs", book.id, time.monotonic() - cover_start)
                else:
                    log.warning("[edit_book] cover save failed book_id=%s duration=%.3fs error=%s", book.id, time.monotonic() - cover_start, error)
                    edit_error = True
                    flash(error, category="error")

        # Stage 2: Apply the remaining metadata changes to the database session.
        modify_date |= edit_book_series_index(to_save.get("series_index"), book)
        modify_date |= edit_book_comments(Markup(to_save.get('comments')).unescape(), book)

        input_identifiers = identifier_list(to_save, book)
        modification, warning = modify_identifiers(input_identifiers, book.identifiers, calibre_db.session)
        if warning:
            flash(_("Identifiers are not Case Sensitive, Overwriting Old Identifier"), category="warning")
        modify_date |= modification

        modify_date |= edit_book_tags(to_save.get('tags'), book)
        modify_date |= edit_book_series(to_save.get("series"), book)
        modify_date |= edit_book_publisher(to_save.get('publisher'), book)

        try:
            invalid = []
            modify_date |= edit_book_languages(to_save.get('languages'), book, upload_mode=upload_formats, invalid=invalid)
            if invalid:
                for lang in invalid:
                    flash(_("'%(langname)s' is not a valid language", langname=lang), category="warning")
        except ValueError as e:
            flash(str(e), category="error")
            edit_error = True

        modify_date |= edit_all_cc_data(book_id, book, to_save)

        if to_save.get("pubdate"):
            try:
                book.pubdate = datetime.strptime(to_save["pubdate"], "%Y-%m-%d")
            except ValueError as e:
                book.pubdate = db.Books.DEFAULT_PUBDATE
                flash(str(e), category="error")
                edit_error = True
        else:
            book.pubdate = db.Books.DEFAULT_PUBDATE

        # Stage 3: Commit all changes to the database.
        if modify_date:
            book.last_modified = datetime.now(timezone.utc)
            calibre_db.set_metadata_dirty(book.id)

        try:
            calibre_db.session.merge(book)
            calibre_db.session.commit()
            log.debug("[edit_book] db commit ok book_id=%s duration=%.3fs", book.id, time.monotonic() - request_start)
        except InvalidRequestError as e:
            # Recover from closed/invalid transaction by recreating the session and retrying once
            log.warning("Edit book transaction invalid, retrying commit: %s", e)
            try:
                calibre_db.session.rollback()
            except Exception:
                pass
            try:
                calibre_db.session.close()
            except Exception:
                pass
            calibre_db.session = None
            calibre_db.ensure_session()
            calibre_db.session.merge(book)
            calibre_db.session.commit()
            log.debug("[edit_book] db commit retry ok book_id=%s duration=%.3fs", book.id, time.monotonic() - request_start)

        if refresh_cover_thumbnail_after_commit:
            helper.replace_cover_thumbnail_cache(
                book.id,
                book_path=book.path,
                last_modified=book.last_modified,
            )

        # CWA: Export of changed Metadata after commit, to avoid race conditions with folder renames
        # Only create log if there were actual meaningful metadata changes
        try:
            # Define metadata fields that represent actual content changes
            metadata_fields = {
                'title', 'authors', 'series', 'series_index', 'tags', 'comments',
                'cover_url', 'pubdate', 'publisher', 'languages', 'rating'
            }

            # Filter to meaningful metadata changes (including empty values for legitimate clearing)
            # but exclude pure form artifacts
            meaningful_changes = {}
            for key, value in to_save.items():
                if (key in metadata_fields and
                    value is not None and
                    key not in ['csrf_token', 'book_format']):
                    meaningful_changes[key] = value

            # Also include custom column changes (including empty values)
            custom_column_changes = {k: v for k, v in to_save.items()
                                   if k.startswith('custom_column_') and v is not None}
            meaningful_changes.update(custom_column_changes)

            # Create log if we have actual database changes (modify_date=True)
            # OR if this appears to be a metadata fetch with content (non-empty meaningful_changes)
            should_create_log = (modify_date or
                               (meaningful_changes and any(v != '' for v in meaningful_changes.values())))

            if should_create_log:
                payload = dict(meaningful_changes)
                payload.setdefault('title', book.title)
                payload.setdefault('authors', ' & '.join([a.name for a in book.authors]))

                # Add source information for debugging
                payload['_cwa_meta'] = {
                    'modify_date': modify_date,
                    'change_count': len(meaningful_changes),
                    'has_content': any(v != '' for v in meaningful_changes.values()),
                    'timestamp': datetime.now().isoformat()
                }

                now = datetime.now()
                log_path = f'/app/calibre-web-automated/metadata_change_logs/{now.strftime("%Y%m%d%H%M%S")}-{book.id}.json'
                with open(log_path, 'w', encoding='utf-8') as f:
                    json.dump(payload, f, indent=4, ensure_ascii=False)
                log.debug(f"Created metadata change log for book {book.id} with changes: {list(meaningful_changes.keys())}")
            else:
                log.debug(f"Skipped metadata change log for book {book.id} - no meaningful changes detected (modify_date={modify_date}, changes={list(meaningful_changes.keys())})")
        except Exception as e:
            log.error_or_exception(f"Failed to write metadata change log for book {book.id}: {e}")

        _update_shelves(book.id, to_save)

        # Stage 4: Post-commit operations.
        if config.config_use_google_drive:
            gdriveutils.updateGdriveCalibreFromLocal()

        if not edit_error and not title_author_error and cover_upload_success is not False:
            flash(_("Metadata successfully updated"), category="success")
            if modify_date:
                _queue_duplicate_scan_after_change([book.id])

        if upload_formats:
            return Response(json.dumps({"location": url_for('edit-book.show_edit_book', book_id=book_id)}), mimetype='application/json')

        if "detail_view" in to_save:
            return redirect(url_for('web.show_book', book_id=book.id))
        else:
            return render_edit_book(book_id)

    except (ValueError, OperationalError, IntegrityError, StaleDataError, InterfaceError, InvalidRequestError) as e:
        log.error_or_exception("Database or Value error: {}".format(e))
        calibre_db.session.rollback()
        flash(_("Oops! Database Error: %(error)s.", error=e.orig if hasattr(e, "orig") else e), category="error")
        return redirect(url_for('web.show_book', book_id=book.id))
    except Exception as ex:
        log.error_or_exception(ex)
        calibre_db.session.rollback()
        flash(_("Error editing book: {}".format(ex)), category="error")
        return redirect(url_for('web.show_book', book_id=book.id))


def merge_metadata(book, meta, to_save):
    if meta.cover:
        to_save['cover_format'] = meta.cover
    for s_field, m_field in [
            ('tags', 'tags'), ('authors', 'author'), ('series', 'series'),
            ('series_index', 'series_id'), ('languages', 'languages'),
            ('title', 'title'), ('comments', 'description')]:
        try:
            val = None if len(getattr(book, s_field)) else getattr(meta, m_field, '')
        except TypeError:
            val = None if len(str(getattr(book, s_field))) else getattr(meta, m_field, '')
        if val:
            to_save[s_field] = val


def identifier_list(to_save, book):
    """Generate a list of Identifiers from form information"""
    id_type_prefix = 'identifier-type-'
    id_val_prefix = 'identifier-val-'
    result = []
    rows = {}
    for key, value in to_save.items():
        if key.startswith(id_type_prefix):
            row_id = key[len(id_type_prefix):]
            rows.setdefault(row_id, {})['type'] = value
        elif key.startswith(id_val_prefix):
            row_id = key[len(id_val_prefix):]
            rows.setdefault(row_id, {})['val'] = value
    for row in rows.values():
        id_type = (row.get('type') or "").strip()
        id_val = (row.get('val') or "").strip()
        if not id_type or not id_val:
            continue
        if id_val.startswith("data:"):
            id_val, __, __ = str.partition(id_val, ",")
        result.append(db.Identifiers(id_val, id_type, book.id))
    return result


def prepare_authors(authr, calibre_path, gdrive=False):
    if gdrive:
        calibre_path = ""
    # handle authors
    input_authors = authr.split('&')
    input_authors = list(map(lambda it: it.strip().replace(',', '|'), input_authors))
    # Remove duplicates in authors list
    input_authors = helper.uniq(input_authors)

    # we have all author names now
    if input_authors == ['']:
        input_authors = [_('Unknown')]  # prevent empty Author

    for in_aut in input_authors:
        renamed_author = calibre_db.session.query(db.Authors).filter(func.lower(db.Authors.name).ilike(in_aut)).first()
        if renamed_author and in_aut != renamed_author.name:
            old_author_name = renamed_author.name
            # rename author in Database
            create_objects_for_addition(renamed_author, in_aut,"author")
            # rename all Books with this author as first author:
            # rename all book author_sort strings with the new author name
            all_books = calibre_db.session.query(db.Books) \
                .filter(db.Books.authors.any(db.Authors.name == renamed_author.name)).all()
            for one_book in all_books:
                # ToDo: check
                sorted_old_author = helper.get_sorted_author(old_author_name)
                sorted_renamed_author = helper.get_sorted_author(in_aut)
                # change author sort path
                try:
                    author_index = one_book.author_sort.index(sorted_old_author)
                    one_book.author_sort = one_book.author_sort.replace(sorted_old_author, sorted_renamed_author)
                except ValueError:
                    log.error("Sorted author {} not found in database".format(sorted_old_author))
                    author_index = -1
                # change book path if changed author is first author -> match on first position
                if author_index == 0:
                    one_titledir = one_book.path.split('/')[1]
                    one_old_authordir = one_book.path.split('/')[0]
                    # rename author path only once per renamed author -> search all books with author name in book.path
                    # Pass the NEW author name as target for the directory rename to avoid path mismatches
                    # das muss einmal geschehen aber pro Buch geprüft werden ansonsten habe ich das Problem das vlt. 2 gleiche Ordner bis auf Groß/Kleinschreibung vorhanden sind im Umzug
                    new_author_dir = helper.rename_author_path(in_aut, one_old_authordir, in_aut, calibre_path, gdrive)
                    one_book.path = os.path.join(new_author_dir, one_titledir).replace('\\', '/')
                    # rename all books in book data with the new author name and move corresponding files to new locations
                    new_path = os.path.join(calibre_path, new_author_dir, one_titledir)
                    # Use the NEW author for filenames as well
                    all_new_name = helper.get_valid_filename(one_book.title, chars=42) + ' - ' \
                                   + helper.get_valid_filename(in_aut, chars=42)
                    # change location in database to new author/title path
                    helper.rename_all_files_on_change(one_book, new_path, new_path, all_new_name, gdrive)

    return input_authors


def delete_whole_book(book_id, book):
    # delete book from shelves, Downloads, Read list
    ub.session.query(ub.BookShelf).filter(ub.BookShelf.book_id == book_id).delete()
    ub.session.query(ub.ReadBook).filter(ub.ReadBook.book_id == book_id).delete()
    ub.session.query(ub.ArchivedBook).filter(ub.ArchivedBook.book_id == book_id).delete()
    ub.delete_download(book_id)
    ub.session_commit()

    # check if only this book links to:
    # author, language, series, tags, custom columns
    modify_database_object([''], book.authors, db.Authors, calibre_db.session, 'author')
    modify_database_object([u''], book.tags, db.Tags, calibre_db.session, 'tags')
    modify_database_object([u''], book.series, db.Series, calibre_db.session, 'series')
    modify_database_object([u''], book.languages, db.Languages, calibre_db.session, 'languages')
    modify_database_object([u''], book.publishers, db.Publishers, calibre_db.session, 'publishers')

    cc = calibre_db.session.query(db.CustomColumns). \
        filter(db.CustomColumns.datatype.notin_(db.cc_exceptions)).all()
    for c in cc:
        cc_string = "custom_column_" + str(c.id)
        if not c.is_multiple:
            if len(getattr(book, cc_string)) > 0:
                if c.datatype == 'bool' or c.datatype == 'integer' or c.datatype == 'float':
                    del_cc = getattr(book, cc_string)[0]
                    getattr(book, cc_string).remove(del_cc)
                    log.debug('remove ' + str(c.id))
                    calibre_db.session.delete(del_cc)
                    calibre_db.session.commit()
                elif c.datatype == 'rating':
                    del_cc = getattr(book, cc_string)[0]
                    getattr(book, cc_string).remove(del_cc)
                    if len(del_cc.books) == 0:
                        log.debug('remove ' + str(c.id))
                        calibre_db.session.delete(del_cc)
                        calibre_db.session.commit()
                else:
                    del_cc = getattr(book, cc_string)[0]
                    getattr(book, cc_string).remove(del_cc)
                    log.debug('remove ' + str(c.id))
                    calibre_db.session.delete(del_cc)
                    calibre_db.session.commit()
        else:
            modify_database_object([u''], getattr(book, cc_string), db.cc_classes[c.id],
                                   calibre_db.session, 'custom')
    calibre_db.session.query(db.Books).filter(db.Books.id == book_id).delete()


def render_delete_book_result(book_format, json_response, warning, book_id, location=""):
    if book_format:
        if json_response:
            return json.dumps([warning, {"location": url_for("edit-book.show_edit_book", book_id=book_id),
                                         "type": "success",
                                         "format": book_format,
                                         "message": _('Book Format Successfully Deleted')}])
        else:
            flash(_('Book Format Successfully Deleted'), category="success")
            return redirect(url_for('edit-book.show_edit_book', book_id=book_id))
    else:
        if json_response:
            return json.dumps([warning, {"location": get_redirect_location(location, "web.index"),
                                         "type": "success",
                                         "format": book_format,
                                         "message": _('Book Successfully Deleted')}])
        else:
            flash(_('Book Successfully Deleted'), category="success")
            return redirect(get_redirect_location(location, "web.index"))


def delete_book_from_table(book_id, book_format, json_response, location=""):
    warning = {}
    if current_user.role_delete_books():
        book = calibre_db.get_book(book_id)
        if book:
            try:
                result, error = helper.delete_book(book, config.get_book_path(), book_format=book_format.upper())
                if not result:
                    if json_response:
                        return json.dumps([{"location": url_for("edit-book.show_edit_book", book_id=book_id),
                                            "type": "danger",
                                            "format": "",
                                            "message": error}])
                    else:
                        flash(error, category="error")
                        return redirect(url_for('edit-book.show_edit_book', book_id=book_id))
                if error:
                    if json_response:
                        warning = {"location": url_for("edit-book.show_edit_book", book_id=book_id),
                                   "type": "warning",
                                   "format": "",
                                   "message": error}
                    else:
                        flash(error, category="warning")
                if not book_format:
                    delete_whole_book(book_id, book)
                else:
                    calibre_db.session.query(db.Data).filter(db.Data.book == book.id).\
                        filter(db.Data.format == book_format).delete()
                calibre_db.session.commit()

                refreshed_duplicate_cache = False
                if not book_format:
                    try:
                        from cps.duplicate_index import (
                            _current_max_book_id,
                            delete_book_keys,
                            get_duplicate_groups_from_index,
                        )
                        sys.path.insert(1, '/app/calibre-web-automated/scripts/')
                        from cwa_db import CWA_DB

                        delete_book_keys([book_id])
                        cwa_db = CWA_DB()
                        duplicate_groups = get_duplicate_groups_from_index(cwa_db.cwa_settings, include_dismissed=True)
                        cwa_db.update_duplicate_cache(duplicate_groups, len(duplicate_groups), _current_max_book_id())
                        refreshed_duplicate_cache = True
                    except Exception as e:
                        log.warning("Failed to refresh duplicate index/cache after deleting book %s: %s", book_id, str(e))

                # Format-only deletions and refresh failures need a later cache refresh.
                if not refreshed_duplicate_cache:
                    try:
                        sys.path.insert(1, '/app/calibre-web-automated/scripts/')
                        from cwa_db import CWA_DB
                        cwa_db = CWA_DB()
                        cwa_db.invalidate_duplicate_cache()
                    except Exception as e:
                        log.error("Failed to invalidate duplicate cache after deletion: %s", str(e))

            except Exception as ex:
                log.error_or_exception(ex)
                calibre_db.session.rollback()
                if json_response:
                    return json.dumps([{"location": url_for("edit-book.show_edit_book", book_id=book_id),
                                        "type": "danger",
                                        "format": "",
                                        "message": ex}])
                else:
                    flash(str(ex), category="error")
                    return redirect(url_for('edit-book.show_edit_book', book_id=book_id))

        else:
            # book not found
            log.error('Book with id "%s" could not be deleted: not found', book_id)
        return render_delete_book_result(book_format, json_response, warning, book_id, location)
    message = _("You are missing permissions to delete books")
    if json_response:
        return json.dumps({"location": url_for("edit-book.show_edit_book", book_id=book_id),
                           "type": "danger",
                           "format": "",
                           "message": message})
    else:
        flash(message, category="error")
        return redirect(url_for('edit-book.show_edit_book', book_id=book_id))


def render_edit_book(book_id):
    cc = calibre_db.session.query(db.CustomColumns).filter(db.CustomColumns.datatype.notin_(db.cc_exceptions)).all()
    book = calibre_db.get_filtered_book(book_id, allow_show_archived=True)
    if not book:
        flash(_("Oops! Selected book is unavailable. File does not exist or is not accessible"),
              category="error")
        return redirect(url_for("web.index"))

    for lang in book.languages:
        lang.language_name = isoLanguages.get_language_name(get_locale(), lang.lang_code)

    book.authors = calibre_db.order_authors([book])

    author_names = []
    for authr in book.authors:
        author_names.append(authr.name.replace('|', ','))

    return render_title_template('book_edit.html', book=book, authors=author_names, cc=cc,
                                 shelf_ids_editable=[shelf.id for shelf in _editable_shelves()],
                                 book_shelf_ids=_book_shelf_ids(book.id),
                                 title=_("Edit Metadata"), page="editbook",
                                 config=config)


def edit_book_ratings(to_save, book):
    changed = False
    if strip_whitespaces(to_save.get("rating", "")):
        old_rating = False
        if len(book.ratings) > 0:
            old_rating = book.ratings[0].rating
        rating_x2 = int(float(to_save.get("rating", "")) * 2)
        if rating_x2 != old_rating:
            changed = True
            is_rating = calibre_db.session.query(db.Ratings).filter(db.Ratings.rating == rating_x2).first()
            if is_rating:
                book.ratings.append(is_rating)
            else:
                new_rating = db.Ratings(rating=rating_x2)
                book.ratings.append(new_rating)
            if old_rating:
                book.ratings.remove(book.ratings[0])
    else:
        if len(book.ratings) > 0:
            book.ratings.remove(book.ratings[0])
            changed = True
    return changed


def edit_book_tags(tags, book):
    if tags is not None:
        input_tags = tags.split(',')
        input_tags = list(map(lambda it: strip_whitespaces(it), input_tags))
        # Remove duplicates
        input_tags = helper.uniq(input_tags)
        return modify_database_object(input_tags, book.tags, db.Tags, calibre_db.session, 'tags')
    return False

def edit_book_series(series, book):
    if series is not None:
        input_series = [strip_whitespaces(series)]
        input_series = [x for x in input_series if x != '']
        return modify_database_object(input_series, book.series, db.Series, calibre_db.session, 'series')
    return False


def edit_book_series_index(series_index, book):
    if series_index:
        # Add default series_index to book
        modify_date = False
        series_index = series_index or '1'
        if not series_index.replace('.', '', 1).isdigit():
            flash(_("Seriesindex: %(seriesindex)s is not a valid number, skipping", seriesindex=series_index), category="warning")
            return False
        if str(book.series_index) != series_index:
            book.series_index = series_index
            modify_date = True
        return modify_date
    return False


# Handle book comments/description
def edit_book_comments(comments, book):
    if comments is not None:
        modify_date = False
        if comments:
            comments = clean_string(comments, book.id)
        if len(book.comments):
            if book.comments[0].text != comments:
                book.comments[0].text = comments
                modify_date = True
        else:
            if comments:
                # Add comment via session instead of appending to collection during flush
                new_comment = db.Comments(comment=comments, book=book.id)
                calibre_db.session.add(new_comment)
                modify_date = True
        return modify_date


def edit_book_languages(languages, book, upload_mode=False, invalid=None):
    if languages is not None:
        input_languages = languages.split(',')
        unknown_languages = []
        if not upload_mode:
            input_l = isoLanguages.get_language_code_from_name(get_locale(), input_languages, unknown_languages)
        else:
            input_l = isoLanguages.get_valid_language_codes_from_code(get_locale(), input_languages, unknown_languages)
        for lang in unknown_languages:
            log.error("'%s' is not a valid language", lang)
            if isinstance(invalid, list):
                invalid.append(lang)
            else:
                raise ValueError(_("'%(langname)s' is not a valid language", langname=lang))
        # ToDo: Not working correct
        if upload_mode and len(input_l) == 1:
            # If the language of the file is excluded from the users view, it's not imported, to allow the user to view
            # the book it's language is set to the filter language
            if input_l[0] != current_user.filter_language() and current_user.filter_language() != "all":
                input_l[0] = calibre_db.session.query(db.Languages). \
                    filter(db.Languages.lang_code == current_user.filter_language()).first().lang_code
        # Remove duplicates from normalized langcodes
        input_l = helper.uniq(input_l)
        return modify_database_object(input_l, book.languages, db.Languages, calibre_db.session, 'languages')
    return False


def edit_book_publisher(publishers, book):
    if publishers is not None:
        changed = False
        if publishers:
            publisher = strip_whitespaces(publishers)
            if len(book.publishers) == 0 or (len(book.publishers) > 0 and publisher != book.publishers[0].name):
                changed |= modify_database_object([publisher], book.publishers, db.Publishers, calibre_db.session,
                                                  'publisher')
        elif len(book.publishers):
            changed |= modify_database_object([], book.publishers, db.Publishers, calibre_db.session, 'publisher')
        return changed
    return False

def edit_cc_data_value(book_id, book, c, to_save, cc_db_value, cc_string):
    changed = False
    if to_save[cc_string] == 'None':
        to_save[cc_string] = None
    elif c.datatype == 'bool':
        to_save[cc_string] = 1 if to_save[cc_string] == 'True' else 0
    elif c.datatype == 'comments':
        to_save[cc_string] = Markup(to_save[cc_string]).unescape()
        if to_save[cc_string]:
            to_save[cc_string] = clean_string(to_save[cc_string], book_id)
    elif c.datatype == 'datetime':
        try:
            to_save[cc_string] = datetime.strptime(to_save[cc_string], "%Y-%m-%d")
        except ValueError:
            to_save[cc_string] = db.Books.DEFAULT_PUBDATE

    if to_save[cc_string] != cc_db_value:
        if cc_db_value is not None:
            if to_save[cc_string] is not None:
                setattr(getattr(book, cc_string)[0], 'value', to_save[cc_string])
                changed = True
            else:
                del_cc = getattr(book, cc_string)[0]
                getattr(book, cc_string).remove(del_cc)
                calibre_db.session.delete(del_cc)
                changed = True
        else:
            cc_class = db.cc_classes[c.id]
            new_cc = cc_class(value=to_save[cc_string], book=book_id)
            calibre_db.session.add(new_cc)
            changed = True
    if type(to_save[cc_string]) is datetime:
        to_save[cc_string] = to_save[cc_string].strftime("%Y-%m-%d")
    return changed, to_save


def edit_cc_data_string(book, c, to_save, cc_db_value, cc_string):
    changed = False
    if c.datatype == 'rating':
        to_save[cc_string] = str(int(float(to_save[cc_string]) * 2))
    if strip_whitespaces(to_save[cc_string]) != cc_db_value:
        if cc_db_value is not None:
            # remove old cc_val
            del_cc = getattr(book, cc_string)[0]
            getattr(book, cc_string).remove(del_cc)
            if len(del_cc.books) == 0:
                calibre_db.session.delete(del_cc)
                changed = True
        cc_class = db.cc_classes[c.id]
        new_cc = calibre_db.session.query(cc_class).filter(
            cc_class.value == strip_whitespaces(to_save[cc_string])).first()
        # if no cc val is found add it
        if new_cc is None:
            new_cc = cc_class(value=strip_whitespaces(to_save[cc_string]))
            calibre_db.session.add(new_cc)
            changed = True
            calibre_db.session.flush()
            new_cc = calibre_db.session.query(cc_class).filter(
                cc_class.value == strip_whitespaces(to_save[cc_string])).first()
        # add cc value to book
        getattr(book, cc_string).append(new_cc)
    return changed, to_save


def edit_single_cc_data(book_id, book, column_id, to_save):
    cc = (calibre_db.session.query(db.CustomColumns)
          .filter(db.CustomColumns.datatype.notin_(db.cc_exceptions))
          .filter(db.CustomColumns.id == column_id)
          .all())
    return edit_cc_data(book_id, book, to_save, cc)


def edit_all_cc_data(book_id, book, to_save):
    cc = calibre_db.session.query(db.CustomColumns).filter(db.CustomColumns.datatype.notin_(db.cc_exceptions)).all()
    return edit_cc_data(book_id, book, to_save, cc)


def edit_cc_data(book_id, book, to_save, cc):
    changed = False
    for c in cc:
        cc_string = "custom_column_" + str(c.id)
        if to_save.get(cc_string) is not None:
            if not c.is_multiple:
                if len(getattr(book, cc_string)) > 0:
                    cc_db_value = getattr(book, cc_string)[0].value
                else:
                    cc_db_value = None
                if strip_whitespaces(to_save[cc_string]):
                    if c.datatype in ['int', 'bool', 'float', "datetime", "comments"]:
                        change, to_save = edit_cc_data_value(book_id, book, c, to_save, cc_db_value, cc_string)
                    else:
                        change, to_save = edit_cc_data_string(book, c, to_save, cc_db_value, cc_string)
                    changed |= change
                else:
                    if cc_db_value is not None:
                        # remove old cc_val
                        del_cc = getattr(book, cc_string)[0]
                        getattr(book, cc_string).remove(del_cc)
                        if not del_cc.books or len(del_cc.books) == 0:
                            calibre_db.session.delete(del_cc)
                            changed = True
            else:
                input_tags = to_save[cc_string].split(',')
                input_tags = list(map(lambda it: strip_whitespaces(it), input_tags))
                changed |= modify_database_object(input_tags,
                                                  getattr(book, cc_string),
                                                  db.cc_classes[c.id],
                                                  calibre_db.session,
                                                  'custom')
    # CWA: Export of changed metadata moved to do_edit_book after commit to avoid race conditions
    return changed


# returns False if an error occurs or no book is uploaded, in all other cases the ebook metadata to change is returned
def upload_book_formats(requested_files, book, book_id, no_cover=True):
    # Check and handle Uploaded file
    to_save = dict()
    error = False
    allowed_extensions = config.config_upload_formats.split(',')
    for requested_file in requested_files:
        current_filename = requested_file.filename
        if config.config_check_extensions and allowed_extensions != ['']:
            if not validate_mime_type(requested_file, allowed_extensions):
                flash(_("File type isn't allowed to be uploaded to this server"), category="error")
                error = True
                continue
        if current_filename != '':
            if not current_user.role_upload():
                flash(_("User has no rights to upload additional file formats"), category="error")
                error = True
                continue
            if '.' in current_filename:
                file_ext = current_filename.rsplit('.', 1)[-1].lower()
                if file_ext not in allowed_extensions and '' not in allowed_extensions:
                    flash(_("File extension '%(ext)s' is not allowed to be uploaded to this server", ext=file_ext),
                          category="error")
                    error = True
                    continue
            else:
                flash(_('File to be uploaded must have an extension'), category="error")
                error = True
                continue

            file_name = book.path.rsplit('/', 1)[-1]
            filepath = os.path.normpath(os.path.join(config.get_book_path(), book.path))
            saved_filename = os.path.join(filepath, file_name + '.' + file_ext)

            # check if file path exists, otherwise create it, copy file to calibre path and delete temp file
            if not os.path.exists(filepath):
                try:
                    os.makedirs(filepath)
                except OSError:
                    flash(_("Failed to create path %(path)s (Permission denied).", path=filepath),
                          category="error")
                    error = True
                    continue
            try:
                requested_file.save(saved_filename)
            except OSError:
                flash(_("Failed to store file %(file)s.", file=saved_filename), category="error")
                error = True
                continue

            file_size = os.path.getsize(saved_filename)

            # Format entry already exists, no need to update the database
            if calibre_db.get_book_format(book_id, file_ext.upper()):
                log.warning('Book format %s already existing', file_ext.upper())
            else:
                try:
                    db_format = db.Data(book_id, file_ext.upper(), file_size, file_name)
                    calibre_db.session.add(db_format)
                    calibre_db.session.commit()
                    calibre_db.create_functions(config)
                except (OperationalError, IntegrityError, StaleDataError) as e:
                    calibre_db.session.rollback()
                    log.error_or_exception("Database error: {}".format(e))
                    flash(_("Oops! Database Error: %(error)s.", error=e.orig if hasattr(e, "orig") else e),
                          category="error")
                    error = True
                    continue

            # Queue uploader info
            link = '<a href="{}">{}</a>'.format(url_for('web.show_book', book_id=book.id), escape(book.title))
            upload_text = N_("File format %(ext)s added to %(book)s", ext=file_ext.upper(), book=link)
            WorkerThread.add(current_user.name, TaskUpload(upload_text, escape(book.title)))
            meta = uploader.process(
                saved_filename,
                *os.path.splitext(current_filename),
                rar_executable=config.config_rarfile_location,
                no_cover=no_cover)
            merge_metadata(book, meta, to_save)
    return to_save, error


def upload_cover(cover_request, book):
    requested_file = cover_request.files.get('btn-upload-cover', None)
    if requested_file:
        # check for empty request
        if requested_file.filename != '':
            # Decouple cover updates from general uploads; require edit permission instead
            if not current_user.role_edit():
                flash(_("User has no rights to upload cover"), category="error")
                return False
            ret, message = helper.save_cover_with_thumbnail_update(requested_file, book.path, book.id)
            if ret is True:
                # Note: save_cover_with_thumbnail_update already triggers thumbnail generation
                # No need to call replace_cover_thumbnail_cache here (would create duplicate tasks)
                return True
            else:
                flash(message, category="error")
                return False
    return None


def handle_title_on_edit(book, book_title):
    # handle book title
    book_title = strip_whitespaces(book_title)
    if book.title != book_title:
        if book_title == '':
            book_title = _(u'Unknown')
        book.title = book_title
        return True
    return False


def handle_author_on_edit(book, author_name, update_stored=True):
    change = False
    input_authors = prepare_authors(author_name, config.get_book_path(), config.config_use_google_drive)

    # Search for each author if author is in database, if not, author name and sorted author name is generated new
    # everything then is assembled for sorted author field in database
    sort_authors_list = list()
    for inp in input_authors:
        stored_author = calibre_db.session.query(db.Authors).filter(db.Authors.name == inp).first()
        if not stored_author:
            stored_author = helper.get_sorted_author(inp.replace('|', ','))
        else:
            stored_author = stored_author.sort
        sort_authors_list.append(helper.get_sorted_author(stored_author))
    sort_authors = ' & '.join(sort_authors_list)
    if book.author_sort != sort_authors and update_stored:
        book.author_sort = sort_authors
        change = True

    change |= modify_database_object(input_authors, book.authors, db.Authors, calibre_db.session, 'author')

    return input_authors, change


def search_objects_remove(db_book_object, db_type, input_elements):
    del_elements = []
    for c_elements in db_book_object:
        found = False
        if db_type == 'custom':
            type_elements = c_elements.value
        else:
            type_elements = c_elements
        for inp_element in input_elements:
            if type_elements == inp_element:
                found = True
                break
        # if the element was not found in the new list, add it to remove list
        if not found:
            del_elements.append(c_elements)
    return del_elements


def search_objects_add(db_book_object, db_type, input_elements):
    add_elements = []
    for inp_element in input_elements:
        found = False
        for c_elements in db_book_object:
            if db_type == 'custom':
                type_elements = c_elements.value
            else:
                type_elements = c_elements
            if type_elements == inp_element:
                found = True
                break
        if not found:
            add_elements.append(inp_element)
    return add_elements


def remove_objects(db_book_object, db_session, del_elements):
    changed = False
    if len(del_elements) > 0:
        for del_element in del_elements:
            db_book_object.remove(del_element)
            changed = True
            if len(del_element.books) == 0:
                db_session.delete(del_element)
                db_session.flush()
    return changed


def add_objects(db_book_object, db_object, db_session, db_type, add_elements):
    changed = False
    if db_type == 'languages':
        db_filter = db_object.lang_code
    elif db_type == 'custom':
        db_filter = db_object.value
    else:
        db_filter = db_object.name
    for add_element in add_elements:
        # check if an element with that name exists
        changed = True
        db_element = db_session.query(db_object).filter(func.lower(db_filter).ilike(add_element)).all()
        # if no element is found add it
        if not db_element:
            if db_type == 'author':
                new_element = db_object(add_element, helper.get_sorted_author(add_element.replace('|', ',')))
            elif db_type == 'series':
                new_element = db_object(add_element, add_element)
            elif db_type == 'custom':
                new_element = db_object(value=add_element)
            elif db_type == 'publisher':
                new_element = db_object(add_element, None)
            else:  # db_type should be tag or language
                new_element = db_object(add_element)
            db_session.add(new_element)
            # Append new element (should not exist in collection, but check for safety)
            if new_element not in db_book_object:
                db_book_object.append(new_element)
        else:
            if len(db_element) == 1:
                db_element = create_objects_for_addition(db_element[0], add_element, db_type)
            else:
                db_el = db_session.query(db_object).filter(db_filter == add_element).first()
                db_element = db_element[0] if not db_el else db_el
            # add element to book only if not already present (prevents UNIQUE constraint errors)
            if db_element not in db_book_object:
                db_book_object.append(db_element)

    return changed


def create_objects_for_addition(db_element, add_element, db_type):
    if db_type == 'custom':
        if db_element.value != add_element:
            db_element.value = add_element
    elif db_type == 'languages':
        if db_element.lang_code != add_element:
            db_element.lang_code = add_element
    elif db_type == 'series':
        if db_element.name != add_element:
            db_element.name = add_element
            db_element.sort = add_element
    elif db_type == 'author':
        if db_element.name != add_element:
            db_element.name = add_element
            db_element.sort = helper.get_sorted_author(add_element.replace('|', ','))
    elif db_type == 'publisher':
        if db_element.name != add_element:
            db_element.name = add_element
            db_element.sort = None
    elif db_element.name != add_element:
        db_element.name = add_element
    return db_element


# Modifies different Database objects, first check if elements have to be deleted,
# because they are no longer used, than check if elements have to be added to database
def modify_database_object(input_elements, db_book_object, db_object, db_session, db_type):
    # passing input_elements not as a list may lead to undesired results
    if not isinstance(input_elements, list):
        raise TypeError(str(input_elements) + " should be passed as a list")
    input_elements = [x for x in input_elements if x != '']

    changed = False
    # If elements are renamed (upper lower case), rename it
    for rec_a, rec_b in zip(db_book_object, input_elements):
        if db_type == "custom":
            if rec_a.value.casefold() == rec_b.casefold() and rec_a.value != rec_b:
                create_objects_for_addition(rec_a, rec_b, db_type)
        else:
            if rec_a.get().casefold() == rec_b.casefold() and rec_a.get() != rec_b:
                create_objects_for_addition(rec_a, rec_b, db_type)
        # we have all input element (authors, series, tags) names now
    # 1. search for elements to remove
    del_elements = search_objects_remove(db_book_object, db_type, input_elements)
    # 2. search for elements that need to be added
    add_elements = search_objects_add(db_book_object, db_type, input_elements)

    # if there are elements to remove, we remove them now
    changed |= remove_objects(db_book_object, db_session, del_elements)
    # if there are elements to add, we add them now!
    if len(add_elements) > 0:
        changed |= add_objects(db_book_object, db_object, db_session, db_type, add_elements)
    return changed


def modify_identifiers(input_identifiers, db_identifiers, db_session):
    """Modify Identifiers to match input information.
       input_identifiers is a list of read-to-persist Identifiers objects.
       db_identifiers is a list of already persisted list of Identifiers objects."""
    changed = False
    error = False
    input_dict = {}
    for identifier in input_identifiers:
        identifier_type = (identifier.type or "").strip().lower()
        if not identifier_type:
            continue
        if identifier_type in input_dict:
            error = True
        input_dict[identifier_type] = identifier
    db_dict = {}
    for identifier in db_identifiers:
        identifier_type = (identifier.type or "").strip().lower()
        if not identifier_type:
            continue
        db_dict[identifier_type] = identifier
    # delete db identifiers not present in input or modify them with input val
    for identifier_type, identifier in db_dict.items():
        if identifier_type not in input_dict.keys():
            db_session.delete(identifier)
            changed = True
        else:
            input_identifier = input_dict[identifier_type]
            identifier.type = input_identifier.type
            identifier.val = input_identifier.val
    # add input identifiers not present in db
    for identifier_type, identifier in input_dict.items():
        if identifier_type not in db_dict.keys():
            db_session.add(identifier)
            changed = True
    return changed, error

from . import editbooks_upload  # noqa: E402,F401  (attaches its routes to this blueprint)
from .editbooks_upload import _ensure_ingest_dir_writable, _get_ingest_path, _save_to_ingest_atomic_rename, _validate_uploaded_file  # noqa: E402,F401

from . import editbooks_bulk  # noqa: E402,F401  (attaches its routes to this blueprint)
