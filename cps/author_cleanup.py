# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Keeps authors to people's names.

calibre takes a PDF's author from the file's own details, and those are often a list of
people in one name ("Stefano Bellucci, Sergio Ferrara, Alessio Marrani", "Akcoglu, Mustafa
A.,Ha, Dzung Minh."), a catalogue entry ("Murray, Francis J. (Francis Joseph), 1911-1996."),
a name in capitals, or not a name at all: a publisher's id ("0000253"), the account that made
the file ("Administrator@NEO-10"), a typesetter's job stamp or a download site's signature.

clean_author_names turns one such name into the people it names. Rebuild metadata runs
tidy_authors before its lookups and imports run tidy_new_book_authors, so a book
whose only author was junk goes by "Unknown" and is looked up by its title alone.

calibre keeps a name's comma as "|"; these functions take and give names with commas."""

import re

from cps.constants import UNKNOWN_AUTHOR

# Not a name: an account, a site or tool's signature, a mangled encoding, a hash
_JUNK = re.compile(
    r"[@<>=/\\©#]|--|\d{1,2}:\d\d:\d\d|\b[0-9a-f]{32}\b|anna[’']s archive|ebooks? account|"
    r"prepress|converpage|\badministra[dt]|^admin$|^-|__",
    re.I)
_PLACEHOLDERS = {
    "unknown", "editor", "editors", "admin", "user", "owner", "author", "authors", "n/a", "na",
    "none", "null", "default", "pdf", "eds", "ed", "edt",
}
# A role or a reference in brackets: "(editor)", "(eds.)", "(Author)", "(Francis Joseph)", "(2)"
_BRACKETS = re.compile(r"\s*\([^)]*(?:\)|$)")
_ET_AL = re.compile(r"[,\s]*\bet\.? al\b\.?", re.I)
_LEADING_ROLE = re.compile(r"^(?:by|edited by|editors?|eds?\.?)\s*:?\s+", re.I)
# A part of a catalogue name that names no one: a role, or life dates ("1911-1996.", "1940-")
_ROLE = re.compile(r"^(?:editors?|eds?\.?|edt|\(?ed\.?\)?|jr\.?|sr\.?)$", re.I)
_PARTICLES = {"de", "la", "le", "du", "des", "di", "da", "del", "della", "van", "von", "der",
              "den", "ter", "ten", "y", "e", "dos", "das"}
# An initial: "C.", "R.K.", "J", "P.W"
_INITIAL = re.compile(r"^(?:[^\W\d_]\.)+[^\W\d_]?$|^[^\W\d_]$")


def _has_letters(text: str) -> bool:
    return bool(re.search(r"[^\W\d_]", text or ""))


def _initials(part: str) -> bool:
    """Only initials: "R. E.", "J.A."."""
    return all(_INITIAL.match(word) for word in part.split())


def _strip_stop(part: str) -> str:
    """Without the full stop a catalogue leaves after a name ("Dzung Minh."), keeping an
    initial's ("Paul F. A.")."""
    part = part.strip(" ,;:")
    words = part.split()
    return part.rstrip(".") if words and not _INITIAL.match(words[-1]) else part


def _is_word(part: str) -> bool:
    """A single word with no initial: "Lyndon", "Kotz"."""
    return " " not in part and not _INITIAL.match(part)


def _forenames(part: str) -> bool:
    """Whether a catalogue name's second part is forenames rather than another surname:
    it has an initial ("Roger C.") or two names ("Paula Yurkanis")."""
    return " " in part or bool(_INITIAL.match(part))


def _person(surname: str, forenames: str, given) -> str:
    """"Lyndon", "Roger C." -> "Roger C. Lyndon". Two single names may be two surnames
    ("Johnson, Kotz"), so they stay as calibre keeps a sorted name, unless the second is
    one of the given names."""
    if _forenames(forenames) or forenames.casefold() in given:
        return f"{forenames} {surname}"
    return f"{surname}, {forenames}"


def _people(chunk: str, given) -> list:
    """The people one comma-separated chunk names."""
    parts = [_strip_stop(p) for p in chunk.split(",")]
    parts = [p for p in parts if p and _has_letters(p) and not _ROLE.match(p)]
    if len(parts) <= 1:
        return parts
    if (len(parts) % 2 == 0 and any(_initials(p) for p in parts[1::2])
            and not any(_initials(p) for p in parts[::2])):
        # "Raab, R. E., De Lange, O. L.": surnames, each followed by initials
        return [_person(parts[i], parts[i + 1], given) for i in range(0, len(parts), 2)]
    if all(" " in p for p in parts):
        # "Stefano Bellucci, Sergio Ferrara": full names in a list
        return parts
    if len(parts) % 2 == 0 and all(_is_word(p) for p in parts[::2]):
        # "Lyndon, Roger C." or "Nicholson, James, Clapham, Christopher": surname, forenames
        return [_person(parts[i], parts[i + 1], given) for i in range(0, len(parts), 2)]
    if all(_is_word(p) and "." not in p for p in parts) and len(parts) > 2:
        # "Fine, Rosenberger, Smith": surnames
        return parts
    return [", ".join(parts)]


def _tidy_word(word: str) -> str:
    """A word of a name in capitals as it is written: "GARCIA-PRADA" -> "Garcia-Prada"."""
    lower = word.lower()
    out = re.sub(r"(^|[-'’.])([^\W\d_])", lambda m: m.group(1) + m.group(2).upper(), lower)
    return re.sub(r"^Mc([a-z])", lambda m: "Mc" + m.group(1).upper(), out)


def _tidy_case(name: str) -> str:
    """A name in capitals in the usual case; any other is left as written."""
    letters = re.sub(r"[^\w]", "", name)
    words = name.split()
    # One word in capitals is an acronym ("SIAM", "NASA")
    if len(words) < 2 or letters != letters.upper() or not re.search(r"[^\W\d_]{3}", name):
        return name
    return " ".join(w.lower() if i and w.lower() in _PARTICLES else _tidy_word(w)
                    for i, w in enumerate(words))


def _tidy_person(name: str) -> str:
    return _tidy_case(_strip_stop(" ".join(name.split())))


def is_junk_person(name: str) -> bool:
    """Whether a name is no one's: no letters, digits ("David1", "PG1248", a stamp's date),
    an account or a lone lower-case word ("uoyilmaz", "bconway")."""
    name = (name or "").strip()
    if not _has_letters(name) or re.search(r"\d", name) or _JUNK.search(name):
        return True
    if name.casefold() in _PLACEHOLDERS:
        return True
    return " " not in name and name == name.lower() and name.isascii()


def given_names(names) -> frozenset:
    """The forenames among full names: the first word of each with two or more."""
    found = set()
    for name in names:
        words = _shown(name).split()
        if "," not in _shown(name) and len(words) > 1 and len(words[0]) > 1 and not _INITIAL.match(words[0]):
            found.add(words[0].casefold())
    return frozenset(found)


def clean_author_names(name: str, given=frozenset()) -> list:
    """The people an author's name names, cleaned up; [] when it names no one. given holds
    the forenames that tell "Kleppner, Daniel" (Daniel Kleppner) from "Johnson, Kotz"."""
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), name or "")
    text = " ".join(text.split())
    if not _has_letters(text) or _JUNK.search(text):
        return []
    if text.casefold() == UNKNOWN_AUTHOR.casefold():
        return [UNKNOWN_AUTHOR]
    names, seen = [], set()
    # ";" and a comma without a space after it separate people: "Wang, Liqiu.; Yu, Pei",
    # "Akcoglu, Mustafa A.,Ha, Dzung Minh."
    for chunk in re.split(r"\s*;\s*|,(?=[^\s,])", text):
        chunk = _BRACKETS.sub("", chunk)
        chunk = _LEADING_ROLE.sub("", _ET_AL.sub("", chunk).strip())
        for person in _people(chunk, given):
            person = _tidy_person(person)
            if person and not is_junk_person(person) and person.casefold() not in seen:
                seen.add(person.casefold())
                names.append(person)
    return names


