# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Pure rules for duplicate detection and resolution: timestamp normalisation, group
hashing, title normalisation and which book of a group to keep. No routes and no
database access beyond reading the format-priority setting, so cps/duplicate_index.py
and the scan task can use them without importing the duplicates blueprint."""

from datetime import datetime, timezone
import hashlib
import sys

from . import logger

sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from cwa_db import CWA_DB

log = logger.create()


def _normalize_timestamp(ts):
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _timestamp_or_default(ts, default):
    normalized = _normalize_timestamp(ts)
    return normalized if normalized is not None else default


_AWARE_MIN = datetime.min.replace(tzinfo=timezone.utc)
_AWARE_MAX = datetime.max.replace(tzinfo=timezone.utc)


def generate_group_hash(title, author):
    """Generate MD5 hash for a duplicate group based on title and author

    Args:
        title: Book title (will be normalized)
        author: Primary author name (will be normalized)

    Returns:
        32-character MD5 hash string
    """
    # Normalize inputs - lowercase, strip whitespace
    normalized_title = (title or "untitled").lower().strip()
    normalized_author = (author or "unknown").lower().strip()

    # Create composite key
    composite = f"{normalized_title}|{normalized_author}"

    # Generate MD5 hash
    return hashlib.md5(composite.encode('utf-8')).hexdigest()


def normalize_title_for_duplicates(title, primary_author=None):
    """Normalize title for duplicate detection.

    If the title starts with the primary author (e.g., "Homer, the Iliad"),
    strip the leading author prefix to avoid false negatives.
    """
    normalized = (title or "untitled").lower().strip()
    if primary_author:
        author_norm = str(primary_author).lower().strip()
        author_prefix = f"{author_norm}, "
        if normalized.startswith(author_prefix):
            normalized = normalized[len(author_prefix):].strip()
    return normalized


def validate_resolution_strategy(strategy):
    """Validate that strategy is one of the allowed values"""
    valid_strategies = ['newest', 'oldest', 'merge', 'highest_quality_format', 'most_metadata', 'largest_file_size']
    return strategy in valid_strategies


def select_book_to_keep(books, strategy):
    """
    Select which book to keep from a duplicate group based on strategy.

    Args:
        books: List of book objects from find_duplicate_books()
        strategy: One of 'newest', 'highest_quality_format', 'most_metadata', 'largest_file_size'

    Returns:
        The book object to keep
    """
    if not books:
        return None

    if strategy == 'newest':
        # Keep the most recently added book
        return max(books, key=lambda b: _timestamp_or_default(b.timestamp, _AWARE_MIN))

    elif strategy == 'oldest':
        # Keep the earliest added book
        return min(books, key=lambda b: _timestamp_or_default(b.timestamp, _AWARE_MAX))

    elif strategy == 'merge':
        # Merge into the newest book by default
        return max(books, key=lambda b: _timestamp_or_default(b.timestamp, _AWARE_MIN))

    elif strategy == 'highest_quality_format':
        # Get format priority from settings
        try:
            import json
            cwa_db = CWA_DB()
            format_priority_json = cwa_db.cwa_settings.get('duplicate_format_priority', '{}')
            format_priority = json.loads(format_priority_json)
        except Exception as e:
            log.warning("[cwa-duplicates] Error loading format priority from settings, using defaults: %s", str(e))
            # Fallback to default priority
            format_priority = {
                'EPUB': 100,
                'KEPUB': 95,
                'AZW3': 90,
                'MOBI': 80,
                'AZW': 75,
                'PDF': 60,
                'TXT': 40,
                'CBZ': 35,
                'CBR': 35,
                'FB2': 30,
                'DJVU': 25,
                'HTML': 20,
                'RTF': 15,
                'DOC': 10,
                'DOCX': 10,
            }

        def get_best_format_score(book):
            """Calculate best format score for a book"""
            if not book.data:
                return 0
            scores = [format_priority.get(data.format.upper(), 0) for data in book.data if data.format]
            return max(scores) if scores else 0

        # Keep book with highest quality format, fallback to newest if tie
        return max(books, key=lambda b: (get_best_format_score(b), _timestamp_or_default(b.timestamp, _AWARE_MIN)))

    elif strategy == 'most_metadata':
        # Count metadata completeness
        def metadata_score(book):
            score = 0

            # Tags
            if hasattr(book, 'tags') and book.tags:
                score += len(book.tags) * 2

            # Series
            if hasattr(book, 'series') and book.series:
                score += 5

            # Rating
            if hasattr(book, 'ratings') and book.ratings:
                for rating in book.ratings:
                    if rating.rating and rating.rating > 0:
                        score += 3

            # Description/comments
            if hasattr(book, 'comments') and book.comments:
                for comment in book.comments:
                    if comment.text and len(comment.text.strip()) > 50:
                        score += 10

            # Publisher
            if hasattr(book, 'publishers') and book.publishers:
                score += 2

            # Published date
            if hasattr(book, 'pubdate') and book.pubdate:
                score += 2

            # Identifiers (ISBN, etc.)
            if hasattr(book, 'identifiers') and book.identifiers:
                score += len(book.identifiers) * 3

            # Number of formats
            if hasattr(book, 'data') and book.data:
                score += len(book.data)

            return score

        # Keep book with most complete metadata, fallback to newest if tie
        return max(books, key=lambda b: (metadata_score(b), _timestamp_or_default(b.timestamp, _AWARE_MIN)))

    elif strategy == 'largest_file_size':
        # Sum all format file sizes
        def total_file_size(book):
            if not book.data:
                return 0
            return sum(data.uncompressed_size for data in book.data if hasattr(data, 'uncompressed_size') and data.uncompressed_size)

        # Keep book with largest total file size, fallback to newest if tie
        return max(books, key=lambda b: (total_file_size(b), _timestamp_or_default(b.timestamp, _AWARE_MIN)))

    else:
        # Default fallback: keep newest
        return max(books, key=lambda b: _timestamp_or_default(b.timestamp, _AWARE_MIN))
