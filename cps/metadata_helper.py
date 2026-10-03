# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Looks a book up with the metadata providers and applies an exact match: for new books
("Fetch metadata for new books") and for every book (Rebuild metadata)."""

import functools
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, UTC

from cps import logger, db, constants, cover_match, helper, page_ocr
from cps.edition import split_edition
from cps.helper import get_sorted_author
from cps.search_metadata import cl as metadata_providers
from cps.services import arxiv_shelf
from cps.services.Metadata import ProviderBusy
from cps.services.identifiers import (ARXIV_ID, DOI_RE, ISBN_RE, arxiv_id_from_doi, compact_isbn,
                                      normalise_identifiers, parse_identifier)
sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from cwa_db import CWA_DB  # noqa: E402
from metadata_suggestions import normalise_title, surnames, title_forms  # noqa: E402

log = logger.create()

# Rebuild metadata looks several books up at once: each holds this while it reads or writes the
# library, and only provider lookups overlap. Its writes and folder moves go one at a time, so the
# lookups don't queue on SQLite's write lock or move two books' folders at once.
library_lock = threading.RLock()

# The metadata-change-detector service hands each log here to cover_enforcer.py
CHANGE_LOGS_DIR = "/app/calibre-web-automated/metadata_change_logs"
# The formats cover_enforcer.py can write metadata into
ENFORCED_FORMATS = {"EPUB", "AZW3"}

# One normalisation for imports and Fetch metadata's ranking, so a full score there
# means the title an import would accept
_normalise = normalise_title


def titles_match(a: str, b: str) -> bool:
    """The same title, ignoring only case, accents, punctuation and spacing. A subtitle or a
    leading "The" makes it a different title."""
    a = _normalise(a)
    return bool(a) and a == _normalise(b)


def matched_title(title: str, record):
    """The record's title as this book has it, in the provider's spelling: in full or, for a
    record with a subtitle, without it. None when the book's title is neither."""
    return next((form for form in title_forms(record) if titles_match(title, form)), None)


def best_metadata_match(title: str, authors, results, page_text: str = "", cover=None):
    """Return the result that is exactly this book, or None.

    The title must match exactly (see titles_match), with or without the result's subtitle,
    and, when both sides list authors, they must share a surname. A result naming the
    authors wins over one that names none. A book with no author has only its title to go
    by, and titles like "Calculus" are shared by many books: one of the result's authors must
    be printed on the book's pages (author_on_pages), or its cover be the book's (cover_is).
    Of several editions that match, the one whose cover is the book's wins. `cover` gives the
    book's cover thumbnail when called (cover_match)."""
    book_surnames = surnames(authors)
    title_only = None
    matches = []
    weighed = 0
    for result in results or []:
        if matched_title(title, result) is None:
            continue
        if not book_surnames:
            if author_on_pages(result, page_text):
                matches.append(result)
            elif weighed < COVERS_WEIGHED:
                weighed += 1
                if cover_is(result, cover):
                    matches.append(result)
            continue
        result_surnames = surnames(getattr(result, 'authors', None))
        if result_surnames:
            if book_surnames & result_surnames:
                matches.append(result)
        elif title_only is None:
            title_only = result
    if len(matches) > 1:
        return next((m for m in matches[:COVERS_WEIGHED] if cover_is(m, cover)), matches[0])
    return matches[0] if matches else title_only


# The most provider covers a match downloads to weigh against the book's
COVERS_WEIGHED = 5


def cover_is(record, cover) -> bool:
    """Whether the record's cover is the book's: `cover` gives the book's thumbnail when
    called. False when either has none, or too little detail to tell (cover_match)."""
    mine = cover() if cover else None
    return bool(mine) and cover_match.same_cover(mine, cover_match.remote_thumbnail(getattr(record, 'cover', '') or ''))


def author_on_pages(record, page_text: str) -> bool:
    """Whether the surname of one of the record's authors is printed on the pages, as a word."""
    names = surnames(getattr(record, 'authors', None))
    return bool(names) and bool(names & set(_normalise(page_text).split()))


# calibre keeps a title to 42 characters in a file's name. A book imported under its file's
# name has its title cut there, often mid-word: "Graph Drawing Algorithms for the Visualiza"
FILE_NAME_TITLE_LENGTH = 42


def cut_short(title: str) -> bool:
    """Whether the title is as long as a file's name lets it be, so probably cut off there
    (one shorter when the cut fell on a space)."""
    return len((title or "").strip()) in (FILE_NAME_TITLE_LENGTH - 1, FILE_NAME_TITLE_LENGTH)


# What a file's name adds after the title: "2e", ", 3rd Edition", "(Lecture Notes in Physics)"
_EDITION_TAIL = re.compile(
    r"[\s,]+(?:\d{1,2}e|(?:\d{1,2}(?:st|nd|rd|th)|second|third|fourth|fifth|sixth|revised|new)"
    r"\s+ed(?:ition|n|\.)?)\s*$", re.I)
_BRACKET_TAIL = re.compile(r"\s+\(([^()]*)\)\s*$")
# "(Volume 2)", "(IV)": which book it is, not an addition
_VOLUME = re.compile(r"(?:vol(?:ume)?|part|pt|book|band|teil|tome|no)\b|[\dIVXLC\s.,-]+$", re.I)


def bare_title(title: str) -> str:
    """The title without what a file's name adds after it: "Nanofluidics 2e" -> "Nanofluidics".
    A volume in brackets stays; so does a title that is nothing else."""
    title = (title or "").strip()
    while True:
        bracket = _BRACKET_TAIL.search(title)
        if bracket and not _VOLUME.match(bracket.group(1).strip()):
            shorter = title[:bracket.start()]
        else:
            shorter = _EDITION_TAIL.sub("", title)
        shorter = shorter.rstrip(" ,;:-")
        if not shorter or shorter == title:
            return title
        title = shorter


def search_title(title: str) -> str:
    """The title to ask a provider for: without what a file's name added, and without the
    half word a cut left at its end."""
    words = (title or "").split()
    if cut_short(title) and len(words) > 2:
        title = " ".join(words[:-1])
    return bare_title(title)


def loosely_titled(title: str, record) -> bool:
    """Whether the record's title is the book's as a file's name left it: with an edition or
    a series after it, or cut short (the record's title starts with all there is of it)."""
    bare = bare_title(title)
    if bare != (title or "").strip() and matched_title(bare, record) is not None:
        return True
    start = _normalise(title)
    return bool(start) and cut_short(title) and any(
        _normalise(form).startswith(start) for form in title_forms(record))


