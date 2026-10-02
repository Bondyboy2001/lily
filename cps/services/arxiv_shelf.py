# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""The arXiv shelf: one shared shelf that a paper is filed on when its metadata is fetched
from arXiv (the edit page, imports and Rebuild metadata). It replaced the Papers shelf, which
took every paper: a one-time pass at startup puts the library's arXiv papers on the arXiv
shelf and removes the Papers shelf."""

import os
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from cps import constants, logger, ub

log = logger.create()

ARXIV_SHELF = "arXiv"
# The shelf this one replaced
_PAPERS_SHELF = "Papers"


def _shelf_named(session, name):
    """The shelf of that name (in any case), a public one first."""
    return session.query(ub.Shelf).filter(func.lower(ub.Shelf.name) == name.lower()) \
        .order_by(ub.Shelf.is_public.desc(), ub.Shelf.id).first()


def arxiv_shelf(session, create=True):
    """The shelf named arXiv (in any case), a public one first. With none, a public
    one owned by the first admin is made, unless `create` is false."""
    shelf = _shelf_named(session, ARXIV_SHELF)
    if shelf or not create:
        return shelf
    owner = session.query(ub.User).filter(
        ub.User.role.op('&')(constants.ROLE_ADMIN) == constants.ROLE_ADMIN).order_by(ub.User.id).first()
    if owner is None:
        return None
    shelf = ub.Shelf(name=ARXIV_SHELF, is_public=1, user_id=owner.id)
    session.add(shelf)
    session.flush()
    return shelf


def add_to_shelf(session, shelf, book_ids):
    """Add the books not on the shelf yet at its end; how many were added."""
    have = {book_id for (book_id,) in session.query(ub.BookShelf.book_id).filter(ub.BookShelf.shelf == shelf.id)}
    order = session.query(func.max(ub.BookShelf.order)).filter(ub.BookShelf.shelf == shelf.id).scalar() or 0
    added = 0
    for book_id in book_ids:
        if book_id in have:
            continue
        have.add(book_id)
        order += 1
        # Through the shelf: ub's before_flush hook bumps its last_modified
        shelf.books.append(ub.BookShelf(shelf=shelf.id, book_id=book_id, order=order))
        added += 1
    return added


@contextmanager
def _app_session():
    """A session on app.db of its own, committed at the end: imports run in a process with
    no app session, Rebuild metadata on a worker thread."""
    if not ub.app_DB_path:
        raise RuntimeError("app.db path is not set")
    engine = ub._create_app_db_engine(ub.app_DB_path)
    try:
        with Session(engine) as session, session.begin():
            yield session
    finally:
        engine.dispose()


def file_on_shelf(book_ids):
    """Put the books on the arXiv shelf; how many were added."""
    with _app_session() as session:
        shelf = arxiv_shelf(session)
        return add_to_shelf(session, shelf, book_ids) if shelf else 0


def arxiv_book_ids():
    """The ids of the library's books that have an arXiv id, in id order."""
    from cps import db
    # A session of its own: the pass runs at startup on the thread that serves requests,
    # where db.CalibreDB(init=True) is handed the app's session and changes its settings
    with Session(db.CalibreDB.engine) as session:
        return sorted({book_id for (book_id,) in session.query(db.Identifiers.book)
                       .filter(func.lower(db.Identifiers.type) == "arxiv")})


def replace_papers_shelf_once(marker_path):
    """Put the arXiv papers already in the library on the arXiv shelf and remove the Papers
    shelf it replaced, the first time only, so a paper taken off the shelf by hand stays off
    and a Papers shelf made later is left alone; how many were shelved."""
    from cps import db
    if os.path.exists(marker_path) or not db.CalibreDB.session_factory:
        return 0
    book_ids = arxiv_book_ids()
    with _app_session() as session:
        shelf = arxiv_shelf(session) if book_ids else None
        added = add_to_shelf(session, shelf, book_ids) if shelf else 0
        papers = _shelf_named(session, _PAPERS_SHELF)
        removed = papers is not None
        if removed:
            session.query(ub.BookShelf).filter(ub.BookShelf.shelf == papers.id).delete()
            session.delete(papers)
    os.makedirs(os.path.dirname(marker_path), exist_ok=True)
    with open(marker_path, "w", encoding="utf-8") as marker:
        marker.write(datetime.now(timezone.utc).isoformat())
    if added or removed:
        log.info("Put %d arXiv papers on the arXiv shelf%s", added, "; removed the Papers shelf" if removed else "")
    return added
