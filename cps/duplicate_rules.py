# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Pure rules for duplicate detection and resolution: timestamp normalisation, group
hashing, title normalisation and which book of a group to keep. No routes and no
database access beyond reading the format-priority setting, so cps/duplicate_index.py
and the scan task can use them without importing the duplicates blueprint."""

from datetime import datetime, UTC
import hashlib
import re
import sys
import unicodedata

from . import logger

sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from cwa_db import CWA_DB

log = logger.create()


def _normalize_timestamp(ts):
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _timestamp_or_default(ts, default):
    normalized = _normalize_timestamp(ts)
    return normalized if normalized is not None else default


_NON_WORD = re.compile(r"[\W_]+")
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}

_AWARE_MIN = datetime.min.replace(tzinfo=UTC)
_AWARE_MAX = datetime.max.replace(tzinfo=UTC)


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


def group_hash_for_books(book_ids):
    """A duplicate group's identity: its books. Dismissing a group hides exactly
    these books together; a new copy turning up makes a new group to review."""
    composite = ",".join(str(book_id) for book_id in sorted(int(book_id) for book_id in book_ids))
    return hashlib.md5(f"books:{composite}".encode()).hexdigest()


def fold_for_duplicates(text):
    """Lower-case `text` without accents, punctuation or repeated spaces.

    "Low-Dimensional  Topology" and "low dimensional topology" fold alike, as do
    "Erdős" and "Erdos".
    """
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(_NON_WORD.sub(" ", plain.casefold()).split())


def normalize_title_for_duplicates(title, primary_author=None):
    """Fold the title for comparison, dropping a leading author prefix ("Homer, The Iliad")."""
    normalized = fold_for_duplicates(title) or "untitled"
    author = fold_for_duplicates(primary_author)
    if author and normalized.startswith(author + " "):
        normalized = normalized[len(author) + 1:]
    return normalized


def normalize_author_for_duplicates(name):
    """Surname plus first initial, or "" for no author.

    "Ricardo Baptista", "R. Baptista" and "Baptista| Ricardo" (calibre keeps a
    comma in a name as "|") all give "baptista r", so one book entered with full
    names and once with initials still matches.
    """
    name = str(name or "")
    if "|" in name:
        surname, _, given = name.partition("|")
        surnames, givens = _without_suffix(fold_for_duplicates(surname).split()), fold_for_duplicates(given).split()
    else:
        words = _without_suffix(fold_for_duplicates(name).split())
        surnames, givens = words[-1:], words[:-1]
    if not surnames or surnames == ["unknown"]:
        return ""
    return f"{surnames[-1]} {givens[0][0]}" if givens else surnames[-1]


def _without_suffix(words):
    while len(words) > 1 and words[-1] in _NAME_SUFFIXES:
        words = words[:-1]
    return words


def validate_resolution_strategy(strategy):
    """Validate that strategy is one of the allowed values"""
    valid_strategies = ['newest', 'oldest', 'merge', 'highest_quality_format', 'most_metadata', 'largest_file_size']
    return strategy in valid_strategies


def select_book_to_keep(books, strategy):
    """
    Select which book to keep from a duplicate group based on strategy.

    Args:
        books: The books of one duplicate group
        strategy: One of 'newest', 'highest_quality_format', 'most_metadata', 'largest_file_size'

    Returns:
        The book object to keep
    """
    if not books:
        return None

    if strategy == 'newest':
        # Keep the most recently added book
        return max(books, key=lambda b: _timestamp_or_default(b.timestamp, _AWARE_MIN))

    if strategy == 'oldest':
        # Keep the earliest added book
        return min(books, key=lambda b: _timestamp_or_default(b.timestamp, _AWARE_MAX))

    if strategy == 'merge':
        # Merge into the newest book by default
        return max(books, key=lambda b: _timestamp_or_default(b.timestamp, _AWARE_MIN))

    if strategy == 'highest_quality_format':
        # Get format priority from settings
        try:
            import json
            cwa_db = CWA_DB()
            format_priority_json = cwa_db.cwa_settings.get('duplicate_format_priority', '{}')
            format_priority = json.loads(format_priority_json)
        except Exception as e:
            log.warning("[cwa-duplicates] Error loading format priority from settings, using defaults: %s", str(e))
            # Fallback to default priority. Mirrors the duplicate_format_priority
            # default in scripts/cwa_schema.sql; only the formats Lily supports
            # are listed, anything else scores 0.
            format_priority = {
                'EPUB': 100,
                'PDF': 60,
                'DJVU': 25,
            }

        def get_best_format_score(book):
            """Calculate best format score for a book"""
            if not book.data:
                return 0
            scores = [format_priority.get(data.format.upper(), 0) for data in book.data if data.format]
            return max(scores) if scores else 0

        # Keep book with highest quality format, fallback to newest if tie
        return max(books, key=lambda b: (get_best_format_score(b), _timestamp_or_default(b.timestamp, _AWARE_MIN)))

    if strategy == 'most_metadata':
        # Count metadata completeness
        def metadata_score(book):
            score = 0

            # Tags
            if hasattr(book, 'tags') and book.tags:
                score += len(book.tags) * 2

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

    if strategy == 'largest_file_size':
        # Sum all format file sizes
        def total_file_size(book):
            if not book.data:
                return 0
            return sum(data.uncompressed_size for data in book.data if hasattr(data, 'uncompressed_size') and data.uncompressed_size)

        # Keep book with largest total file size, fallback to newest if tie
        return max(books, key=lambda b: (total_file_size(b), _timestamp_or_default(b.timestamp, _AWARE_MIN)))

    # Default fallback: keep newest
    return max(books, key=lambda b: _timestamp_or_default(b.timestamp, _AWARE_MIN))