def _on_pages(record, page_text: str) -> bool:
    """Whether the record's title, with or without its subtitle, is printed on the pages."""
    return any(title_on_page(form, page_text) for form in title_forms(record))


def loose_metadata_match(title: str, authors, results, page_text: str = "", cover=None):
    """The result that is this book going by a title its file's name damaged (see
    loosely_titled), or None.

    Such a title says less than an exact one, so one of the book's authors must be among the
    result's, the result's title printed on the book's own pages, or its cover the book's
    (cover_is). Results that are different books leave it undecided."""
    book_surnames = surnames(authors)
    found = None
    weighed = 0
    for result in results or []:
        if not loosely_titled(title, result):
            continue
        if not (book_surnames & surnames(getattr(result, 'authors', None)) or _on_pages(result, page_text)):
            if weighed >= COVERS_WEIGHED:
                continue
            weighed += 1
            if not cover_is(result, cover):
                continue
        if found is None:
            found = result
        elif _normalise(found.title) != _normalise(result.title):
            return None
    return found


# arXiv stamps its id, version and date down the first page's margin: "arXiv:1706.03762v7
# [cs.CL] 2 Aug 2023". A paper citing another names it without them.
_ARXIV_STAMP = re.compile(
    rf"arxiv:\s*({ARXIV_ID})v\d+\s*(?:\[[^\]]*\]\s*)?\d{{1,2}}\s*[A-Z][a-z]{{2}}\s*\d{{4}}", re.I)
_ARXIV_MENTION = re.compile(rf"arxiv:\s*({ARXIV_ID})", re.I)


def find_paper_identifiers(title: str, page_text: str = "", identifiers=None) -> dict:
    """The arXiv id and DOI naming a paper: the book's own, else one its title is
    (a PDF often imports under its file name, "1706.03762v7") or one on its first
    page (arXiv's margin stamp, a journal's DOI line)."""
    found = {k: v for k, v in normalise_identifiers(identifiers or {}).items()
             if k in ("arxiv", "doi")}
    typed = parse_identifier(title)
    mention = _ARXIV_MENTION.search(title or "")
    stamp = _ARXIV_STAMP.search(page_text or "")
    for match in (mention, stamp):
        if match:
            typed.setdefault("arxiv", match.group(1))
    doi = DOI_RE.search(page_text or "")
    if doi:
        typed.setdefault("doi", doi.group(0).rstrip(".,;:)]}"))
    for key in ("arxiv", "doi"):
        if typed.get(key):
            found.setdefault(key, typed[key])
    # arXiv's own DOI names the paper's arXiv id
    if arxiv_id_from_doi(found.get("doi")):
        found.setdefault("arxiv", arxiv_id_from_doi(found["doi"]))
    return found


# A title that is the name of the file the book was typeset or exported from: "427551_Print.indd",
# "Microsoft Word - thesis.docx". calibre reads it from the PDF's own details, which the
# publisher's layout software fills in.
_FILE_NAME_TITLE = re.compile(
    r"^microsoft (?:word|powerpoint) - "
    r"|\.(?:indd|qxd|qxp|p65|pm[5-7]|fm|docx?|rtf|odt|tex|dvi|ps|eps|ai|pdf|txt|djvu|tiff?)$", re.I)


def named_by_file(title: str) -> bool:
    """Whether the book's title is a file's name rather than its own."""
    return bool(_FILE_NAME_TITLE.search((title or "").strip()))


def placeholder_author(name: str) -> bool:
    """calibre's "Unknown", or a name with no letters ("0000253", a publisher's id that a PDF
    gives as its author): not a name to search for or match."""
    return constants.is_unknown_author(name) or not re.search(r"[^\W\d_]", name or "")


# "ISBN 978-1-4471-2490-0", "ISBN-10: 0-387-95385-X": the ISBNs a copyright page prints
_ISBN_ON_PAGE = re.compile(r"ISBN(?:-1[03])?:?\s*((?:97[89][\s-]?)?\d(?:[\s-]?\d){8}[\s-]?[\dX])", re.I)


def _isbn_checks(isbn: str) -> bool:
    """Whether the ISBN's check digit is right: text read off a page can garble a digit."""
    if len(isbn) == 13:
        return isbn.isdigit() and sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(isbn)) % 10 == 0
    return sum((10 - i) * (10 if d == "X" else int(d)) for i, d in enumerate(isbn)) % 11 == 0


# "9781118230725.pdf", "978-0-7923-0760-0_Book_PrintPDF.pdf": a publisher's file, named by its ISBN
_ISBN_NAMED = re.compile(r"\s*(97[89](?:[\s-]?\d){10})(?!\d)")


def isbn_in_title(title: str) -> str:
    """The ISBN the title starts with, as a book imported under its file's name has when the
    file was named by its ISBN; empty when it starts with none."""
    match = _ISBN_NAMED.match(title or "")
    isbn = compact_isbn(match.group(1)) if match else ""
    return isbn if isbn and _isbn_checks(isbn) else ""


def isbns_on_pages(page_text: str) -> list[str]:
    """The ISBNs printed on the pages, the book's own likely first: a series list before the
    title page names its other books "(ISBN 0-8176-3967-5)", in brackets after their titles,
    so a bracketed ISBN comes after any that isn't."""
    plain: list[str] = []
    bracketed: list[str] = []
    for match in _ISBN_ON_PAGE.finditer(page_text or ""):
        isbn = compact_isbn(match.group(1))
        if ISBN_RE.fullmatch(isbn) and _isbn_checks(isbn) and isbn not in plain + bracketed:
            in_brackets = page_text[max(match.start() - 1, 0):match.start()] == "("
            (bracketed if in_brackets else plain).append(isbn)
    return plain + bracketed


def isbn_on_pages(page_text: str) -> str:
    """The ISBN printed on the pages most likely the book's own (the print edition's, on a
    copyright page; see isbns_on_pages); empty when there is none."""
    return next(iter(isbns_on_pages(page_text)), "")


def _squash(text: str) -> str:
    """Letters and digits only: PDF text often loses or adds spaces and hyphens."""
    return re.sub(r'[\W_]+', '', _normalise(text))


