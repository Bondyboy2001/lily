# -*- coding: utf-8 -*-
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
from datetime import datetime, timezone

from cps import logger, db, constants, helper
from cps.clean_html import clean_string
from cps.helper import get_sorted_author
from cps.search_metadata import cl as metadata_providers
from cps.services.Metadata import ProviderBusy
from cps.services.identifiers import (ARXIV_ID, DOI_RE, arxiv_id_from_doi, normalise_identifiers,
                                      parse_identifier)
from cps.tag_cleanup import clean_tags
sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from cwa_db import CWA_DB  # noqa: E402
from metadata_suggestions import normalise_title, surname, title_forms  # noqa: E402

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


def _surnames(authors) -> set:
    """The authors' surnames, by the rule Fetch metadata ranks with."""
    return {name for name in map(surname, authors or []) if name}


def titles_match(a: str, b: str) -> bool:
    """The same title, ignoring only case, accents, punctuation and spacing. A subtitle or a
    leading "The" makes it a different title."""
    a = _normalise(a)
    return bool(a) and a == _normalise(b)


def matched_title(title: str, record):
    """The record's title as this book has it, in the provider's spelling: in full or, for a
    record with a subtitle, without it. None when the book's title is neither."""
    return next((form for form in title_forms(record) if titles_match(title, form)), None)


def best_metadata_match(title: str, authors, results):
    """Return the result that is exactly this book, or None.

    The title must match exactly (see titles_match), with or without the result's subtitle,
    and, when both sides list authors, they must share a surname. A result naming the
    authors wins over one that names none.
    """
    book_surnames = _surnames(authors)
    title_only = None
    for result in results or []:
        if matched_title(title, result) is None:
            continue
        result_surnames = _surnames(getattr(result, 'authors', None))
        if book_surnames and result_surnames:
            if book_surnames & result_surnames:
                return result
        elif result_surnames or not book_surnames:
            return result
        elif title_only is None:
            title_only = result
    return title_only


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


def _squash(text: str) -> str:
    """Letters and digits only: PDF text often loses or adds spaces and hyphens."""
    return re.sub(r'[\W_]+', '', _normalise(text))


def title_on_page(title: str, page_text: str) -> bool:
    """Whether the title appears in the page's text, spacing and punctuation aside."""
    title = _squash(title)
    return bool(title) and title in _squash(page_text)


def _main_title(title: str) -> str:
    """The title without its subtitle: "Dune: Deluxe Edition" -> "dune"."""
    return _normalise((title or '').split(':')[0])


def found_by_id_is_this_book(record, ids: dict, title: str, authors, page_text: str = "",
                             own_ids=None) -> bool:
    """Whether a record an identifier lookup returned is this book.

    A record carrying the arXiv id or DOI looked up is this paper when the id is the
    book's own or its file name; one read off the first page may be a citation, so the
    record's title must be on that page too. Anything else (an ISBN can be a placeholder
    or another book's) needs the same title, subtitle aside, and no other author."""
    record_ids = normalise_identifiers(getattr(record, 'identifiers', None) or {})
    trusted = find_paper_identifiers(title, "", own_ids)
    for key in ("arxiv", "doi"):
        wanted = (ids.get(key) or "").lower()
        if not wanted or (record_ids.get(key) or "").lower() != wanted:
            continue
        if (trusted.get(key) or "").lower() == wanted or title_on_page(record.title, page_text):
            return True
    if not _main_title(title) or _main_title(title) != _main_title(record.title):
        return False
    book_surnames, record_surnames = _surnames(authors), _surnames(record.authors)
    return not (book_surnames and record_surnames and not book_surnames & record_surnames)


def pdf_first_page_text(book) -> str:
    """The text of the book's PDF's first page; empty without a PDF or text layer."""
    from cps import config
    pdf = next((d for d in book.data or [] if (d.format or "").upper() == "PDF"), None)
    if pdf is None:
        return ""
    path = os.path.join(config.get_book_path(), book.path, pdf.name + ".pdf")
    try:
        return _read_first_page(path, os.path.getmtime(path))
    except OSError:
        return ""


