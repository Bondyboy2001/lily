# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Bulk and list-editing endpoints: selected-books actions, merge, sort values and author/title swap.

Routes are attached to the editbook blueprint; editbooks.py imports this module at its end."""

import os
import json
from shutil import copyfile


from flask import request, Response
from sqlalchemy.exc import OperationalError, IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from . import helper
from . import config, db, calibre_db
from .helper import change_archived_books
from .usermanagement import user_login_required, login_required_if_no_ano

from datetime import datetime, timezone
from . import gdriveutils

from .editbooks import editbook, log, _queue_duplicate_scan_after_change, delete_book_from_table, edit_required, handle_author_on_edit, handle_title_on_edit


@editbook.route("/ajax/sort_value/<field>/<int:bookid>")
@user_login_required
def get_sorted_entry(field, bookid):
    if field in ['title', 'authors', 'sort', 'author_sort']:
        book = calibre_db.get_filtered_book(bookid)
        if book:
            if field == 'title':
                return json.dumps({'sort': book.sort})
            elif field == 'authors':
                return json.dumps({'author_sort': book.author_sort})
            if field == 'sort':
                return json.dumps({'sort': book.title})
            if field == 'author_sort':
                return json.dumps({'authors': " & ".join([a.name for a in calibre_db.order_authors([book])])})
    return ""


@editbook.route("/ajax/simulatemerge", methods=['POST'])
@user_login_required
@edit_required
def simulate_merge_list_book():
    vals = request.get_json().get('Merge_books')
    if vals:
        to_book = calibre_db.get_book(vals[0]).title
        vals.pop(0)
        if to_book:
            from_book = []
            for book_id in vals:
                from_book.append(calibre_db.get_book(book_id).title)
            return json.dumps({'to': to_book, 'from': from_book})
    return ""


@editbook.route("/ajax/displayselectedbooks", methods=['POST'])
@user_login_required
@edit_required
def display_selected_books():
    vals = request.get_json().get('selections')
    books = []
    if vals:
        for book_id in vals:
            books.append(calibre_db.get_book(book_id).title)
        return json.dumps({'books': books})
    return ""

@editbook.route("/ajax/archiveselectedbooks", methods=['POST'])
@login_required_if_no_ano
@edit_required
def archive_selected_books():
    vals = request.get_json().get('selections')
    state = request.get_json().get('archive')
    if vals:
        for book_id in vals:
            change_archived_books(book_id, state,
                                  message="Book {} archive bit set to: {}".format(book_id, state))
        return json.dumps({'success': True})
    return ""

@editbook.route("/ajax/deleteselectedbooks", methods=['POST'])
@user_login_required
@edit_required
def delete_selected_books():
    vals = request.get_json().get('selections')
    if vals:
        for book_id in vals:
            delete_book_from_table(book_id, "", True, reason="bulk delete")
        _queue_duplicate_scan_after_change(vals)
        return json.dumps({'success': True})
    return ""

@editbook.route("/ajax/readselectedbooks", methods=['POST'])
@user_login_required
@edit_required
def read_selected_books():
    vals = request.get_json().get('selections')
    markAsRead = request.get_json().get('markAsRead')
    if vals:
        try:
            for book_id in vals:
                ret = helper.edit_book_read_status(book_id, markAsRead)
                # edit_book_read_status returns an error message (truthy) or "" on success
                if ret:
                    return json.dumps({'success': False, 'msg': ret})

        except (OperationalError, IntegrityError, StaleDataError) as e:
            calibre_db.session.rollback()
            log.error_or_exception("Database error: {}".format(e))
            return Response(json.dumps({'success': False,
                    'msg': 'Database error: {}'.format(e.orig if hasattr(e, "orig") else e)}),
                    mimetype='application/json')

        return json.dumps({'success': True})
    return ""


@editbook.route("/ajax/mergebooks", methods=['POST'])
@user_login_required
@edit_required
def merge_list_book():
    vals = request.get_json().get('Merge_books')
    to_file = list()
    if vals:
        # load all formats from target book
        to_book = calibre_db.get_book(vals[0])
        vals.pop(0)
        if to_book:
            for file in to_book.data:
                to_file.append(file.format)
            to_name = helper.get_valid_filename(to_book.title,
                                                chars=96) + ' - ' + helper.get_valid_filename(to_book.authors[0].name,
                                                                                              chars=96)
            for book_id in vals:
                from_book = calibre_db.get_book(book_id)
                if from_book:
                    for element in from_book.data:
                        if element.format not in to_file:
                            # create new data entry with: book_id, book_format, uncompressed_size, name
                            filepath_new = os.path.normpath(os.path.join(config.get_book_path(),
                                                                         to_book.path,
                                                                         to_name + "." + element.format.lower()))
                            filepath_old = os.path.normpath(os.path.join(config.get_book_path(),
                                                                         from_book.path,
                                                                         element.name + "." + element.format.lower()))
                            copyfile(filepath_old, filepath_new)
                            to_book.data.append(db.Data(to_book.id,
                                                        element.format,
                                                        element.uncompressed_size,
                                                        to_name))
                            to_file.append(element.format)
                    delete_book_from_table(from_book.id, "", True, reason="merged into %d" % to_book.id)
            calibre_db.session.commit()
            _queue_duplicate_scan_after_change([to_book.id] + vals)
            return json.dumps({'success': True})
    return ""


@editbook.route("/ajax/xchange", methods=['POST'])
@user_login_required
@edit_required
def table_xchange_author_title():
    vals = request.get_json().get('xchange')
    edited_books_id = False
    if vals:
        for val in vals:
            modify_date = False
            book = calibre_db.get_book(val)
            authors = book.title
            book.authors = calibre_db.order_authors([book])
            author_names = []
            for authr in book.authors:
                author_names.append(authr.name.replace('|', ','))

            title_change = handle_title_on_edit(book, " ".join(author_names))
            input_authors, author_change = handle_author_on_edit(book, authors)
            if author_change or title_change:
                edited_books_id = book.id
                modify_date = True

            if config.config_use_google_drive:
                gdriveutils.updateGdriveCalibreFromLocal()

            dir_error = None
            if edited_books_id:
                # Returns False on success, or an error message when the move failed.
                dir_error = helper.update_dir_structure(
                    edited_books_id, config.get_book_path(), input_authors[0])
                if dir_error:
                    log.error("Directory structure update failed for book {}: {}",
                              edited_books_id, dir_error)
            if modify_date:
                book.last_modified = datetime.now(timezone.utc)
                calibre_db.set_metadata_dirty(book.id)
            try:
                calibre_db.session.commit()
            except (OperationalError, IntegrityError, StaleDataError) as e:
                calibre_db.session.rollback()
                log.error_or_exception("Database error: {}".format(e))
                return json.dumps({'success': False})

            if config.config_use_google_drive:
                gdriveutils.updateGdriveCalibreFromLocal()

            if dir_error:
                # The metadata edit is saved, but the files were not moved, so the book
                # path no longer matches the database. Report it instead of claiming success.
                return json.dumps({'success': False, 'msg': str(dir_error)})
        return json.dumps({'success': True})
    return ""