@functools.lru_cache(maxsize=16)
def _squashed_pages(page_text: str) -> str:
    """_squash of pages' text, kept: one lookup asks whether many titles are on the same pages."""
    return _squash(page_text)


def title_on_page(title: str, page_text: str) -> bool:
    """Whether the title appears in the page's text, spacing and punctuation aside."""
    title = _squash(title)
    return bool(title) and title in _squashed_pages(page_text or "")


def _main_title(title: str) -> str:
    """The title without its subtitle: "Dune: Deluxe Edition" -> "dune"."""
    return _normalise((title or '').split(':')[0])


def found_by_id_is_this_book(record, ids: dict, title: str, authors, page_text: str = "",
                             own_ids=None) -> bool:
    """Whether a record an identifier lookup returned is this book.

    A record carrying the arXiv id or DOI looked up is this paper when the id is the
    book's own or its file name; one read off the first page may be a citation, so the
    record's title must be on that page too. A book named by its file ("427551_Print.indd")
    is the record whose title is on its first pages, or, for a file named by its ISBN, the
    record carrying that ISBN. Anything else (an ISBN can be a placeholder or another book's)
    needs the same title, subtitle and a file name's damage aside, and no other author."""
    record_ids = normalise_identifiers(getattr(record, 'identifiers', None) or {})
    trusted = find_paper_identifiers(title, "", own_ids)
    for key in ("arxiv", "doi"):
        wanted = (ids.get(key) or "").lower()
        if not wanted or (record_ids.get(key) or "").lower() != wanted:
            continue
        if (trusted.get(key) or "").lower() == wanted or title_on_page(record.title, page_text):
            return True
    named_isbn = isbn_in_title(title)
    if named_isbn and record_ids.get("isbn") == named_isbn:
        return True
    if named_isbn or named_by_file(title):
        # Its title says nothing about it, but its title page does
        return _on_pages(record, page_text) and not _printed_by_other(authors, record, page_text)
    return _same_book(title, authors, record)


def other_author(authors, record) -> bool:
    """Whether the record is by someone else: both sides name authors and share no surname."""
    book_surnames, record_surnames = surnames(authors), surnames(getattr(record, 'authors', None))
    return bool(book_surnames and record_surnames and not book_surnames & record_surnames)


def _printed_by_other(authors, record, page_text: str) -> bool:
    """Whether the record is by someone else while the pages print the book's own author: then
    its title on them is a mention (a series list of other books), not the title page. A book
    whose author is not printed there may have one saved wrong."""
    return other_author(authors, record) and any(
        title_on_page(name, page_text) for name in surnames(authors))


def _same_book(title: str, authors, record) -> bool:
    """The same title (subtitle aside, or the record's as a file's name left it) and no
    other author."""
    main = _main_title(title)
    if not (main and main == _main_title(record.title) or loosely_titled(title, record)):
        return False
    return not other_author(authors, record)


def printed_isbn_is_this_book(record, title: str, authors, page_text: str) -> bool:
    """Whether the record found by the ISBN a book's pages print is that book: its title is
    on those pages too and they don't name the book's own author beside another's, or its
    title is the book's own. (The
    pages can print other books' ISBNs: the set a volume belongs to, the hardback of a
    reprint, the publisher's list of the series' other titles.)"""
    return (_on_pages(record, page_text) and not _printed_by_other(authors, record, page_text)
            or _same_book(title, authors, record))


# A book's title page and copyright page come within its first few pages, after its cover
FRONT_PAGES = 8
# The printed ISBNs looked up, the likeliest first: a lookup each, and a series list prints many
PRINTED_ISBNS_TRIED = 3


def pdf_first_page_text(book) -> str:
    """The text of the book's PDF's first page; empty without a PDF or text layer."""
    return _pdf_text(book, 0, 1)


def pdf_front_matter_text(book) -> str:
    """The text of the PDF's pages after the first, up to FRONT_PAGES: a book's title page,
    and its copyright page with the ISBN. Empty without a PDF or text layer."""
    return _pdf_text(book, 1, FRONT_PAGES)


def _pdf_text(book, first: int, last: int) -> str:
    path, mtime = _pdf_file(book)
    return _read_pages(path, mtime, first, last) if path else ""


def _pdf_file(book):
    """(path, mtime) of the book's PDF; (None, 0) without one."""
    from cps.pdf_fast import source
    path = source(book)
    try:
        return (path, os.path.getmtime(path)) if path else (None, 0)
    except OSError:
        return None, 0


# A scan's pages read by OCR for its title page and copyright page: fewer than FRONT_PAGES,
# as each takes a moment
OCR_FRONT_PAGES = 5


def scanned_text(book, text: str, first: int, last: int) -> str:
    """The pages' text, or what OCR reads on them when they have no text layer: a scan (see
    page_ocr). Only the automatic lookup asks for it: OCR blocks while it reads, and Fetch
    metadata runs on the server's event loop."""
    if not page_ocr.needs_ocr(text) or not page_ocr.available():
        return text
    path, mtime = _pdf_file(book)
    return (page_ocr.ocr_pages(path, mtime, first, last) or text) if path else text


def back_cover_isbn(book) -> str:
    """The ISBN barcode on the last page of the book's PDF, a scan's back cover; '' for none."""
    path, mtime = _pdf_file(book)
    return page_ocr.back_cover_isbn(path, mtime) if path else ""


@functools.lru_cache(maxsize=32)
def _read_pages(path: str, mtime: float, first: int, last: int) -> str:
    """The text of pages first to last (from 0, last not included); keyed by mtime so a
    replaced file is read again. Fetch metadata asks for the first page once per provider."""
    pypdf_log = logging.getLogger("pypdf")
    level = pypdf_log.level
    # pypdf warns about every font it can't fully decode; the ids read fine regardless
    pypdf_log.setLevel(logging.ERROR)
    try:
        from pypdf import PdfReader
        # The open file, not its path: given a path, pypdf reads the whole file into memory first
        with open(path, "rb") as pdf:
            pages = PdfReader(pdf).pages[first:last]
            return "\n".join(page.extract_text() or "" for page in pages)
    except Exception as e:
        log.debug(f"Could not read the first page of {path}: {e}")
        return ""
    finally:
        pypdf_log.setLevel(level)


