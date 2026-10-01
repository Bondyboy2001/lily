# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Admin review of metadata suggestions (see cps/tasks/suggest_metadata.py).

Accepting a suggestion fills gaps only: a description is added when the book has none,
identifiers are added when the book lacks that type. Existing values are never replaced.
"""

import json
from datetime import datetime, timezone

from flask import Blueprint, flash, redirect, url_for
from flask_babel import gettext as _
from markupsafe import Markup

from . import calibre_db, db, logger, ub
from .admin import admin_required, _tasks_page_link
from .clean_html import clean_string
from .cw_login import current_user
from .render_template import render_title_template
from .services.worker import WorkerThread
from .usermanagement import user_login_required

suggestions = Blueprint('suggestions', __name__)
log = logger.create()

STATUS_PENDING = 'pending'


def _now():
    return datetime.now(timezone.utc).isoformat()


def _load_scripts():
    from .tasks import suggest_metadata  # noqa: F401 (puts scripts/ on sys.path)
    from metadata_suggestions import HIGH_CONFIDENCE, fill_fields
    return HIGH_CONFIDENCE, fill_fields


def _current_state(book):
    comment = next((c.text for c in book.comments if c.text), "")
    return {"description": comment, "identifiers": {i.type: i.val for i in book.identifiers}}


def apply_suggestion(row):
    """Applies one pending suggestion to its book. Returns what was actually added
    ({} when the book has since gained everything the suggestion offered, or is gone)."""
    _, fill_fields = _load_scripts()
    book = calibre_db.get_book(row.book_id)
    if book is None:
        return {}
    to_add = fill_fields(_current_state(book), json.loads(row.fill))
    if not to_add:
        return {}
    if "description" in to_add:
        # Suggested descriptions come from metadata providers; store them cleaned
        to_add["description"] = clean_string(to_add["description"], book.id)
        existing = calibre_db.session.query(db.Comments).filter(db.Comments.book == book.id).first()
        if existing:
            existing.text = to_add["description"]
        else:
            calibre_db.session.add(db.Comments(to_add["description"], book.id))
    for id_type, value in to_add.get("identifiers", {}).items():
        calibre_db.session.add(db.Identifiers(value, id_type, book.id))
    book.last_modified = datetime.now(timezone.utc)
    calibre_db.session.commit()
    return to_add


def _finish(row, status):
    row.status = status
    row.reviewed_at = _now()
    row.reviewed_by = current_user.name


def _back():
    return redirect(url_for('suggestions.review_suggestions'))


def _accept(row):
    try:
        added = apply_suggestion(row)
    except Exception as e:
        calibre_db.session.rollback()
        log.error("Applying metadata suggestion %s failed: %s", row.id, e)
        return None
    _finish(row, 'accepted' if added else 'rejected')
    return added


@suggestions.route('/admin/metadata/suggestions', methods=['GET'])
@user_login_required
@admin_required
def review_suggestions():
    pending = ub.session.query(ub.MetadataSuggestion).filter(
        ub.MetadataSuggestion.status == STATUS_PENDING).order_by(
        ub.MetadataSuggestion.score.desc(), ub.MetadataSuggestion.id).all()
    items = []
    for row in pending:
        fill = json.loads(row.fill)
        items.append({"row": row, "description": fill.get("description", ""),
                      "identifiers": fill.get("identifiers", {}), "percent": round(row.score * 100)})
    from .tasks.suggest_metadata import NO_MATCH
    looked_up = ub.session.query(ub.MetadataSuggestion.book_id).distinct().count()
    no_match = ub.session.query(ub.MetadataSuggestion).filter(ub.MetadataSuggestion.status == NO_MATCH).count()
    high, _ff = _load_scripts()
    return render_title_template("metadata_suggestions.html", title=_("Metadata Suggestions"),
                                 page="metadata-suggestions", items=items, high_percent=round(high * 100),
                                 high_count=sum(1 for i in items if i["row"].score >= high),
                                 looked_up=looked_up, no_match=no_match)


@suggestions.route('/admin/metadata/suggestions/<int:suggestion_id>/<action>', methods=['POST'])
@user_login_required
@admin_required
def suggestion_action(suggestion_id, action):
    row = ub.session.query(ub.MetadataSuggestion).filter(
        ub.MetadataSuggestion.id == suggestion_id, ub.MetadataSuggestion.status == STATUS_PENDING).first()
    if row is None or action not in ('accept', 'reject'):
        flash(_("That suggestion is no longer pending."), category="error")
        return _back()
    if action == 'reject':
        _finish(row, 'rejected')
    else:
        added = _accept(row)
        if added is None:
            flash(_("Could not apply the suggestion; see the server log."), category="error")
            return _back()
        if not added:
            flash(_("The book already has everything this suggestion offered."), category="info")
    ub.session_commit()
    return _back()


@suggestions.route('/admin/metadata/suggestions/bulk/<action>', methods=['POST'])
@user_login_required
@admin_required
def suggestion_bulk(action):
    if action not in ('accept_high', 'reject_all'):
        return redirect(url_for('suggestions.review_suggestions'))
    high, _ff = _load_scripts()
    query = ub.session.query(ub.MetadataSuggestion).filter(ub.MetadataSuggestion.status == STATUS_PENDING)
    if action == 'accept_high':
        done = failed = 0
        for row in query.filter(ub.MetadataSuggestion.score >= high).order_by(ub.MetadataSuggestion.score.desc()):
            if _accept(row) is None:
                failed += 1
            else:
                done += 1
        ub.session_commit()
        flash(_("Accepted %(n)d high-confidence suggestion(s).", n=done), category="success")
        if failed:
            flash(_("%(n)d could not be applied; see the server log.", n=failed), category="error")
    else:
        rows = query.all()
        for row in rows:
            _finish(row, 'rejected')
        ub.session_commit()
        flash(_("Rejected %(n)d suggestion(s).", n=len(rows)), category="success")
    return _back()


@suggestions.route('/admin/metadata/suggestions/run', methods=['POST'])
@user_login_required
@admin_required
def run_suggestions():
    from .tasks.suggest_metadata import TaskSuggestMetadata
    WorkerThread.add(current_user.name, TaskSuggestMetadata())
    flash(Markup(_("Looking up metadata. Follow its progress on the %(link)s page, then come back here.",
                   link=_tasks_page_link())), category="success")
    return _back()