def _stored(name: str) -> str:
    return name.replace(",", "|")


def _shown(name: str) -> str:
    return (name or "").replace("|", ",")


class _Authors:
    """Finds or makes the author row for a name, so each name has one row."""

    def __init__(self, session):
        from cps import db
        from cps.helper import get_sorted_author
        self.db, self.sorted, self.session = db, get_sorted_author, session
        self.rows = {row.name.casefold(): row for row in session.query(db.Authors).all()}
        self.given = given_names(row.name for row in self.rows.values())

    def row(self, name: str):
        """The row named name, made when there is none; a row of the same name in other
        capitals is renamed to it."""
        key = _stored(name).casefold()
        row = None
        if ", " in name:
            # "Fitzpatrick, Richard" is the "Richard Fitzpatrick" the library already has
            surname, forenames = name.split(", ", 1)
            row = self.rows.get(f"{forenames} {surname}".casefold())
        row = row or self.rows.get(key)
        if row is None:
            row = self.db.Authors(_stored(name), self.sorted(name))
            self.session.add(row)
            self.rows[key] = row
        if row.name != _stored(name) and row.name.casefold() == key:
            row.name, row.sort = _stored(name), self.sorted(name)
        return row


def _replacements(authors, finder):
    """Author id -> the rows to put in its place, for each author whose name needs cleaning."""
    plan = {}
    for author in authors:
        name = author.name
        rows = [finder.row(n) for n in clean_author_names(_shown(name), finder.given)]
        if rows != [author] or author.name != name:
            plan[author.id] = rows
    return plan


