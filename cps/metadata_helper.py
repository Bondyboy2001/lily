# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import functools
import json
import logging
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime, timezone

from cps import logger, db, constants, helper
from cps.helper import get_sorted_author
from cps.search_metadata import cl as metadata_providers
import sys
sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from cwa_db import CWA_DB
from metadata_suggestions import normalise_title
from cps.services.identifiers import ARXIV_ID, DOI_RE, normalise_identifiers, parse_identifier
from cps.tag_cleanup import clean_tags

log = logger.create()

# Calibre's sessions share one SQLite connection (StaticPool), so one thread closing or rolling
# back its session undoes another's unsaved changes. Rebuild metadata looks several books up at
# once: each holds this while it reads or writes the library, and only provider lookups overlap.
library_lock = threading.RLock()

# One normalisation for imports and Fetch metadata's ranking, so a full score there
# means the title an import would accept
_normalise = normalise_title


def _surnames(authors) -> set:
    names = set()
    for name in authors or []:
        # Calibre sort form "Surname, Forename" puts the surname first
        surname, comma, _ = (name or '').partition(',')
        parts = _normalise(surname).split()
        if parts and parts != ['unknown']:  # Calibre's placeholder author
            names.add(parts[0] if comma else parts[-1])
    return names


def titles_match(a: str, b: str) -> bool:
    """The same title, ignoring only case, accents, punctuation and spacing. A subtitle or a
    leading "The" makes it a different title."""
    a = _normalise(a)
    return bool(a) and a == _normalise(b)


def best_metadata_match(title: str, authors, results):
    """Return the result that is exactly this book, or None.

    The title must match exactly (see titles_match) and, when both sides list authors, they
    must share a surname. A result naming the authors wins over one that names none.
    """
    book_surnames = _surnames(authors)
    title_only = None
    for result in results or []:
        if not titles_match(title, getattr(result, 'title', '')):
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


# arXiv stamps its id down the first page's margin: "arXiv:1706.03762v7 [cs.CL] 2 Aug 2023"
_ARXIV_STAMP = re.compile(rf"arxiv:\s*({ARXIV_ID})", re.I)


def find_paper_identifiers(title: str, page_text: str = "", identifiers=None) -> dict:
    """The arXiv id and DOI naming a paper: the book's own, else one its title is
    (a PDF often imports under its file name, "1706.03762v7") or one on its first
    page (arXiv's margin stamp, a journal's DOI line)."""
    found = {k: v for k, v in normalise_identifiers(identifiers or {}).items()
             if k in ("arxiv", "doi")}
    typed = parse_identifier(title)
    for text in (title, page_text):
        stamp = _ARXIV_STAMP.search(text or "")
        if stamp:
            typed.setdefault("arxiv", stamp.group(1))
    doi = DOI_RE.search(page_text or "")
    if doi:
        typed.setdefault("doi", doi.group(0).rstrip(".,;:)]}"))
    for key in ("arxiv", "doi"):
        if typed.get(key):
            found.setdefault(key, typed[key])
    return found


def _squash(text: str) -> str:
    """Letters and digits only: PDF text often loses or adds spaces and hyphens."""
    return re.sub(r'[\W_]+', '', _normalise(text))


def found_by_id_is_this_book(record, ids: dict, title: str, authors, page_text: str = "",
                             own_ids=None) -> bool:
    """Whether a record an identifier lookup returned is this book. The arXiv id
    (from the paper's own stamp or file name) and the book's own DOI are trusted;
    otherwise the title must match exactly or an author be shared (an ISBN can be
    a placeholder or another book's), or for a DOI read off the first page, which
    may be a citation, the record's title must be on that page too."""
    record_ids = normalise_identifiers(getattr(record, 'identifiers', None) or {})
    own_ids = own_ids or {}
    if ids.get('arxiv') and record_ids.get('arxiv') == ids['arxiv']:
        return True
    if ids.get('doi') and ids['doi'].lower() == own_ids.get('doi', '').lower():
        return True
    if titles_match(title, record.title) or _surnames(authors) & _surnames(record.authors):
        return True
    return bool(ids.get('doi')) and title_on_page(record.title, page_text)


def title_on_page(title: str, page_text: str) -> bool:
    """Whether the title appears in the page's text, spacing and punctuation aside."""
    title = _squash(title)
    return bool(title) and title in _squash(page_text)


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


