# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Query-string filters for the library book grids (format, language, read status, tag, and
what the book's last metadata lookup found).

Filters arrive as query parameters (?format=EPUB&lang=eng&status=unread&tag=12&metadata=failed), so they survive
pagination (url_for_other_page copies request.args) and are carried into the sort links. They are
AND-ed into the page's own db_filter; calibre_db.fill_indexpage still applies common_filters, so
user tag/language/custom-column restrictions keep working.
"""
from flask import request, url_for
from sqlalchemy import bindparam, select
from sqlalchemy.sql.expression import true, and_

from . import config, db, logger, ub
from .cw_login import current_user

log = logger.create()

FILTER_PARAMS = ("format", "lang", "status", "tag", "metadata")
STATUS_CHOICES = ("unread", "reading", "read")
# What a book's last metadata lookup found (cwa.db's metadata_lookups), unchecked for none yet,
# or changed: a lookup changed it and Book Details can undo that (metadata_changes)
METADATA_CHOICES = ("matched", "nomatch", "failed", "unchecked", "changed")


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
        if key == "metadata" and value not in METADATA_CHOICES:
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
    if "metadata" in active:
        clauses.append(_metadata_clause(active["metadata"]))
    status = active.get("status")
    if status == "read":
        clauses.append(db.Books.id.in_(_finished_subquery()))
    elif status == "reading":
        clauses.append(db.Books.id.in_(_read_status_subquery(ub.ReadBook.STATUS_IN_PROGRESS)))
    elif status == "unread":
        clauses.append(db.Books.id.notin_(_finished_subquery()))
        clauses.append(db.Books.id.notin_(_read_status_subquery(ub.ReadBook.STATUS_IN_PROGRESS)))
    return and_(*clauses) if clauses else true()


def _metadata_clause(choice):
    """Books whose last lookup found `choice`, that have had none (unchecked), or that a lookup
    changed (changed). The lookups
    live in cwa.db, not the library, so their ids are written into the query as literals: a
    library can have more books than SQLite takes bound parameters."""
    from cwa_db import CWA_DB
    try:
        with CWA_DB() as store:
            if choice == "changed":
                ids = store.metadata_changed_ids()
            else:
                ids = store.metadata_lookup_ids(None if choice == "unchecked" else choice)
    except Exception as e:
        log.error("Could not read the metadata lookups: %s", e)
        ids = []
    ids_param = bindparam("metadata_lookup_ids", ids, expanding=True, literal_execute=True)
    if choice == "unchecked":
        return db.Books.id.notin_(ids_param)
    return db.Books.id.in_(ids_param)


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


def filter_context():
    """What the book grids need from the active filters: the filters, their query args and a URL builder."""
    active = active_filters()
    return {"active": active, "args": dict(active), "url": filter_url,
            "metadata_choices": METADATA_CHOICES}