@functools.lru_cache(maxsize=32)
def _read_first_page(path: str, mtime: float) -> str:
    """The first page's text; keyed by mtime so a replaced file is read again. Fetch
    metadata asks for it once per provider."""
    pypdf_log = logging.getLogger("pypdf")
    level = pypdf_log.level
    # pypdf warns about every font it can't fully decode; the ids read fine regardless
    pypdf_log.setLevel(logging.ERROR)
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        return (reader.pages[0].extract_text() or "") if reader.pages else ""
    except Exception as e:
        log.debug(f"Could not read the first page of {path}: {e}")
        return ""
    finally:
        pypdf_log.setLevel(level)


def _image_area(path: str) -> int:
    """Width times height of an image file; 0 when it can't be read."""
    try:
        from wand.image import Image
        with Image(filename=path) as img:
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


def _largest_cover(record) -> int:
    """The most pixels the record's cover can have, when its provider says (0 when not)."""
    source = getattr(getattr(record, 'source', None), 'id', None)
    provider = next((p for p in metadata_providers if source and getattr(p, '__id__', None) == source), None)
    return getattr(provider, 'COVER_MAX_PIXELS', 0)


def _cover_worth_fetching(store, book_id, record, current) -> bool:
    """Whether to download the record's cover to weigh it against the book's (see _cover_wins).
    Not when it can't be larger, or when this very cover was already weighed against the
    one the book still has: a rebuild would download every matched book's cover again."""
    if current is None:
        return True
    largest = _largest_cover(record)
    if largest and _image_area(current) >= largest:
        return False
    try:
        return store.get_cover_check(book_id) != (record.cover, _cover_state(current))
    except Exception as e:
        log.debug(f"No cover check to read for book {book_id}: {e}")
        return True


def _remember_cover(store, book_id, url, current) -> None:
    """Note that the cover at url was weighed against the book's, as it now is."""
    try:
        store.save_cover_check(book_id, url, _cover_state(current))
    except Exception as e:
        log.debug(f"Could not note the cover check of book {book_id}: {e}")


def fetch_and_apply_metadata(book_id: int, force: bool = False, unanswered=None) -> bool:
    """Look the book up and apply an exact match; True when the book changed.

    Runs for a new book when "Fetch metadata for new books" is on, and for every book in
    Rebuild metadata (force). The library is read, then the providers are asked and the
    cover downloaded without holding it, then every change is made and committed at once
    under library_lock, so the write lock is held for moments, not for a download. A new
    title or first author moves the book's folder, and with "Write edits into book files"
    on, the change is queued for the files. The names of providers that failed to answer
    are added to `unanswered` (a set) when one is given."""
    if not db.CalibreDB.session_factory:
        log.error("CalibreDB not initialized; skipping metadata fetch")
        return False
    store = CWA_DB()
    settings = store.get_cwa_settings()
    if not force and not settings.get('auto_metadata_fetch_enabled'):
        return False
    with library_lock:
        cdb = db.CalibreDB(expire_on_commit=False, init=True)
    try:
        with library_lock:
            book = cdb.get_book(book_id)
            if not book:
                log.error(f"Book with ID {book_id} not found")
                return False
            # calibre's "Unknown" stand-in is not a name to search for; it keeps a name's comma as "|"
            authors = [a.name.replace('|', ',') for a in book.authors or []
                       if not constants.is_unknown_author(a.name)]
            own_ids = normalise_identifiers({i.type: i.val for i in book.identifiers or []})
            page_text = pdf_first_page_text(book)
            title = book.title
            current_cover = _cover_path(book)
        record = _find_record(title, authors, own_ids, page_text, unanswered)
        if record is None:
            return False
        url = getattr(record, 'cover', '') or ''
        with tempfile.TemporaryDirectory() as tmp:
            cover = None
            if _cover_worth_fetching(store, book_id, record, current_cover):
                cover = _download_cover(url, tmp)
            with library_lock:
                before = _title_and_author(cdb, book)
                changed = _apply_record(cdb, book, record, cover)
                if cover:
                    # Weighed, whichever won: the same cover need not be fetched to compare again
                    _remember_cover(store, book_id, url, _cover_path(book))
                if not changed:
                    return False
                source = getattr(getattr(record, 'source', None), 'description', 'a provider')
                log.info(f"Applied metadata from {source} to book {book_id}")
                _follow_up(cdb, book_id, before, bool(settings.get('auto_metadata_enforcement')))
        return True
    except Exception as e:
        log.error(f"Metadata lookup for book {book_id} failed: {e}", exc_info=True)
        with library_lock:
            _rollback(cdb)
        return False
    finally:
        with library_lock:
            cdb.session.close()