def _apply_cover(book, url: str) -> bool:
    """Saves a provider's cover for the book when it has none, or when the new one
    is larger: a provider's cover is often a small thumbnail, worse than the one
    the file came with. Any doubt keeps the current cover."""
    if not (url or '').startswith(('http://', 'https://')):
        return False  # none, or the generic placeholder
    from cps import config
    book_dir = os.path.join(config.get_book_path(), book.path)
    current = os.path.join(book_dir, 'cover.jpg')
    with tempfile.TemporaryDirectory() as tmp:
        # save_cover_from_url joins its path onto the library's; an absolute one
        # lands here, so the current cover stays until the new one wins
        saved, error = helper.save_cover_from_url(url, tmp)
        fetched = os.path.join(tmp, 'cover.jpg')
        if not saved or not os.path.isfile(fetched):
            log.debug(f"Cover {url} not saved: {error}")
            return False
        if book.has_cover and os.path.isfile(current):
            new_area, old_area = _image_area(fetched), _image_area(current)
            if not new_area or not old_area or new_area <= old_area:
                return False
        os.makedirs(book_dir, exist_ok=True)
        shutil.move(fetched, current)
    book.has_cover = 1
    helper.replace_cover_thumbnail_cache(book.id)
    log.info(f"Saved the cover from {url} for book {book.id}")
    return True


def fetch_and_apply_metadata(book_id: int, force: bool = False) -> bool:
    """
    Fetch metadata for a newly ingested book and apply it if settings allow.

    Args:
        book_id: The ID of the book to fetch metadata for
        force: Look it up even when "Fetch metadata for new books" is off (Rebuild metadata)

    Returns:
        bool: True if metadata was successfully fetched and applied, False otherwise
    """
    try:
        if not db.CalibreDB.session_factory:
            log.error("CalibreDB not initialized; skipping metadata fetch")
            return False

        # The library is read and written under library_lock; only the provider lookups
        # run beside other books' (Rebuild metadata)
        with library_lock:
            # Check global settings (admin-controlled only)
            cwa_db = CWA_DB()
            cwa_settings = cwa_db.get_cwa_settings()

            if not force and not cwa_settings.get('auto_metadata_fetch_enabled', False):
                log.debug("Auto metadata fetch disabled by administrator")
                return False

            # Get the book
            calibre_db_instance = db.CalibreDB(expire_on_commit=False, init=True)
            book = calibre_db_instance.get_book(book_id)
            if not book:
                log.error(f"Book with ID {book_id} not found")
                return False

            # Create search query from book title and author
            search_query = book.title
            # calibre's "Unknown" stand-in is not a name to search for
            author_names = [author.name for author in book.authors or []
                            if not constants.is_unknown_author(author.name)]
            if author_names:
                search_query += " " + " ".join(author_names)

            log.info(f"Fetching metadata for: {search_query}")

            # Get provider hierarchy
            try:
                provider_hierarchy = json.loads(cwa_settings.get('metadata_provider_hierarchy', '["google","openlibrary","hardcover","googlescholar"]'))
            except (json.JSONDecodeError, TypeError):
                provider_hierarchy = ["google", "openlibrary", "hardcover", "googlescholar"]

            # Global provider enablement map
            enabled_map = _parse_metadata_providers_enabled(
                cwa_settings.get('metadata_providers_enabled', '{}')
            )

            # Saved order first, then any provider it doesn't name (the settings page does the same)
            available_ids = [p.__id__ for p in metadata_providers]
            provider_hierarchy = [p for p in provider_hierarchy if p in available_ids] + \
                [p for p in available_ids if p not in provider_hierarchy]

            providers = []
            for provider_id in provider_hierarchy:
                provider = next((p for p in metadata_providers if p.__id__ == provider_id), None)
                if not provider or not provider.active:
                    continue
                if not provider.is_globally_enabled(enabled_map):
                    log.debug(f"Provider {provider_id} is globally disabled")
                    continue
                providers.append(provider)

            # A book is looked up exactly by its identifiers first: a paper by its arXiv
            # id or DOI, as its title is often the file name, which no title search
            # matches; a book by its ISBN
            own_ids = normalise_identifiers({i.type: i.val for i in book.identifiers or []})
            page_text = pdf_first_page_text(book)
            lookup_ids = find_paper_identifiers(book.title, page_text, own_ids)
            if own_ids.get('isbn'):
                lookup_ids['isbn'] = own_ids['isbn']
            title = book.title

        metadata_found = False
        matched = False
        if lookup_ids:
            log.info(f"Looking up '{title}' by {lookup_ids}")
        for provider in providers:
            ids = {k: v for k, v in lookup_ids.items() if k in provider.identifier_types}
            if not ids:
                continue
            try:
                results = provider.search_identifiers(ids, "", "en")
            except Exception as e:
                log.warning(f"Error looking up {ids} with provider {provider.__id__}: {e}")
                continue
            record = next((r for r in results or [] if found_by_id_is_this_book(
                r, ids, title, author_names, page_text, own_ids)), None)
            if record is None:
                continue
            matched = True
            if _apply_locked(book, record, calibre_db_instance, f"{provider.__name__} found by {ids}"):
                metadata_found = True
                break

        # Otherwise try each provider's title search in order
        for provider in providers if not matched else []:
            try:
                log.debug(f"Trying metadata provider: {provider.__name__}")

                # Search for metadata
                results = provider.search(search_query, "", "en")
                if not results or len(results) == 0:
                    continue

                # Only accept a result that is exactly this book
                metadata = best_metadata_match(title, author_names, results)
                if metadata is None:
                    log.debug(f"No result from {provider.__name__} matches '{title}'")
                    continue
                matched = True

                # Apply metadata to book
                if _apply_locked(book, metadata, calibre_db_instance, provider.__name__):
                    metadata_found = True
                    break

            except Exception as e:
                log.warning(f"Error fetching metadata from provider {provider.__id__}: {e}")
                continue

        if not matched:
            log.info(f"No exact metadata match for '{title}'; keeping the file's metadata")
        with library_lock:
            calibre_db_instance.session.close()
        return metadata_found

    except Exception as e:
        log.error(f"Error in fetch_and_apply_metadata: {e}", exc_info=True)
        return False