def _image_area(path: str) -> int:
    """Width times height of an image file; 0 when it can't be read."""
    try:
        from wand.image import Image
        # The header alone says the size (Wand before 0.5.6 has no ping and reads it all)
        with getattr(Image, 'ping', Image)(filename=path) as img:
            return img.width * img.height
    except Exception as e:
        log.debug(f"Could not measure {path}: {e}")
        return 0


def _download_cover(url: str, folder: str):
    """The provider's cover saved as folder/cover.jpg, or None (none offered, the
    generic placeholder, or a failed download)."""
    if not (url or '').startswith(('http://', 'https://')):
        return None
    try:
        # save_cover_from_url joins its path onto the library's; an absolute one lands here
        saved, error = helper.save_cover_from_url(url, folder)
    except Exception as e:
        saved, error = False, e
    path = os.path.join(folder, 'cover.jpg')
    if not saved or not os.path.isfile(path):
        log.debug(f"Cover {url} not saved: {error}")
        return None
    return path


def _cover_path(book):
    """The book's cover.jpg, or None when it has no cover."""
    from cps import config
    path = os.path.join(config.get_book_path(), book.path, 'cover.jpg')
    return path if book.has_cover and os.path.isfile(path) else None


def _keeps_page_cover(book) -> bool:
    """True for a book whose only files are PDFs, when page 1 can be rendered as its cover
    (cps/pdf_cover.py): no provider's cover is applied to it automatically."""
    from cps import pdf_cover
    formats = [d.format.upper() for d in book.data or []]
    return bool(formats) and all(f == 'PDF' for f in formats) and pdf_cover.available()


def _cover_wins(book, new_cover: str) -> bool:
    """A provider's cover replaces the book's only when the book has none or it is
    larger: a provider's cover is often a small thumbnail, worse than the one the file
    came with. Any doubt keeps the current cover."""
    current = _cover_path(book)
    if current is None:
        return True
    new_area, old_area = _image_area(new_cover), _image_area(current)
    return bool(new_area and old_area and new_area > old_area)


def _cover_state(path) -> str:
    """A cover file as "size:mtime", which changes whenever the file does; '' for none."""
    try:
        stat = os.stat(path)
    except (OSError, TypeError):
        return ''
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _cover_worth_fetching(store, book_id, url, largest, current) -> bool:
    """Whether to download the cover at url to weigh it against the book's (see _cover_wins).
    Not when it can't be larger (`largest` is the most pixels it can have, 0 for unknown), or
    when this very cover was already weighed against the one the book still has: a rebuild
    would download every matched book's cover again."""
    if current is None:
        return True
    if largest and _image_area(current) >= largest:
        return False
    try:
        return store.get_cover_check(book_id) != (url, _cover_state(current))
    except Exception as e:
        log.debug(f"No cover check to read for book {book_id}: {e}")
        return True


def _remember_cover(store, book_id, url, state) -> None:
    """Note that the cover at url was weighed against the book's, in the state it is now."""
    try:
        store.save_cover_check(book_id, url, state)
    except Exception as e:
        log.debug(f"Could not note the cover check of book {book_id}: {e}")


def fetch_and_apply_metadata(book_id: int, force: bool = False, unanswered=None, overwrite: bool = False) -> bool:
    """Look the book up and apply an exact match; True when the book changed.

    Runs for a new book when "Fetch metadata for new books" is on, and for every book in
    Rebuild metadata (force). The library is read, then the providers are asked and the
    cover downloaded without holding it, then every change is made and committed at once
    under library_lock, so the write lock is held for moments, not for a download. A new
    title or first author moves the book's folder, and with "Write edits into book files"
    on, the change is queued for the files. The names of providers that failed to answer
    are added to `unanswered` (a set) when one is given. What the lookup found is noted in
    cwa.db (see _note_lookup). A book's tags are never touched: they are the user's own.

    With overwrite (Full rebuild) a match replaces the book's details rather than filling
    them: its date and identifiers, and its title and authors unless the book
    was edited by hand."""
    if not db.CalibreDB.session_factory:
        log.error("CalibreDB not initialized; skipping metadata fetch")
        return False
    store = CWA_DB()
    settings = store.get_cwa_settings()
    if not force and not settings.get('auto_metadata_fetch_enabled'):
        return False
    with library_lock:
        cdb = db.CalibreDB(expire_on_commit=False, init=True)
    title = ''
    try:
        with library_lock:
            book = cdb.get_book(book_id)
            if not book:
                log.error(f"Book with ID {book_id} not found")
                return False
            # "Unknown" and ids are not names to search for; calibre keeps a name's comma as "|"
            authors = [a.name.replace('|', ',') for a in book.authors or []
                       if not placeholder_author(a.name)]
            own_ids = normalise_identifiers({i.type: i.val for i in book.identifiers or []})
            title = book.title
            current_cover = _cover_path(book)
            # These load the book's files too, so the PDF reads below need no library access
            page_cover = _keeps_page_cover(book)
            papers_too = _may_be_a_paper(book)
        hand_edited = _hand_edited(store, book_id)
        mode = (HAND if hand_edited else REPLACE) if overwrite else lookup_mode(title, authors, hand_edited)
        # Its PDF is read without holding the library: a page takes ~0.5 s on a NAS, and the
        # rebuild's other lookups would queue behind it
        # A scan's pages are read by OCR
        page_text = scanned_text(book, pdf_first_page_text(book), 0, 1)

        def front_matter():
            return scanned_text(book, pdf_front_matter_text(book), 1, OCR_FRONT_PAGES)
        if named_by_file(title) or not authors:
            # What it is shows on its title page, its author there too, and its ISBN on the
            # copyright page
            page_text = "\n".join((page_text, front_matter()))
            front_matter = str
        # Otherwise the pages after the first are read only when the searches find nothing,
        # and not while holding the library
        missed, busy = set(), set()
        # The book's cover, shrunk only when a record's cover is weighed against it
        book_cover = functools.cache(lambda: cover_match.file_thumbnail(current_cover)) if current_cover else None
        record = _find_record(title, authors, own_ids, page_text, missed,
                              front_matter=front_matter, papers_too=papers_too, busy=busy, cover=book_cover,
                              back_cover=functools.partial(back_cover_isbn, book))
        if unanswered is not None:
            unanswered.update(missed)
        if record is None:
            # A book filled in by hand that no provider has stays so, out of the No match list
            status = 'manual' if mode == HAND else _missed_status(missed, busy)
            _note_lookup(store, book_id, status, title=title)
            return False
        # A PDF's cover is its first page; a provider's is only taken by hand, in Fetch metadata
        url = '' if page_cover else getattr(record, 'cover', '') or ''
        with tempfile.TemporaryDirectory() as tmp:
            cover = None
            if _cover_worth_fetching(store, book_id, url, getattr(record, 'cover_max_pixels', 0), current_cover):
                cover = _download_cover(url, tmp)
            changes = {}
            with library_lock:
                before = _title_and_author(cdb, book)
                changed = _apply_record(cdb, book, record, cover, mode=mode, store=store, overwrite=overwrite,
                                        changes=changes)
                cover_state = _cover_state(_cover_path(book))
                title = book.title
                if changed:
                    source = getattr(getattr(record, 'source', None), 'description', 'a provider')
                    log.info(f"Applied metadata from {source} to book {book_id}")
                    _follow_up(cdb, book_id, before, bool(settings.get('auto_metadata_enforcement')))
        if cover:
            # Weighed, whichever won: the same cover need not be fetched to compare again
            _remember_cover(store, book_id, url, cover_state)
        source = getattr(getattr(record, 'source', None), 'description', '')
        _note_lookup(store, book_id, 'matched', source, title, changes)
        _log_changes(book_id, title, source, changes)
        _file_if_from_arxiv(book_id, record)
        return changed
    except Exception as e:
        log.error(f"Metadata lookup for book {book_id} failed: {e}", exc_info=True)
        _note_lookup(store, book_id, 'failed', title=title)
        with library_lock:
            _rollback(cdb)
        return False
    finally:
        with library_lock:
            cdb.session.close()


