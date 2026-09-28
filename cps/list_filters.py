# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Filter chips for the library book grids (format, language, read status, tag).

Filters arrive as query parameters (?format=EPUB&lang=eng&status=unread&tag=12), so they survive
pagination (url_for_other_page copies request.args) and are carried into the sort links. They are
AND-ed into the page's own db_filter; calibre_db.fill_indexpage still applies common_filters, so
user tag/language/custom-column restrictions keep working.
"""
from flask import request, url_for
from flask_babel import gettext as _
from flask_babel import get_locale
from sqlalchemy import select, distinct
from sqlalchemy.sql.expression import func, true, and_

from . import calibre_db, config, db, isoLanguages, logger, ub
from .cw_login import current_user

log = logger.create()

FILTER_PARAMS = ("format", "lang", "status", "tag")
STATUS_CHOICES = ("unread", "reading", "read")
TAG_OPTION_LIMIT = 40


def _read_status_subquery(status):
    return (select(ub.ReadBook.book_id)
            .where(ub.ReadBook.user_id == int(current_user.id))
            .where(ub.ReadBook.read_status == status))


def _finished_subquery():
    """Book ids the current user has finished, from the configured read column if there is one."""
    if config.config_read_column:
        try:
            read_column = db.cc_classes[config.config_read_column]
            return select(read_column.book).where(read_column.value == True)
        except (KeyError, AttributeError, IndexError):
            log.error("Custom Column No.%s does not exist in calibre database", config.config_read_column)
    return _read_status_subquery(ub.ReadBook.STATUS_FINISHED)


def active_filters():
    """The valid filter parameters on this request, as {param: value}."""
    if current_user.is_anonymous:
        allowed = ("format", "lang", "tag")
    else:
        allowed = FILTER_PARAMS
    active = {}
    for key in allowed:
        value = (request.args.get(key) or "").strip()
        if not value:
            continue
        if key == "status" and value not in STATUS_CHOICES:
            continue
        if key == "tag" and not value.isdigit():
            continue
        if key == "format":
            value = value.upper()
        active[key] = value
    return active


def filter_expression(active=None):
    """SQLAlchemy expression for the active filters (true() when none)."""
    active = active_filters() if active is None else active
    clauses = []
    if "format" in active:
        clauses.append(db.Books.data.any(db.Data.format == active["format"]))
    if "lang" in active:
        clauses.append(db.Books.languages.any(db.Languages.lang_code == active["lang"]))
    if "tag" in active:
        clauses.append(db.Books.tags.any(db.Tags.id == int(active["tag"])))
    status = active.get("status")
    if status == "read":
        clauses.append(db.Books.id.in_(_finished_subquery()))
    elif status == "reading":
        clauses.append(db.Books.id.in_(_read_status_subquery(ub.ReadBook.STATUS_IN_PROGRESS)))
    elif status == "unread":
        clauses.append(db.Books.id.notin_(_finished_subquery()))
        clauses.append(db.Books.id.notin_(_read_status_subquery(ub.ReadBook.STATUS_IN_PROGRESS)))
    return and_(*clauses) if clauses else true()


def filter_url(**changes):
    """This page's URL with filters changed (None removes one) and pagination reset."""
    args = dict(request.view_args or {})
    if "page" in args:
        args["page"] = 1
    for key, value in request.args.items():
        args[key] = value
    for key, value in changes.items():
        if value is None:
            args.pop(key, None)
        else:
            args[key] = value
    return url_for(request.endpoint, **args)


def _format_options():
    rows = (calibre_db.session.query(db.Data.format, func.count(distinct(db.Data.book)))
            .join(db.Books, db.Books.id == db.Data.book)
            .filter(calibre_db.common_filters())
            .group_by(db.Data.format)
            .order_by(func.count(distinct(db.Data.book)).desc())
            .all())
    return [fmt for fmt, _count in rows if fmt]


def _tag_options():
    count = func.count(distinct(db.books_tags_link.c.book))
    rows = (calibre_db.session.query(db.Tags.id, db.Tags.name, count)
            .join(db.books_tags_link, db.books_tags_link.c.tag == db.Tags.id)
            .join(db.Books, db.Books.id == db.books_tags_link.c.book)
            .filter(calibre_db.common_filters())
            .group_by(db.Tags.id)
            .order_by(count.desc())
            .limit(TAG_OPTION_LIMIT)
            .all())
    return sorted(((str(tag_id), name) for tag_id, name, _count in rows), key=lambda t: t[1].lower())


def _language_options():
    if current_user.filter_language() != "all":
        return []
    languages = calibre_db.speaking_language()
    return [(lang.lang_code, lang.name) for lang in languages]


def filter_context():
    """Everything the chip row in image.html needs. Failures degrade to no chips, never a 500."""
    active = active_filters()
    context = {"active": active, "args": dict(active), "url": filter_url,
               "formats": [], "languages": [], "tags": [],
               "show_status": not current_user.is_anonymous,
               "status_labels": {"unread": _("Unread"), "reading": _("In progress"), "read": _("Read")}}
    try:
        context["formats"] = _format_options()
        context["languages"] = _language_options()
        context["tags"] = _tag_options()
    except Exception as ex:  # a broken option query must not take the library page down
        log.debug("Could not load filter options: %s", ex)
    if "tag" in active and not any(t[0] == active["tag"] for t in context["tags"]):
        tag = calibre_db.session.query(db.Tags).filter(db.Tags.id == int(active["tag"])).first()
        if tag:
            context["tags"].append((active["tag"], tag.name))
    if "lang" in active and not any(l[0] == active["lang"] for l in context["languages"]):
        try:
            name = isoLanguages.get_language_name(get_locale(), active["lang"])
        except Exception:
            name = active["lang"]
        context["languages"].append((active["lang"], name))
    context["tag_name"] = dict(context["tags"]).get(active.get("tag"))
    context["lang_name"] = dict(context["languages"]).get(active.get("lang"))
    return context