def _apply_locked(book, metadata, calibre_db_instance, source) -> bool:
    with library_lock:
        if not _apply_metadata_to_book(book, metadata, calibre_db_instance):
            return False
        log.info(f"Applied metadata from {source} for book: {book.title}")
        return True


def _apply_metadata_to_book(book, metadata, calibre_db_instance) -> bool:
    """
    Apply fetched metadata to a book record.

    Args:
        book: The book database record
        metadata: The metadata record from provider
        calibre_db_instance: Database instance

    Returns:
        bool: True if metadata was successfully applied
    """
    try:
        # Get CWA settings to check smart application preference and field selections
        cwa_db = CWA_DB()
        cwa_settings = cwa_db.get_cwa_settings()
        use_smart_application = cwa_settings.get('auto_metadata_smart_application', False)

        updated = False

        # Update title - only if enabled in settings
        if (cwa_settings.get('auto_metadata_update_title', True) and
            metadata.title and metadata.title.strip()):
            if use_smart_application:
                if len(metadata.title.strip()) > len(book.title.strip()):
                    book.title = metadata.title.strip()
                    updated = True
            else:
                book.title = metadata.title.strip()
                updated = True

        # Update authors - only if enabled in settings
        if (cwa_settings.get('auto_metadata_update_authors', True) and
            metadata.authors and len(metadata.authors) > 0):
            # Clear existing authors
            book.authors.clear()
            for author_name in metadata.authors:
                if author_name and author_name.strip():
                    author = calibre_db_instance.get_author_by_name(author_name.strip())
                    if not author:
                        author = db.Authors(author_name.strip(), get_sorted_author(author_name.strip()))
                        calibre_db_instance.session.add(author)
                    book.authors.append(author)
            # "Surname, Forename & …" in the new order: author sorting and calibre's first author read it
            book.author_sort = ' & '.join(author.sort for author in book.authors)
            updated = True

        # Update description - only if enabled in settings
        if (cwa_settings.get('auto_metadata_update_description', True) and
            metadata.description and metadata.description.strip()):
            current_description = book.comments[0].text if book.comments else ""
            if use_smart_application:
                if len(metadata.description.strip()) > len(current_description):
                    if book.comments:
                        book.comments[0].text = metadata.description.strip()
                    else:
                        comment = db.Comments(metadata.description.strip(), book.id)
                        calibre_db_instance.session.add(comment)
                    updated = True
            else:
                if book.comments:
                    book.comments[0].text = metadata.description.strip()
                else:
                    comment = db.Comments(metadata.description.strip(), book.id)
                    calibre_db_instance.session.add(comment)
                updated = True

        # Update publisher - only if enabled in settings
        if (cwa_settings.get('auto_metadata_update_publisher', True) and
            metadata.publisher and metadata.publisher.strip()):
            if use_smart_application:
                if not book.publishers or len(book.publishers) == 0:
                    publisher = calibre_db_instance.get_publisher_by_name(metadata.publisher.strip())
                    if not publisher:
                        publisher = db.Publishers(metadata.publisher.strip(), metadata.publisher.strip())
                        calibre_db_instance.session.add(publisher)
                    book.publishers = [publisher]
                    updated = True
            else:
                # Clear existing publishers and add new one
                book.publishers.clear()
                publisher = calibre_db_instance.get_publisher_by_name(metadata.publisher.strip())
                if not publisher:
                    publisher = db.Publishers(metadata.publisher.strip(), metadata.publisher.strip())
                    calibre_db_instance.session.add(publisher)
                book.publishers = [publisher]
                updated = True

        # Update tags if available and enabled in settings
        if (cwa_settings.get('auto_metadata_update_tags', True) and
            hasattr(metadata, 'tags') and metadata.tags):
            # Only subjects: a provider's tags can be shop categories or the book's own title
            tag_names = clean_tags(metadata.tags,
                                   title=book.title,
                                   authors=[author.name for author in book.authors],
                                   publishers=[publisher.name for publisher in book.publishers],
                                   series=[serie.name for serie in book.series])
            for tag_name in tag_names:
                tag = calibre_db_instance.get_tag_by_name(tag_name)
                if not tag:
                    tag = db.Tags(name=tag_name)
                    calibre_db_instance.session.add(tag)
                if tag not in book.tags:
                    book.tags.append(tag)
                    updated = True

        # Update series if available and enabled in settings
        if (cwa_settings.get('auto_metadata_update_series', True) and
            hasattr(metadata, 'series') and metadata.series and metadata.series.strip()):
            series = calibre_db_instance.get_series_by_name(metadata.series.strip())
            if not series:
                series = db.Series(metadata.series.strip(), metadata.series.strip())
                calibre_db_instance.session.add(series)
            book.series.clear()
            book.series.append(series)

            # Set series index if available
            if hasattr(metadata, 'series_index') and metadata.series_index:
                try:
                    # Convert to float first to validate, then store as string (DB column is String)
                    float_value = float(metadata.series_index)
                    book.series_index = str(float_value)
                except (ValueError, TypeError):
                    book.series_index = '1.0'
            updated = True

        # Update published date if available and enabled in settings
        if (cwa_settings.get('auto_metadata_update_published_date', True) and
            hasattr(metadata, 'publishedDate') and metadata.publishedDate):
            try:
                if isinstance(metadata.publishedDate, str):
                    # Try to parse various date formats
                    for fmt in ['%Y-%m-%d', '%Y-%m', '%Y']:
                        try:
                            book.pubdate = datetime.strptime(metadata.publishedDate, fmt).date()
                            updated = True
                            break
                        except ValueError:
                            continue
                elif hasattr(metadata.publishedDate, 'date'):
                    book.pubdate = metadata.publishedDate.date()
                    updated = True
            except Exception as e:
                log.warning(f"Error parsing published date: {e}")

        # Update rating if available and enabled in settings
        if (cwa_settings.get('auto_metadata_update_rating', True) and
            hasattr(metadata, 'rating') and metadata.rating):
            try:
                rating_value = float(metadata.rating)
                if 0 <= rating_value <= 10:  # Calibre uses 0-10 scale
                    if book.ratings:
                        book.ratings[0].rating = int(rating_value * 2)  # Convert to Calibre's 0-10 scale
                    else:
                        rating = db.Ratings(rating=int(rating_value * 2))
                        calibre_db_instance.session.add(rating)
                        book.ratings = [rating]
                    updated = True
            except (ValueError, TypeError):
                pass

        # Update identifiers if available and enabled in settings
        if (cwa_settings.get('auto_metadata_update_identifiers', True) and
            hasattr(metadata, 'identifiers') and metadata.identifiers):
            for identifier_type, identifier_value in metadata.identifiers.items():
                if identifier_type and identifier_value:
                    # Check if identifier already exists
                    existing = False
                    for identifier in book.identifiers:
                        if identifier.type == identifier_type:
                            identifier.val = identifier_value
                            existing = True
                            break
                    if not existing:
                        new_identifier = db.Identifiers(identifier_value, identifier_type, book.id)
                        calibre_db_instance.session.add(new_identifier)
                        book.identifiers.append(new_identifier)
                    updated = True

        # Handle cover image - only if enabled in settings
        if (cwa_settings.get('auto_metadata_update_cover', True) and
                _apply_cover(book, getattr(metadata, 'cover', ''))):
            # A new cover-URL cache key, so browsers fetch the new cover
            book.last_modified = datetime.now(timezone.utc)
            updated = True

        if updated:
            calibre_db_instance.session.commit()

        return updated

    except Exception as e:
        log.error(f"Error applying metadata to book {getattr(book, 'id', 'unknown')}: {e}")
        calibre_db_instance.session.rollback()
        return False


def _parse_metadata_providers_enabled(raw_value):
    """Lightweight parser for metadata_providers_enabled without importing cwa_functions."""
    try:
        if raw_value is None:
            return {}
        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode('utf-8', errors='ignore')
        if isinstance(raw_value, str):
            s = raw_value.strip()
            if not s:
                return {}
            if s.startswith("'") and s.endswith("'"):
                s = s[1:-1]
            if not s:
                return {}
            data = json.loads(s)
            return data if isinstance(data, dict) else {}
        if isinstance(raw_value, dict):
            return raw_value
        return {}
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        return {}