def _log_changes(book_id, title, source, changes):
    """Say in the service log what a lookup changed, a line a field: the Logs page shows it too."""
    for field, (old, new) in changes.items():
        log.info(f"Book {book_id} '{title}' from {source}: {field} {old or '(none)'!s} -> {new or '(none)'!s}")


def _missed_status(missed, busy) -> str:
    """failed when a provider that didn't answer might have the book (worth asking again),
    else nomatch. One that only said it was out of quota (Google without a key, nearly always)
    doesn't make it failed while another provider answered."""
    if not missed:
        return 'nomatch'
    asked = {provider.__name__ for provider in _lookup_order()}
    if missed - busy or asked <= missed:
        return 'failed'
    return 'nomatch'


def _hand_edited(store, book_id) -> bool:
    try:
        return bool(store.is_hand_edited(book_id))
    except Exception as e:
        log.debug(f"Could not read whether book {book_id} was edited by hand: {e}")
        return False


def _note_lookup(store, book_id, status, source='', title='', changes=None):
    """Note what the book's lookup found: matched, nomatch (every provider answered and none
    has it), failed (one didn't answer, or the lookup went wrong) or manual (no match for a
    book filled in by hand, so not one to list as a problem). The library's Metadata
    filter and Retry failed read it, and the Logs page lists it with what it changed; a
    failure here is logged, never the lookup's."""
    try:
        store.save_metadata_lookup(book_id, status, source)
        store.log_metadata_lookup(book_id, title, status, source, json.dumps(changes or {}, ensure_ascii=False))
    except Exception as e:
        log.debug(f"Could not note the lookup of book {book_id}: {e}")


def _lookup_order():
    """The providers in the order a lookup asks them: Google Books last, as its daily quota
    is soon used up and the others often have the book."""
    return sorted(metadata_providers, key=lambda provider: provider.__id__ == 'google')


def _file_if_from_arxiv(book_id, record):
    """Put a paper whose record arXiv gave on the arXiv shelf; a failure there is logged,
    never the lookup's."""
    if "arxiv" not in normalise_identifiers(getattr(record, 'identifiers', None) or {}):
        return
    try:
        if arxiv_shelf.file_on_shelf([book_id]):
            log.info(f"Filed book {book_id} on the arXiv shelf")
    except Exception as e:
        log.warning(f"Could not file book {book_id} on the arXiv shelf: {e}")


def _rollback(cdb):
    try:
        cdb.session.rollback()
    except Exception as e:
        log.error(f"Rollback failed: {e}")


def _no_answer(provider, what, error, unanswered, busy=None) -> None:
    """Log a provider's failure and note its name for the caller, and in `busy` too when it
    only said it was out of quota (429)."""
    if unanswered is not None:
        unanswered.add(provider.__name__)
    if busy is not None and (isinstance(error, ProviderBusy) or "429" in str(error)):
        busy.add(provider.__name__)
    # Left alone after a 429: said once then, not for every book until it is asked again
    level = log.debug if isinstance(error, ProviderBusy) else log.warning
    level(f"{what} with {provider.__name__} failed: {error}")


# The provider that searches papers (Crossref, DataCite, Semantic Scholar, arXiv)
PAPERS = 'googlescholar'


# A paper's first page heads its summary "Abstract"; a book's rarely does
_ABSTRACT = re.compile(r"^\s*abstract\b", re.I | re.M)


def _search_order(lookup_ids, page_text):
    """The providers in the order a title search weighs their answers: the paper sources
    after Google Books, unless the first page reads as a paper's. A textbook shares its title
    with articles, and with reviews of it that name other authors."""
    providers = _lookup_order()
    if 'arxiv' in lookup_ids or _ABSTRACT.search(page_text or ''):
        return providers
    return sorted(providers, key=lambda provider: provider.__id__ == PAPERS)


def _may_be_a_paper(book) -> bool:
    """Whether a title search should ask the paper sources: only for a book whose files are all
    PDFs. An EPUB is a trade book, which Crossref matches to a critical edition or a chapter."""
    formats = {d.format.upper() for d in book.data or []}
    return not formats or formats == {'PDF'}


