# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import json
import re
import unicodedata
from difflib import SequenceMatcher

from cps import logger, db, constants
from cps.search_metadata import cl as metadata_providers
import sys
sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from cwa_db import CWA_DB

log = logger.create()

# Minimum SequenceMatcher ratio between normalised titles for a provider result to be
# treated as the same book. "Tide Tables for Beginners" vs "Tide tables and charts" scores ~0.60.
TITLE_MATCH_THRESHOLD = 0.85

_QUOTES = str.maketrans({'\u2018': "'", '\u2019': "'", '\u201c': '"', '\u201d': '"'})
_ARTICLE = re.compile(r'^(the|a|an) ')


def _normalise(text: str) -> str:
    text = unicodedata.normalize('NFKD', (text or '').translate(_QUOTES).casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c)).replace("'", '')
    return ' '.join(re.sub(r'[^\w\s]|_', ' ', text).split())


def _title_variants(title: str) -> set:
    variants = {_normalise(title), _normalise((title or '').split(':')[0])}
    return {_ARTICLE.sub('', v) for v in variants if v} - {''}


def _surnames(authors) -> set:
    names = set()
    for name in authors or []:
        # Calibre sort form "Surname, Forename" puts the surname first
        surname, comma, _ = (name or '').partition(',')
        parts = _normalise(surname).split()
        if parts and parts != ['unknown']:  # Calibre's placeholder author
            names.add(parts[0] if comma else parts[-1])
    return names


def title_similarity(a: str, b: str) -> float:
    return max((SequenceMatcher(None, x, y).ratio()
                for x in _title_variants(a) for y in _title_variants(b)), default=0.0)


def best_metadata_match(title: str, authors, results):
    """Return the result most likely to be the same book, or None if none is close enough.

    A result must have a title at least TITLE_MATCH_THRESHOLD similar and, when both
    sides list authors, share at least one surname.
    """
    book_surnames = _surnames(authors)
    best, best_score = None, 0.0
    for result in results or []:
        score = title_similarity(title, getattr(result, 'title', ''))
        if score < TITLE_MATCH_THRESHOLD or score <= best_score:
            continue
        result_surnames = _surnames(getattr(result, 'authors', None))
        if book_surnames and result_surnames and not book_surnames & result_surnames:
            continue
        best, best_score = result, score
    return best


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

        # Try each provider in order
        metadata_found = False
        matched = False
        for provider_id in provider_hierarchy:
            try:
                # Find the provider
                provider = None
                for p in metadata_providers:
                    if p.__id__ == provider_id:
                        provider = p
                        break

                if not provider or not provider.active:
                    continue
                if not provider.is_globally_enabled(enabled_map):
                    log.debug(f"Provider {provider_id} is globally disabled")
                    continue

                log.debug(f"Trying metadata provider: {provider.__name__}")

                # Search for metadata
                results = provider.search(search_query, "", "en")
                if not results or len(results) == 0:
                    continue

                # Only accept a result that is recognisably the same book
                metadata = best_metadata_match(book.title, author_names, results)
                if metadata is None:
                    log.debug(f"No result from {provider.__name__} matches '{book.title}'")
                    continue
                matched = True

                # Apply metadata to book
                if _apply_metadata_to_book(book, metadata, calibre_db_instance):
                    log.info(f"Successfully applied metadata from {provider.__name__} for book: {book.title}")
                    metadata_found = True
                    break

            except Exception as e:
                log.warning(f"Error fetching metadata from provider {provider_id}: {e}")
                continue

        if not matched:
            log.info(f"No confident metadata match for '{book.title}'; keeping the file's metadata")
        calibre_db_instance.session.close()
        return metadata_found

    except Exception as e:
        log.error(f"Error in fetch_and_apply_metadata: {e}", exc_info=True)
        return False


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
                        author = db.Authors(author_name.strip(), author_name.strip())
                        calibre_db_instance.session.add(author)
                    book.authors.append(author)
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
            for tag_name in metadata.tags:
                if tag_name and tag_name.strip():
                    tag = calibre_db_instance.get_tag_by_name(tag_name.strip())
                    if not tag:
                        tag = db.Tags(name=tag_name.strip())
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
                from datetime import datetime
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
            hasattr(metadata, 'cover') and metadata.cover):
            # TODO: Implement cover resolution checking for smart mode
            # For now, just apply the cover in normal mode
            if not use_smart_application:
                # Apply cover (implementation depends on how covers are handled in Calibre-Web)
                pass

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
