# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Tags are the user's own: nothing adds them but an edit by hand.

calibre turns a file's subjects and a PDF's Keywords field into tags on import, so an import
clears them again (metadata lookups never add any either)."""


def delete_unused_tags(session):
    """Remove tags no book uses any more; returns how many. The caller commits."""
    from cps import db
    unused = session.query(db.Tags).filter(~db.Tags.id.in_(
        session.query(db.books_tags_link.c.tag))).all()
    for tag in unused:
        session.delete(tag)
    return len(unused)


def clear_new_book_tags(book_id):
    """Drop the tags calibre read from a newly imported file; True when it had any."""
    from cps import db
    cdb = db.CalibreDB(expire_on_commit=False, init=True)
    try:
        book = cdb.get_book(book_id)
        if book is None or not book.tags:
            return False
        book.tags = []
        cdb.session.flush()
        delete_unused_tags(cdb.session)
        cdb.session.commit()
        return True
    except Exception:
        cdb.session.rollback()
        raise
    finally:
        cdb.session.close()