def _find_record(title, authors, own_ids, page_text, unanswered=None, front_matter=None, papers_too=True,
                 busy=None, cover=None, back_cover=None):
    """The provider record that is exactly this book, or None. An identifier lookup
    comes first: a paper by its arXiv id or DOI, as its title is often the file name,
    which no title search matches; a book by its ISBN, its own, the one its file was named
    by or, for a book named by its file, the one its copyright page prints. Then each
    provider's title search (the paper sources only when papers_too), unless the title is a
    file's name: an exact title first, then
    one a file's name damaged (see loose_metadata_match). A book still not found is looked
    up by the ISBN on its copyright page: `front_matter` gives the text of its pages after
    the first when called. Google Books is asked last of the book sources (see _lookup_order),
    and the paper sources weighed after it for a book (see _search_order). Providers that
    fail to answer are added to `unanswered`, and to `busy` when out of quota. `cover` gives
    the book's cover thumbnail when called, to weigh the records' covers against. Last, a
    book with no ISBN is looked up by the barcode on its back cover: `back_cover` reads it
    when called."""
    lookup_ids = find_paper_identifiers(title, page_text, own_ids)
    named_isbn = isbn_in_title(title)
    isbn = own_ids.get('isbn') or named_isbn or (isbn_on_pages(page_text) if named_by_file(title) else '')
    if isbn:
        lookup_ids['isbn'] = isbn
    record = _find_by_identifiers(lookup_ids, unanswered, lambda found, ids: found_by_id_is_this_book(
        found, ids, title, authors, page_text, own_ids), busy)
    if record is not None:
        return record
    if named_by_file(title) or named_isbn:
        # No provider has a book called "427551_Print.indd"
        record = None if 'isbn' in lookup_ids else _find_by_barcode(
            back_cover, title, authors, page_text, unanswered, busy)
        if record is None:
            log.info(f"No identifier found for '{title}'; keeping its details")
        return record
    query = " ".join([search_title(title)] + authors)
    searched = []
    providers = [p for p in _search_order(lookup_ids, page_text) if p.__id__ != PAPERS or papers_too]
    # Every provider but Google is asked at once, and the first in order with a match wins;
    # Google only when none has one, as its daily quota is soon used up
    at_once = [p for p in providers if p.__id__ != 'google']
    pool = ThreadPoolExecutor(max_workers=max(len(at_once), 1))
    try:
        asked = {p: pool.submit(p.search_titles, query, "", "en") for p in at_once}
        for provider in providers:
            try:
                results = (asked[provider].result() if provider in asked
                           else provider.search_titles(query, "", "en")) or []
                # Only the record applied needs the details a provider fetches per result
                record = best_metadata_match(title, authors, results, page_text, cover)
                if record is not None:
                    record = provider.complete(record)
            except Exception as e:
                _no_answer(provider, f"Searching for '{query}'", e, unanswered, busy)
                continue
            if record is not None:
                return record
            searched.append((provider, results))
    finally:
        # A match leaves the slower searches to finish on their own
        pool.shutdown(wait=False)
    record = _find_loosely(searched, title, authors, page_text, unanswered, busy, cover)
    pages, printed = page_text, []
    if record is None and front_matter and 'isbn' not in lookup_ids:
        pages = "\n".join((page_text, front_matter()))
        printed = isbns_on_pages(pages)[:PRINTED_ISBNS_TRIED]
        for isbn in printed:
            record = _find_by_identifiers({'isbn': isbn}, unanswered, lambda found, ids: (
                printed_isbn_is_this_book(found, title, authors, pages)), busy)
            if record is not None:
                break
        if record is None and pages.strip() != page_text.strip():
            # The title page is among them: it can confirm a title the first page could not
            record = _find_loosely(searched, title, authors, pages, unanswered, busy, cover)
    if record is None and 'isbn' not in lookup_ids:
        record = _find_by_barcode(back_cover, title, authors, pages, unanswered, busy, tried=printed)
    if record is None:
        log.info(f"No exact metadata match for '{title}'; keeping its details")
    return record


def _find_by_barcode(back_cover, title, authors, pages, unanswered, busy, tried=()):
    """The record found by the ISBN barcode on the book's back cover, checked as a printed
    ISBN's is (printed_isbn_is_this_book); None without one, or when it is among the ISBNs
    `tried`."""
    scanned = back_cover() if back_cover else ''
    if not scanned or scanned in tried:
        return None
    log.info(f"Looking '{title}' up by the ISBN barcode on its back cover, {scanned}")
    return _find_by_identifiers({'isbn': scanned}, unanswered, lambda found, ids: (
        printed_isbn_is_this_book(found, title, authors, pages)), busy)


def _find_by_identifiers(lookup_ids, unanswered, is_this_book, busy=None):
    """The first record a provider finds by the identifiers it knows that
    is_this_book(record, identifiers asked for) accepts, or None."""
    for provider in _lookup_order():
        ids = {k: v for k, v in lookup_ids.items() if k in provider.identifier_types}
        if not ids:
            continue
        try:
            results = provider.search_identifiers(ids, "", "en") or []
        except Exception as e:
            _no_answer(provider, f"Looking up {ids}", e, unanswered, busy)
            continue
        record = next((r for r in results if is_this_book(r, ids)), None)
        if record is not None:
            return record
    return None


def _find_loosely(searched, title, authors, page_text, unanswered, busy=None, cover=None):
    """The first loose match (see loose_metadata_match) among the results the title
    searches gave, completed by its provider, or None."""
    for provider, results in searched:
        record = loose_metadata_match(title, authors, results, page_text, cover)
        if record is None:
            continue
        try:
            return provider.complete(record)
        except Exception as e:
            _no_answer(provider, f"Completing '{record.title}'", e, unanswered, busy)
    return None


def _has_date(current, published) -> bool:
    """Whether the book's date already says what the provider's does. A provider that knows
    only the year (or month) gives its first day, so a book dated within it is that date,
    known more exactly, and keeps its own."""
    if not current:
        return False
    if current.date() == published.date():
        return True
    if published.day != 1 or current.year != published.year:
        return False
    return published.month in (1, current.month)


def _named(cdb, model, lookup, name, *extra):
    """The row called name, or a new one."""
    row = lookup(name)
    if row is None:
        row = model(name, *extra)
        cdb.session.add(row)
    return row


def _unused(session, row) -> bool:
    """Whether no book has the author any more. Asked of the
    library, as row.books would load every book that has it: thousands, for a common tag."""
    model = type(row)
    return session.query(model.id).filter(model.id == row.id, model.books.any()).first() is None


def _note_edition(book_id, edition):
    """Store an edition found in a fetched title, keeping one the book already has."""
    try:
        store = CWA_DB()
        if store.get_book_edition(book_id) is None:
            store.set_book_edition(book_id, edition)
    except Exception as e:
        log.debug("Could not note book %s's edition: %s", book_id, e)


