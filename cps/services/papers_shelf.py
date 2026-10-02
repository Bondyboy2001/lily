# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Papers shelf: one shared shelf that a paper is filed on when its metadata is
fetched (the edit page, imports and Rebuild metadata), and, once, every paper already
in the library. A paper is a book whose metadata came from Scholar (arXiv, Semantic
Scholar, Crossref), or one with an arXiv id, or a DOI and no ISBN: a textbook with a
publisher's DOI keeps its ISBN and stays off."""

import os
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from cps import constants, logger, ub

log = logger.create()

PAPERS_SHELF = "Papers"
SCHOLAR = "googlescholar"


def is_paper(identifiers, source_id=None):
    """True for a paper: metadata from Scholar, an arXiv id, or a DOI and no ISBN."""
    if source_id == SCHOLAR:
        return True
    kinds = {str(kind).lower() for kind, val in (identifiers or {}).items() if val and str(val).strip()}
    return "arxiv" in kinds or ("doi" in kinds and "isbn" not in kinds)


def papers_shelf(session, create=True):
    """The shelf named Papers (in any case), a public one first. With none, a public
    one owned by the first admin is made, unless `create` is false."""
    shelf = session.query(ub.Shelf).filter(func.lower(ub.Shelf.name) == PAPERS_SHELF.lower()) \
        .order_by(ub.Shelf.is_public.desc(), ub.Shelf.id).first()
    if shelf or not create:
        return shelf
    owner = session.query(ub.User).filter(
        ub.User.role.op('&')(constants.ROLE_ADMIN) == constants.ROLE_ADMIN).order_by(ub.User.id).first()
    if owner is None:
        return None
    shelf = ub.Shelf(name=PAPERS_SHELF, is_public=1, user_id=owner.id)
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


def file_papers(book_ids):
    """Put the books on the Papers shelf; how many were added. It opens a session of its
    own: imports run in a process with no app session, Rebuild metadata on a worker thread."""
    if not ub.app_DB_path:
        raise RuntimeError("app.db path is not set")
    engine = ub._create_app_db_engine(ub.app_DB_path)
    session = Session(engine)
    try:
        shelf = papers_shelf(session)
        added = add_to_shelf(session, shelf, book_ids) if shelf else 0
        session.commit()
        return added
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        engine.dispose()


def paper_book_ids():
    """The ids of the library's books whose identifiers make them papers, in id order."""
    from cps import db
    cdb = db.CalibreDB(expire_on_commit=False, init=True)
    try:
        ids = {}
        for book_id, kind, val in cdb.session.query(db.Identifiers.book, db.Identifiers.type, db.Identifiers.val) \
                .filter(func.lower(db.Identifiers.type).in_(("arxiv", "doi", "isbn"))):
            ids.setdefault(book_id, {})[kind.lower()] = val
    finally:
        cdb.session.close()
    return sorted(book_id for book_id, found in ids.items() if is_paper(found))


def backfill_once(marker_path):
    """File every paper already in the library, the first time only, so a paper taken
    off the shelf by hand stays off; how many were added."""
    from cps import db
    if os.path.exists(marker_path) or not db.CalibreDB.session_factory:
        return 0
    book_ids = paper_book_ids()
    added = file_papers(book_ids) if book_ids else 0
    os.makedirs(os.path.dirname(marker_path), exist_ok=True)
    with open(marker_path, "w", encoding="utf-8") as marker:
        marker.write(datetime.now(timezone.utc).isoformat())
    if added:
        log.info("Filed %d papers on the Papers shelf", added)
    return added