def _rollback(cdb):
    try:
        cdb.session.rollback()
    except Exception as e:
        log.error(f"Rollback failed: {e}")


def _no_answer(provider, what, error, unanswered) -> None:
    """Log a provider's failure and note its name for the caller."""
    if unanswered is not None:
        unanswered.add(provider.__name__)
    # Left alone after a 429: said once then, not for every book until it is asked again
    level = log.debug if isinstance(error, ProviderBusy) else log.warning
    level(f"{what} with {provider.__name__} failed: {error}")


def _find_record(title, authors, own_ids, page_text, unanswered=None):
    """The provider record that is exactly this book, or None. An identifier lookup
    comes first: a paper by its arXiv id or DOI, as its title is often the file name,
    which no title search matches; a book by its ISBN. Then each provider's title search.
    Providers that fail to answer are added to `unanswered`."""
    lookup_ids = find_paper_identifiers(title, page_text, own_ids)
    if own_ids.get('isbn'):
        lookup_ids['isbn'] = own_ids['isbn']
    for provider in metadata_providers:
        ids = {k: v for k, v in lookup_ids.items() if k in provider.identifier_types}
        if not ids:
            continue
        try:
            results = provider.search_identifiers(ids, "", "en") or []
        except Exception as e:
            _no_answer(provider, f"Looking up {ids}", e, unanswered)
            continue
        record = next((r for r in results if found_by_id_is_this_book(
            r, ids, title, authors, page_text, own_ids)), None)
        if record is not None:
            return record
    query = " ".join([title] + authors)
    for provider in metadata_providers:
        # Without the details a provider fetches per result, when it can leave them out
        # (services/Metadata.py): only the record applied needs them
        search = getattr(provider, 'search_titles', provider.search)
        try:
            record = best_metadata_match(title, authors, search(query, "", "en") or [])
            if record is not None and hasattr(provider, 'complete'):
                record = provider.complete(record)
        except Exception as e:
            _no_answer(provider, f"Searching for '{query}'", e, unanswered)
            continue
        if record is not None:
            return record
    log.info(f"No exact metadata match for '{title}'; keeping its details")
    return None


def _parse_date(value):
    """A provider's "2016-05-03", "2016-05" or "2016" as a datetime, or None."""
    for fmt in ('%Y-%m-%d', '%Y-%m', '%Y'):
        try:
            return datetime.strptime(str(value or '').strip(), fmt)
        except ValueError:
            continue
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


def _index(value):
    """A series index as a number, or None when there is none (providers give 0 or '')."""
    try:
        return float(value) or None
    except (TypeError, ValueError):
        return None


def _named(cdb, model, lookup, name, *extra):
    """The row called name, or a new one."""
    row = lookup(name)
    if row is None:
        row = model(name, *extra)
        cdb.session.add(row)
    return row


def _same_rows(a, b) -> bool:
    """The same library rows in any order. Rows compare by name and can't be hashed."""
    return len(a) == len(b) and all(any(x is y for y in b) for x in a)


