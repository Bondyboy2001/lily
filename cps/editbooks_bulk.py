# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Bulk endpoints for the duplicates page: list, delete and merge the selected books.

Routes are attached to the editbook blueprint; editbooks.py imports this module at its end."""

import json


from flask import request, Response, jsonify
from flask_babel import gettext as _

from . import calibre_db, ub
from .book_recovery import RecoveryError
from .cw_login import current_user
from .usermanagement import user_login_required


from .editbooks import (editbook, log, _queue_duplicate_scan_after_change, perform_delete,
                        merge_books, edit_required, delete_required)

MAX_BATCH_IDS = 1000


def parse_batch_ids(raw):
    """Validate a batch id list: positive non-bool ints, deduplicated, capped.
    Returns (ids, error_response)."""
    if not isinstance(raw, list) or not raw:
        return None, (jsonify({"success": False, "msg": _("No books selected")}), 400)
    ids = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return None, (jsonify({"success": False, "msg": _("Invalid book id in selection")}), 400)
        if value not in ids:
            ids.append(value)
    if len(ids) > MAX_BATCH_IDS:
        return None, (jsonify({"success": False, "msg": _("Too many books selected")}), 400)
    return ids, None


def batch_response(results, status=200):
    """Common truthful shape: success only when every requested item succeeded."""
    summary = {"succeeded": 0, "failed": 0, "skipped": 0}
    for entry in results:
        summary[entry.get("status", "failed")] = summary.get(entry.get("status", "failed"), 0) + 1
    ok = bool(results) and all(entry.get("status") == "succeeded" for entry in results)
    return Response(json.dumps({"success": ok, "results": results, "summary": summary}),
                    mimetype='application/json', status=status)


@editbook.route("/ajax/displayselectedbooks", methods=['POST'])
@user_login_required
@edit_required
def display_selected_books():
    d = request.get_json(silent=True)
    vals = d.get('selections') if isinstance(d, dict) else None
    books = []
    if isinstance(vals, list):
        for book_id in vals:
            if isinstance(book_id, bool) or not isinstance(book_id, int) or book_id <= 0:
                continue
            book = calibre_db.get_filtered_book(book_id)
            books.append(book.title if book else _("Book %(id)s not found", id=book_id))
        return json.dumps({'books': books})
    return ""

@editbook.route("/ajax/deleteselectedbooks", methods=['POST'])
@user_login_required
@edit_required
def delete_selected_books():
    if not current_user.role_delete_books():
        return jsonify({"success": False, "msg": _("You are missing permissions to delete books")}), 403
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return batch_response([], status=400)
    ids, error = parse_batch_ids(d.get('selections'))
    if error is not None:
        return error
    results = [perform_delete(book_id) for book_id in ids]
    _queue_duplicate_scan_after_change(
        [e["book_id"] for e in results if e["status"] == "succeeded"])
    return batch_response(results)

@editbook.route("/ajax/mergebooks", methods=['POST'])
@user_login_required
@edit_required
@delete_required
def merge_list_book():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return batch_response([], status=400)
    vals = d.get('Merge_books')
    ids, error = parse_batch_ids(vals)
    if error is not None:
        return error
    results = [{"book_id": book_id, "status": "failed", "message": ""} for book_id in ids[1:]]
    to_book = calibre_db.get_filtered_book(ids[0])
    if not to_book:
        for entry in results:
            entry["message"] = str(_("Target book not found"))
        return batch_response(results)
    sources = []
    for book_id in ids[1:]:
        source = calibre_db.get_filtered_book(book_id)
        if source:
            sources.append(source)
    missing = {e["book_id"] for e in results} - {s.id for s in sources}
    for entry in results:
        if entry["book_id"] in missing:
            entry["message"] = str(_("Book not found"))
    if not sources:
        return batch_response(results)
    try:
        merged, _recovery_ids = merge_books(to_book, sources)
    except RecoveryError as e:
        for entry in results:
            if entry["book_id"] not in missing:
                entry["message"] = str(e)
        return batch_response(results)
    except Exception as e:
        calibre_db.session.rollback()
        ub.session.rollback()
        log.error_or_exception("Merge failed: %s", e)
        for entry in results:
            if entry["book_id"] not in missing:
                entry["message"] = str(e)
        return batch_response(results)
    by_id = {entry["book_id"]: entry for entry in merged}
    for entry in results:
        if entry["book_id"] in by_id:
            entry.update(by_id[entry["book_id"]])
        elif entry["book_id"] not in missing:
            entry["status"] = "skipped"
            entry["message"] = str(_("Not processed"))
    _queue_duplicate_scan_after_change([to_book.id] + ids[1:])
    return batch_response(results)