def _clean_book(book, plan, finder) -> bool:
    """Put the cleaned authors in place on one book; True when they changed."""
    authors = []
    for author in book.authors:
        for row in plan.get(author.id, [author]):
            if row not in authors:
                authors.append(row)
    if not authors:
        authors = [finder.row(UNKNOWN_AUTHOR)]
    author_sort = " & ".join(a.sort for a in authors)
    if authors == list(book.authors) and author_sort == book.author_sort:
        return False
    book.authors = authors
    book.author_sort = author_sort
    return True


def _delete_unused_authors(session) -> int:
    from cps import db
    unused = session.query(db.Authors).filter(~db.Authors.id.in_(
        session.query(db.books_authors_link.c.author))).all()
    for author in unused:
        session.delete(author)
    return len(unused)


def _first_author_dir(book) -> str:
    from cps.helper import get_valid_filename
    return get_valid_filename(book.authors[0].name, chars=96) if book.authors else ""


def _move_folders(session, books, calibre_path) -> None:
    """Move each book's folder to its first author's, as an edit of its authors does."""
    from cps import helper
    from cps.logger import create
    log = create()
    for book in books:
        if book.path.split("/")[0] == _first_author_dir(book):
            continue
        try:
            error = helper.update_dir_structure(book.id, calibre_path, book.authors[0].name, book=book)
            if error:
                raise RuntimeError(error)
            session.commit()
        except Exception as ex:
            session.rollback()
            log.error("Could not move the folder of book %s: %s", book.id, ex)


def tidy_authors(session, books=None, calibre_path=None):
    """Clean the authors of the books given, or of every book: (books changed, authors
    removed). Commits, then moves the folders of books whose first author changed."""
    from cps import db
    finder = _Authors(session)
    if books is None:
        plan = _replacements(session.query(db.Authors).all(), finder)
        books = session.query(db.Books).filter(
            db.Books.authors.any(db.Authors.id.in_(list(plan)))).all() if plan else []
    else:
        plan = _replacements({a.id: a for book in books for a in book.authors}.values(), finder)
    if not plan:
        session.rollback()
        return 0, 0
    changed = [book for book in books if _clean_book(book, plan, finder)]
    session.flush()
    removed = _delete_unused_authors(session)
    session.commit()
    if calibre_path:
        _move_folders(session, changed, calibre_path)
    return len(changed), removed


def tidy_new_book_authors(book_id, calibre_path) -> bool:
    """Clean the authors calibre read from a newly imported file; True when they changed."""
    from cps import db
    cdb = db.CalibreDB(expire_on_commit=False, init=True)
    try:
        book = cdb.get_book(book_id)
        if book is None:
            return False
        return tidy_authors(cdb.session, [book], calibre_path)[0] > 0
    except Exception:
        cdb.session.rollback()
        raise
    finally:
        cdb.session.close()