def _apply_record(cdb, book, record, cover):
    """Writes what the record changes and commits; True when anything changed. Only fields
    the record has are touched; a book's own identifiers are kept and new ones added. The
    rating is left alone: a provider's is its readers' average, not this library's."""
    session = cdb.session
    changed = False
    dropped = []
    with session.no_autoflush:
        # A book that goes by the title without its subtitle (or with it) keeps going by that
        title = (matched_title(book.title, record) or record.title or '').strip()
        if title and title != book.title:
            book.title = title
            changed = True

        # calibre keeps a name's comma as "|"; names differing only in case are one author
        names = {}
        for name in record.authors or []:
            name = (name or '').strip().replace(',', '|')
            if name:
                names.setdefault(name.casefold(), name)
        if names:
            authors = []
            for name in names.values():
                author = _named(cdb, db.Authors, cdb.get_author_by_name, name,
                                get_sorted_author(name.replace('|', ',')))
                if author not in authors:
                    authors.append(author)
            # "Surname, Forename & …": author sorting and calibre's first author read it
            author_sort = ' & '.join(a.sort for a in authors)
            # Compared as rows and their order, not as text: the library finds a name whatever
            # its case, so the same author spelt in another case is no change
            if not _same_rows(authors, book.authors) or author_sort != book.author_sort:
                dropped += [a for a in book.authors if a not in authors]
                book.authors = authors
                book.author_sort = author_sort
                changed = True

        # Cleaned like an edit's: a description is shown as HTML, and a provider's can be anyone's
        description = (record.description or '').strip()
        description = clean_string(description, book.id) if description else ''
        if description and description != (book.comments[0].text if book.comments else ''):
            if book.comments:
                book.comments[0].text = description
            else:
                session.add(db.Comments(description, book.id))
            changed = True

        publisher = (record.publisher or '').strip()
        if publisher:
            row = _named(cdb, db.Publishers, cdb.get_publisher_by_name, publisher, publisher)
            if not _same_rows(book.publishers, [row]):
                dropped += [p for p in book.publishers if p is not row]
                book.publishers = [row]
                changed = True

        # Only subjects: a provider's tags can be shop categories or the book's own title
        for name in clean_tags(record.tags or [], title=book.title,
                               authors=[a.name for a in book.authors],
                               publishers=[p.name for p in book.publishers],
                               series=[s.name for s in book.series]):
            tag = _named(cdb, db.Tags, cdb.get_tag_by_name, name)
            if tag not in book.tags:
                book.tags.append(tag)
                changed = True

        series = (record.series or '').strip()
        if series:
            row = _named(cdb, db.Series, cdb.get_series_by_name, series, series)
            new_series = not _same_rows(book.series, [row])
            if new_series:
                dropped += [s for s in book.series if s is not row]
                book.series = [row]
                changed = True
            # A new series starts at the record's index, or 1; the same one takes the record's
            index = _index(record.series_index) or (1.0 if new_series else None)
            if index and index != _index(book.series_index):
                book.series_index = str(index)
                changed = True

        published = _parse_date(record.publishedDate)
        if published and not _has_date(book.pubdate, published):
            book.pubdate = published
            changed = True

        have = {i.type.lower() for i in book.identifiers}
        for kind, value in (record.identifiers or {}).items():
            kind, value = str(kind or '').strip().lower(), str(value or '').strip()
            if kind and value and kind not in have:
                book.identifiers.append(db.Identifiers(value, kind, book.id))
                have.add(kind)
                changed = True

        new_cover = bool(cover) and _cover_wins(book, cover)
        if new_cover:
            book.has_cover = 1
            changed = True

    if not changed:
        return False
    book.last_modified = datetime.now(timezone.utc)
    undo, done = _place_cover(book, cover) if new_cover else (None, None)
    try:
        session.flush()
        for row in dropped:
            if not row.books:  # nothing else uses it, as a normal edit does
                session.delete(row)
        session.commit()
    except Exception:
        if undo:
            undo()
        raise
    if done:
        done()
        helper.replace_cover_thumbnail_cache(book.id)
    return True


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