def poorly_cased(title: str) -> bool:
    """Whether a title's case is a file's or a catalogue's, not a title's: all capitals, or a
    word of four letters or more starting in lower case ("The war of the worlds")."""
    title = (title or '').strip()
    if title.isupper():
        return True
    return any(len(word) > 3 and word[0].islower() for word in re.findall(r"[^\W\d_][\w'-]*", title))


# How a match is applied (see lookup_mode)
REPLACE, FILL, HAND = "replace", "fill", "hand"


def lookup_mode(title: str, authors, hand_edited: bool) -> str:
    """How much of a match a lookup applies to the book.

    HAND for a book edited by hand: its title and authors stay, empty fields are filled.
    REPLACE for a book whose details are a file's leftovers (its title is a file's name, an
    ISBN, cut short or tailed with an edition, or it has no real author): the match replaces
    them. FILL otherwise: the title and authors take the match's spelling only when they
    are the same, and only empty fields are filled."""
    if hand_edited:
        return HAND
    if not authors or named_by_file(title) or isbn_in_title(title) or cut_short(title) \
            or bare_title(title) != (title or '').strip():
        return REPLACE
    return FILL


_NO_DATE_YEAR = 101  # calibre's "no date": 0101-01-01


def _no_date(current) -> bool:
    return current is None or current.year <= _NO_DATE_YEAR


def _apply_record(cdb, book, record, cover, mode=REPLACE, store=None, changes=None, overwrite=False):
    """Writes what the record changes and commits; True when anything changed.

    With mode REPLACE (see lookup_mode) the record's fields replace the book's; with FILL and
    HAND only empty fields are filled, and the title and authors change only as lookup_mode
    says (a date is also replaced by an earlier one, as a first publication is). Only fields
    the record has are touched; a book's own identifiers are kept and new ones added. The
    description, tags, the publisher, languages and ratings are left alone: Lily keeps none of
    them from a lookup. What changed is
    kept in `store` as it was before, for Undo, and added to `changes` as {field: [before,
    after]} for the Logs page (see described_changes)."""
    session = cdb.session
    changed = False
    dropped = []
    before = {}
    # Overwriting (Full rebuild), even a book edited by hand takes the match's other details
    filling = mode != REPLACE and not overwrite
    with session.no_autoflush:
        # A book that goes by the title without its subtitle (or with it) keeps going by that
        matched = matched_title(book.title, record)
        title = (matched or record.title or '').strip()
        # "Title (9th Edition)": the edition goes to the book's Edition, unless it has one
        title, edition = split_edition(title)
        edition = edition or split_edition(record.title or '')[1]
        if edition and mode != HAND:
            _note_edition(book.id, edition)
        # Filling, the title only takes the record's spelling of the same title. The same title
        # differing only in case and punctuation is respelled only from a poorly cased one
        title_ok = mode == REPLACE or (mode == FILL and matched is not None)
        if matched is not None and not poorly_cased(book.title):
            title_ok = False
        if title and title_ok and title != book.title:
            before['title'] = book.title
            book.title = title
            changed = True

        # calibre keeps a name's comma as "|" (MetaRecord already turned "Last, First" round);
        # names differing only in case, spacing or punctuation are one author: Open Library
        # lists "J.R.R. Tolkien" and "J. R. R. Tolkien"
        names = {}
        for name in record.authors or []:
            name = (name or '').strip().replace(',', '|')
            key = _squash(name)
            # "J. LESSLIE HALL" gives way to "J. Lesslie Hall"
            if key and (key not in names or names[key].isupper()):
                names[key] = name
        own = [a.name for a in book.authors if not placeholder_author(a.name.replace('|', ','))]
        # Filling, the authors change only from none, or to the same people in another case or order
        authors_ok = mode == REPLACE or (mode == FILL and (not own or {_squash(n) for n in own} == set(names)))
        if names and authors_ok:
            authors = []
            for name in names.values():
                author = _named(cdb, db.Authors, cdb.get_author_by_name, name,
                                get_sorted_author(name.replace('|', ',')))
                if author not in authors:
                    authors.append(author)
            # "Surname, Forename & …": author sorting and calibre's first author read it
            author_sort = ' & '.join(a.sort for a in authors)
            # Compared as the rows found, not as the record spells them: the library finds a
            # name whatever its case, so the same author in another case is no change
            if {a.name for a in authors} != {a.name for a in book.authors} or author_sort != book.author_sort:
                before['authors'] = [a.name for a in book.authors]
                before['author_sort'] = book.author_sort
                dropped += [a for a in book.authors if a not in authors]
                book.authors = authors
                book.author_sort = author_sort
                changed = True

        published = helper.parse_partial_date(record.publishedDate)
        date_ok = not filling or _no_date(book.pubdate) or (published and book.pubdate
                                                             and published.date() < book.pubdate.date())
        if published and date_ok and not _has_date(book.pubdate, published):
            before['pubdate'] = book.pubdate.isoformat() if book.pubdate else None
            book.pubdate = published
            changed = True

        have = {i.type.lower() for i in book.identifiers}
        for kind, value in (record.identifiers or {}).items():
            kind, value = str(kind or '').strip().lower(), str(value or '').strip()
            if kind and value and kind not in have:
                book.identifiers.append(db.Identifiers(value, kind, book.id))
                before.setdefault('identifiers_added', []).append(kind)
                have.add(kind)
                changed = True
            elif kind and value and overwrite:
                # Overwriting, the match's value replaces the book's own of that kind
                for identifier in book.identifiers:
                    if identifier.type.lower() == kind and identifier.val != value:
                        before.setdefault('identifiers_changed', {})[kind] = identifier.val
                        identifier.val = value
                        changed = True

        new_cover = bool(cover) and _cover_wins(book, cover)
        if new_cover:
            book.has_cover = 1
            changed = True

    if not changed:
        return False
    book.last_modified = datetime.now(UTC)
    undo, done = _place_cover(book, cover) if new_cover else (None, None)
    try:
        session.flush()
        for row in dropped:
            if _unused(session, row):  # as a normal edit does
                session.delete(row)
        session.commit()
    except Exception:
        if undo:
            undo()
        raise
    if done:
        done()
        helper.replace_cover_thumbnail_cache(book.id)
    if before and store is not None:
        _keep_change(store, book.id, getattr(getattr(record, 'source', None), 'description', ''), before)
    if changes is not None:
        changes.update(described_changes(book, before, new_cover))
    return True


