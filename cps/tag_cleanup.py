# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Lily keeps no descriptions, publishers, languages or ratings, and no tags but the ones a user
types: calibre reads them from a file on import, so an import clears them again (metadata
lookups never add any either). The file's published date stays."""


def delete_unused(session):
    """Remove tags, publishers, languages and ratings no book uses any more; returns how many.
    The caller commits."""
    from cps import db
    removed = 0
    for model, link, column in ((db.Tags, db.books_tags_link, "tag"),
                                (db.Publishers, db.books_publishers_link, "publisher"),
                                (db.Languages, db.books_languages_link, "lang_code"),
                                (db.Ratings, db.books_ratings_link, "rating")):
        unused = session.query(model).filter(~model.id.in_(session.query(getattr(link.c, column)))).all()
        for row in unused:
            session.delete(row)
        removed += len(unused)
    return removed


def clear_new_book_details(book_id):
    """Drop the description, tags, publisher, languages and rating calibre read from a newly
    imported file; True when it had any."""
    from cps import db
    cdb = db.CalibreDB(expire_on_commit=False, init=True)
    try:
        book = cdb.get_book(book_id)
        if book is None:
            return False
        description = cdb.session.query(db.Comments).filter(db.Comments.book == book_id)
        if not (book.tags or book.publishers or book.languages or book.ratings or description.count()):
            return False
        book.tags, book.publishers, book.languages, book.ratings = [], [], [], []
        description.delete(synchronize_session=False)
        cdb.session.flush()
        delete_unused(cdb.session)
        cdb.session.commit()
        return True
    except Exception:
        cdb.session.rollback()
        raise
    finally:
        cdb.session.close()