def _date_text(value):
    """A pubdate as YYYY-MM-DD; '' for none or calibre's "no date"."""
    if not value:
        return ''
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return '' if _no_date(value) else value.date().isoformat()


def described_changes(book, before, new_cover=False) -> dict:
    """What a lookup changed, {field: [before, after]} as text, read from `before` (as
    _apply_record keeps it for Undo) and the book as it is now."""
    def names(rows):
        return ", ".join(row.name.replace('|', ',') for row in rows)
    changes = {}
    if 'title' in before:
        changes['title'] = [before['title'], book.title]
    if 'authors' in before:
        changes['authors'] = [", ".join(n.replace('|', ',') for n in before['authors'] or []), names(book.authors)]
    if 'pubdate' in before:
        changes['pubdate'] = [_date_text(before['pubdate']), _date_text(book.pubdate)]
    if before.get('identifiers_added') or before.get('identifiers_changed'):
        added = set(before.get('identifiers_added') or [])
        replaced = before.get('identifiers_changed') or {}
        changes['identifiers'] = [", ".join(f"{kind} {val}" for kind, val in replaced.items()),
                                  ", ".join(f"{i.type} {i.val}" for i in book.identifiers
                                            if i.type.lower() in added or i.type.lower() in replaced)]
    if new_cover:
        changes['cover'] = ['', 'new']
    return changes


def _keep_change(store, book_id, source, before):
    """Note what a lookup changed, for Undo; a failure here is logged, never the lookup's."""
    try:
        store.save_metadata_change(book_id, source, json.dumps(before, ensure_ascii=False))
    except Exception as e:
        log.debug(f"Could not keep the change to book {book_id}: {e}")


def undo_last_change(book_id: int) -> bool:
    """Put back what the book's latest lookup changed, as it was before; True when there was
    one. The book then counts as edited by hand, so the next lookup only fills its gaps. A
    cover the lookup set stays."""
    store = CWA_DB()
    change = store.last_metadata_change(book_id)
    if not change:
        return False
    before = json.loads(change["before"])
    with library_lock:
        cdb = db.CalibreDB(expire_on_commit=False, init=True)
    try:
        with library_lock:
            book = cdb.get_book(book_id)
            if not book:
                return False
            old = _title_and_author(cdb, book)
            _restore(cdb, book, before)
            _follow_up(cdb, book_id, old, bool(store.get_cwa_settings().get('auto_metadata_enforcement')))
    except Exception:
        with library_lock:
            _rollback(cdb)
        raise
    finally:
        with library_lock:
            cdb.session.close()
    store.drop_metadata_change(change["id"])
    store.save_hand_edit(book_id)
    log.info(f"Undid the {change['source'] or 'metadata'} lookup of book {book_id}")
    return True


def _restore(cdb, book, before):
    """Set the book's fields back to `before` (see _apply_record) and commit."""
    session = cdb.session
    dropped = []
    with session.no_autoflush:
        if 'title' in before:
            book.title = before['title']
        if 'authors' in before:
            authors = []
            for name in before['authors'] or []:
                author = _named(cdb, db.Authors, cdb.get_author_by_name, name, get_sorted_author(name.replace('|', ',')))
                if author not in authors:
                    authors.append(author)
            dropped += [a for a in book.authors if a not in authors]
            book.authors = authors
            book.author_sort = before.get('author_sort') or ' & '.join(a.sort for a in authors)
        if 'pubdate' in before:
            book.pubdate = datetime.fromisoformat(before['pubdate']) if before['pubdate'] else db.Books.DEFAULT_PUBDATE
        added = set(before.get('identifiers_added') or [])
        replaced = before.get('identifiers_changed') or {}
        for identifier in list(book.identifiers):
            if identifier.type.lower() in added:
                book.identifiers.remove(identifier)
                session.delete(identifier)
            elif identifier.type.lower() in replaced:
                identifier.val = replaced[identifier.type.lower()]
    book.last_modified = datetime.now(UTC)
    session.flush()
    for row in dropped:
        if _unused(session, row):
            session.delete(row)
    session.commit()


def _place_cover(book, cover):
    """Moves the downloaded cover into the book's folder, keeping the old one beside it
    until the commit is through. Returns (undo, done)."""
    from cps import config
    current = os.path.join(config.get_book_path(), book.path, 'cover.jpg')
    backup = current + '.before-lookup'
    had_cover = os.path.isfile(current)
    os.makedirs(os.path.dirname(current), exist_ok=True)
    if had_cover:
        os.replace(current, backup)
    shutil.move(cover, current)

    def undo():
        try:
            if had_cover:
                os.replace(backup, current)
            else:
                os.remove(current)
        except OSError as e:
            log.error(f"Could not put back the cover of book {book.id}: {e}")

    def done():
        if had_cover:
            try:
                os.remove(backup)
            except OSError:
                pass
    return undo, done


def _title_and_author(cdb, book):
    """(title, first author) as calibre orders them."""
    authors = cdb.order_authors([book]) if book.authors else []
    return book.title, authors[0].name if authors else None


def _follow_up(cdb, book_id, before, write_files):
    """Move the folder after a new title or first author, then queue the file write."""
    from cps import config
    cdb.session.expire_all()
    book = cdb.session.get(db.Books, book_id)
    if book is None:
        return
    after = _title_and_author(cdb, book)
    if after != before:
        try:
            error = helper.update_dir_structure(book_id, config.get_book_path(), after[1], book=book)
            if error:
                raise RuntimeError(error)
            cdb.session.commit()
        except Exception as ex:
            _rollback(cdb)
            log.error(f"Could not move the folder of book {book_id}: {ex}")
    if write_files and {d.format.upper() for d in book.data} & ENFORCED_FORMATS:
        _write_change_log(book)


def _write_change_log(book):
    """The same log an edit writes; cover_enforcer.py reads the book's metadata back from the library."""
    payload = {
        'title': book.title,
        'authors': ' & '.join(author.name for author in book.authors),
        '_cwa_meta': {'source': 'metadata lookup', 'timestamp': datetime.now().isoformat()},
    }
    path = os.path.join(CHANGE_LOGS_DIR, "%s-%s.json" % (datetime.now().strftime("%Y%m%d%H%M%S"), book.id))
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=4, ensure_ascii=False)
    except OSError as ex:
        log.error(f"Could not queue the file write for book {book.id}: {ex}")
