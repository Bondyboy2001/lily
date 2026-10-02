# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Finding groups of duplicate books: the SQL and Python scans, dismissed-group filtering and
the library visibility filters they apply. The routes and auto-resolution that use these
live in duplicates.py, which re-exports the names below."""

import sys
import time

from sqlalchemy import func, and_, case
from sqlalchemy.sql.expression import true, false
from sqlalchemy.orm import joinedload

from . import db, calibre_db, logger, ub, config
from .cw_login import current_user
from .duplicate_rules import _AWARE_MIN, _timestamp_or_default, generate_group_hash, normalize_title_for_duplicates

sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from cwa_db import CWA_DB

log = logger.create()


def _no_author(name) -> bool:
    """calibre's "Unknown" stand-in (constants.UNKNOWN_AUTHOR): a book with no author shows none."""
    return (name or "").strip().lower() == "unknown"


def get_unresolved_duplicate_count(user_id=None):
    """Get count of unresolved duplicate groups for a user

    Args:
        user_id: User ID (defaults to current_user.id)

    Returns:
        Integer count of unresolved duplicate groups
    """
    if user_id is None:
        user_id = current_user.id

    try:
        # Get all duplicate groups
        duplicate_groups = find_duplicate_books(include_dismissed=False, user_id=user_id)
        return len(duplicate_groups)
    except Exception as e:
        log.error("[cwa-duplicates] Error counting unresolved duplicates: %s", str(e))
        return 0


def filter_dismissed_groups(duplicate_groups, user_id=None):
    """Filter dismissed duplicate groups for a given user."""
    if not duplicate_groups:
        return []
    if user_id is None:
        try:
            user_id = current_user.id
        except Exception:
            user_id = None
    if not user_id:
        return duplicate_groups

    try:
        dismissed_groups = ub.session.query(ub.DismissedDuplicateGroup.group_hash)\
            .filter(ub.DismissedDuplicateGroup.user_id == user_id)\
            .all()
        dismissed_hashes = {row[0] for row in dismissed_groups}
        if not dismissed_hashes:
            return duplicate_groups
        return [group for group in duplicate_groups if group.get('group_hash') not in dismissed_hashes]
    except Exception as e:
        log.error("[cwa-duplicates] Error filtering dismissed groups: %s", str(e))
        return duplicate_groups


def find_duplicate_books(include_dismissed=False, user_id=None):
    """Find books with duplicate combinations based on configurable criteria

    Args:
        include_dismissed: If False, filter out dismissed groups for the user
        user_id: User ID for dismissed filtering (defaults to current_user.id)

    Returns:
        List of duplicate group dictionaries
    """
    start_time = time.perf_counter()

    if user_id is None:
        try:
            if hasattr(current_user, 'id'):
                user_id = current_user.id
        except Exception:
            # current_user may be unavailable outside a request context
            user_id = None

    try:
        # Get CWA settings for duplicate detection
        cwa_db = CWA_DB()
        settings = cwa_db.cwa_settings

        # Check if duplicate detection is enabled
        detection_enabled = settings.get('duplicate_detection_enabled', 1)
        if not detection_enabled:
            print("[cwa-duplicates] Duplicate detection is disabled in settings", flush=True)
            return []

    except Exception as e:
        print(f"[cwa-duplicates] Error loading CWA settings: {str(e)}, falling back to defaults", flush=True)
        log.error("[cwa-duplicates] Error loading CWA settings: %s, falling back to defaults", str(e))
        # Fallback to safe defaults
        settings = {
            'duplicate_detection_enabled': 1,
            'duplicate_detection_title': 1,
            'duplicate_detection_author': 1,
            'duplicate_detection_language': 1,
            'duplicate_detection_series': 0,
            'duplicate_detection_publisher': 0,
            'duplicate_detection_format': 0,
            'duplicate_detection_use_sql': 1,
            'duplicate_scan_method': 'hybrid'
        }

    # Extract duplicate detection criteria
    use_title = settings.get('duplicate_detection_title', 1)
    use_author = settings.get('duplicate_detection_author', 1)
    use_language = settings.get('duplicate_detection_language', 1)
    use_series = settings.get('duplicate_detection_series', 0)
    use_publisher = settings.get('duplicate_detection_publisher', 0)
    use_format = settings.get('duplicate_detection_format', 0)

    # Check SQL method preference
    use_sql = settings.get('duplicate_detection_use_sql', 1)
    scan_method = settings.get('duplicate_scan_method', 'auto')

    # Ensure at least one criterion is selected (fallback to title+author if none selected)
    if not any([use_title, use_author, use_language, use_series, use_publisher, use_format]):
        print("[cwa-duplicates] Warning: No duplicate detection criteria selected, falling back to title+author", flush=True)
        log.warning("[cwa-duplicates] No duplicate detection criteria selected, falling back to title+author")
        use_title = 1
        use_author = 1

    # Determine which method to use
    method_to_use = 'python'  # Default fallback

    if scan_method == 'python':
        method_to_use = 'python'
    elif scan_method == 'sql':
        # SQL-only is available but still experimental
        method_to_use = 'sql' if not use_format else 'hybrid'
    elif scan_method == 'hybrid':
        method_to_use = 'hybrid'
    else:  # 'auto'
        if use_sql:
            # Prefer hybrid prefilter for safety unless SQL-only is explicitly chosen
            method_to_use = 'hybrid'
        else:
            method_to_use = 'python'

    print(f"[cwa-duplicates] Using detection method: {method_to_use}", flush=True)
    print(f"[cwa-duplicates] Using duplicate detection criteria: title={use_title}, author={use_author}, language={use_language}, series={use_series}, publisher={use_publisher}, format={use_format}", flush=True)

    # Call appropriate method
    if method_to_use == 'sql':
        duplicate_groups = find_duplicate_books_sql(
            use_title, use_author, use_language, use_series, use_publisher,
            include_dismissed, user_id
        )
    elif method_to_use == 'hybrid':
        # Use SQL as a prefilter to get candidate book IDs, then Python for robust grouping
        candidate_ids = find_duplicate_candidate_ids_sql(use_title, use_author, user_id=user_id)
        if candidate_ids is None:
            print("[cwa-duplicates] Hybrid prefilter unavailable, falling back to full Python scan", flush=True)
            duplicate_groups = find_duplicate_books_python(
                use_title, use_author, use_language, use_series, use_publisher, use_format,
                include_dismissed, user_id
            )
        elif not candidate_ids:
            duplicate_groups = []
        else:
            duplicate_groups = find_duplicate_books_python(
                use_title, use_author, use_language, use_series, use_publisher, use_format,
                include_dismissed, user_id, candidate_ids=candidate_ids
            )
        print("[cwa-duplicates] Hybrid prefilter applied (SQL candidates + Python validation)", flush=True)
    else:
        duplicate_groups = find_duplicate_books_python(
            use_title, use_author, use_language, use_series, use_publisher, use_format,
            include_dismissed, user_id
        )

    duration = time.perf_counter() - start_time
    print(f"[cwa-duplicates] Scan completed in {duration:.2f}s using {method_to_use} method", flush=True)
    log.info("[cwa-duplicates] Scan completed in %.2fs using %s method", duration, method_to_use)

    # Get max book ID for incremental scanning (from calibre database, not cwa.db)
    max_book_id = 0
    try:
        max_id_result = calibre_db.session.query(func.max(db.Books.id)).scalar()
        max_book_id = max_id_result if max_id_result is not None else 0
    except Exception as e:
        log.warning("[cwa-duplicates] Could not get max book ID: %s", str(e))

    # Update cache with performance metrics
    try:
        cwa_db_update = CWA_DB()
        cwa_db_update.cur.execute("""
            UPDATE cwa_duplicate_cache
            SET scan_duration_seconds = ?, scan_method_used = ?, last_scanned_book_id = ?
            WHERE id = 1
        """, (duration, method_to_use, max_book_id))
        cwa_db_update.con.commit()
    except Exception as e:
        log.warning("[cwa-duplicates] Failed to update performance metrics: %s", str(e))

    return duplicate_groups


def find_duplicate_candidate_ids_sql(use_title, use_author, user_id=None, min_book_id=None):
    """SQL-based candidate prefilter for hybrid mode.

    Returns a set of book IDs that are likely part of duplicate groups.
    Uses only title/author prefiltering to remain a safe superset.

    Args:
        use_title: Whether title criteria is enabled
        use_author: Whether author criteria is enabled

    Returns:
        set of int book IDs, empty set if none, or None if prefilter should be skipped
    """
    # If neither title nor author is enabled, prefilter is too risky -> skip
    if not use_title and not use_author:
        return None

    log.debug("[cwa-duplicates] Using SQL hybrid prefilter (candidate IDs)")

    # Note: these GROUP BY fields are evaluated by SQLite at query time; they are not cached
    # groupings in memory. We only use this query to prefilter candidate IDs.
    group_by_fields = []

    norm_title = None
    primary_author = None

    if use_author:
        norm_author_sort = func.lower(func.trim(func.coalesce(db.Books.author_sort, 'unknown')))
        primary_author = case(
            (func.instr(norm_author_sort, '&') > 0,
             func.substr(norm_author_sort, 1, func.instr(norm_author_sort, '&') - 1)),
            else_=norm_author_sort
        )
        group_by_fields.append(primary_author)

    if use_title:
        norm_title = func.lower(func.trim(func.coalesce(db.Books.title, 'untitled')))
        if primary_author is not None:
            author_prefix = primary_author + ', '
            norm_title = case(
                (norm_title.like(author_prefix + '%'),
                 func.trim(func.substr(norm_title, func.length(primary_author) + 3))),
                else_=norm_title
            )
        group_by_fields.append(norm_title)

    max_id_field = func.max(db.Books.id).label('max_book_id')

    query = (calibre_db.session.query(
                func.count(func.distinct(db.Books.id)).label('book_count'),
                func.group_concat(func.distinct(db.Books.id)).label('book_ids_str'),
                max_id_field
            )
            .select_from(db.Books)
            .filter(get_common_filters(user_id=user_id))
            .group_by(*group_by_fields)
            .having(func.count(func.distinct(db.Books.id)) > 1))

    if min_book_id is not None:
        query = query.having(max_id_field >= int(min_book_id))

    try:
        log.debug("[cwa-duplicates] Executing SQL prefilter query (min_book_id=%s)...", min_book_id)
        results = query.all()
        log.debug("[cwa-duplicates] SQL prefilter query completed, got %s result rows", len(results))
    except Exception as e:
        log.error("[cwa-duplicates] Hybrid prefilter SQL failed: %s", str(e))
        return None

    candidate_ids = set()
    for row in results:
        if not row.book_ids_str:
            continue
        candidate_ids.update(int(bid) for bid in row.book_ids_str.split(',') if bid)

    log.debug("[cwa-duplicates] Hybrid prefilter returned %s candidate books", len(candidate_ids))
    return candidate_ids


def find_duplicate_books_sql(use_title, use_author, use_language, use_series, use_publisher,
                              include_dismissed=False, user_id=None):
    """SQL-based duplicate detection using GROUP BY - experimental/WIP

    NOTE: This is experimental code, disabled by default. Needs refinement:
    - Multi-author books create duplicate rows (handled by DISTINCT but not ideal)
    - Relies on COALESCE for NULL handling which may not match Python behavior exactly
    - Not thoroughly tested across all criteria combinations

    Use Python method (default) for production until this is properly tested.

    Args:
        use_title, use_author, use_language, use_series, use_publisher: Boolean flags for criteria
        include_dismissed: If False, filter out dismissed groups
        user_id: User ID for dismissed filtering

    Returns:
        List of duplicate group dictionaries
    """
    print("[cwa-duplicates] Using SQL-based duplicate detection", flush=True)

    # Build dynamic GROUP BY clause based on criteria
    group_by_fields = []
    select_fields = []

    if use_title:
        group_by_fields.append(func.lower(db.Books.title))
        select_fields.append(func.lower(db.Books.title).label('norm_title'))

    if use_author:
        group_by_fields.append(func.lower(db.Authors.name))
        select_fields.append(func.lower(db.Authors.name).label('norm_author'))

    if use_language:
        # Use COALESCE to handle NULL languages (books without language)
        group_by_fields.append(func.coalesce(func.lower(db.Languages.lang_code), 'unknown'))
        select_fields.append(func.coalesce(func.lower(db.Languages.lang_code), 'unknown').label('norm_language'))

    if use_series:
        # Use COALESCE to handle NULL series (books without series)
        group_by_fields.append(func.coalesce(func.lower(db.Series.name), 'no_series'))
        select_fields.append(func.coalesce(func.lower(db.Series.name), 'no_series').label('norm_series'))

    if use_publisher:
        # Use COALESCE to handle NULL publishers (books without publisher)
        group_by_fields.append(func.coalesce(func.lower(db.Publishers.name), 'unknown_publisher'))
        select_fields.append(func.coalesce(func.lower(db.Publishers.name), 'unknown_publisher').label('norm_publisher'))

    # Add count and aggregated book IDs
    # Use DISTINCT because LEFT JOINs can create duplicate rows for books with multiple languages/series/publishers
    select_fields.extend([
        func.count(func.distinct(db.Books.id)).label('book_count'),
        func.group_concat(func.distinct(db.Books.id)).label('book_ids_str')
    ])

    # Build query starting from Books table with explicit joins
    query = calibre_db.session.query(*select_fields).select_from(db.Books)

    # Join required tables based on criteria
    # Note: Books with multiple authors/languages/etc will create multiple rows - handled by DISTINCT in count
    if use_author:
        # Join to get author (books_authors_link has no ordering column, Python uses first from relationship)
        query = query.join(db.books_authors_link, db.Books.id == db.books_authors_link.c.book)\
                     .join(db.Authors, db.books_authors_link.c.author == db.Authors.id)

    if use_language:
        # LEFT JOIN to include books without languages (handled by COALESCE)
        query = query.outerjoin(db.books_languages_link, db.Books.id == db.books_languages_link.c.book)\
                     .outerjoin(db.Languages, db.books_languages_link.c.lang_code == db.Languages.id)

    if use_series:
        # LEFT JOIN to include books without series (handled by COALESCE)
        query = query.outerjoin(db.books_series_link, db.Books.id == db.books_series_link.c.book)\
                     .outerjoin(db.Series, db.books_series_link.c.series == db.Series.id)

    if use_publisher:
        # LEFT JOIN to include books without publishers (handled by COALESCE)
        query = query.outerjoin(db.books_publishers_link, db.Books.id == db.books_publishers_link.c.book)\
                     .outerjoin(db.Publishers, db.books_publishers_link.c.publisher == db.Publishers.id)

    # Apply common filters for user permissions
    query = query.filter(get_common_filters(user_id=user_id))

    # Group by selected criteria
    query = query.group_by(*group_by_fields)

    # Only get groups with 2+ books (duplicates)
    query = query.having(func.count(func.distinct(db.Books.id)) > 1)

    # Execute query
    try:
        results = query.all()
        print(f"[cwa-duplicates] SQL query returned {len(results)} duplicate groups", flush=True)
    except Exception as e:
        log.error("[cwa-duplicates] SQL query failed: %s, falling back to Python method", str(e))
        print(f"[cwa-duplicates] SQL query failed: {str(e)}, falling back to Python method", flush=True)
        return find_duplicate_books_python(
            use_title, use_author, use_language, use_series, use_publisher, False,
            include_dismissed, user_id
        )

    # Process results into duplicate groups
    duplicate_groups = []

    for result in results:
        # Parse book IDs from group_concat result
        book_ids_str = result.book_ids_str
        book_ids = [int(bid) for bid in book_ids_str.split(',')]

        # Load full book objects for these IDs with eager loading
        books = (calibre_db.session.query(db.Books)
                .options(joinedload(db.Books.data))
                .options(joinedload(db.Books.authors))
                .filter(db.Books.id.in_(book_ids))
                .filter(get_common_filters(user_id=user_id))
                .order_by(db.Books.timestamp.desc())
                .all())

        if len(books) < 2:
            continue  # Safety check

        # Prepare display data
        for book in books:
            # Ensure we have ordered authors
            if not hasattr(book, 'ordered_authors') or not book.ordered_authors:
                book.ordered_authors = calibre_db.order_authors([book])

            book.author_names = ', '.join(author.name.replace('|', ',') for author in book.ordered_authors or []
                                          if author.name and not _no_author(author.name))

            # Add cover URL
            if hasattr(book, 'has_cover') and book.has_cover:
                book.cover_url = f"/cover/{book.id}"
            else:
                book.cover_url = "/static/generic_cover.svg"

        # Get safe title and author for display
        display_title = books[0].title if books[0].title else 'Untitled'
        display_author = 'Unknown'
        if hasattr(books[0], 'author_names') and books[0].author_names:
            display_author = books[0].author_names.split(',')[0].strip()

        # Generate group hash for dismiss tracking
        group_hash = generate_group_hash(display_title, display_author)

        duplicate_groups.append({
            'title': display_title,
            'author': '' if _no_author(display_author) else display_author,
            'count': len(books),
            'books': books,
            'group_hash': group_hash
        })

        print(f"[cwa-duplicates] Found duplicate group: '{display_title}' by {display_author} ({len(books)} copies) - IDs: {book_ids}", flush=True)
        log.info("[cwa-duplicates] Found duplicate group: '%s' by %s (%s copies) - IDs: %s",
                display_title, display_author, len(books), book_ids)

    # Filter out dismissed groups if requested
    if not include_dismissed and user_id:
        try:
            dismissed_hashes = set()
            dismissed_groups = ub.session.query(ub.DismissedDuplicateGroup.group_hash)\
                .filter(ub.DismissedDuplicateGroup.user_id == user_id)\
                .all()
            dismissed_hashes = {row[0] for row in dismissed_groups}

            if dismissed_hashes:
                original_count = len(duplicate_groups)
                duplicate_groups = [group for group in duplicate_groups
                                  if group['group_hash'] not in dismissed_hashes]
                filtered_count = original_count - len(duplicate_groups)
                if filtered_count > 0:
                    print(f"[cwa-duplicates] Filtered out {filtered_count} dismissed groups for user {user_id}", flush=True)
                    log.info("[cwa-duplicates] Filtered out %s dismissed groups for user %s",
                            filtered_count, user_id)
        except Exception as e:
            log.error("[cwa-duplicates] Error filtering dismissed groups: %s", str(e))

    # Sort by title, then author for consistent display
    duplicate_groups.sort(key=lambda x: (x['title'].lower(), x['author'].lower()))

    print(f"[cwa-duplicates] Found {len(duplicate_groups)} duplicate groups total", flush=True)

    return duplicate_groups


def find_duplicate_books_python(use_title, use_author, use_language, use_series, use_publisher, use_format,
                                 include_dismissed=False, user_id=None, candidate_ids=None):
    """Original Python-based duplicate detection - fallback for complex scenarios

    Args:
        use_title, use_author, use_language, use_series, use_publisher, use_format: Boolean flags
        include_dismissed: If False, filter out dismissed groups
        user_id: User ID for dismissed filtering

    Returns:
        List of duplicate group dictionaries
    """
    print("[cwa-duplicates] Using Python-based duplicate detection", flush=True)

    # Get all books with proper user filtering - this is much simpler and more reliable
    # than trying to do complex joins for duplicate detection
    books_query = (calibre_db.session.query(db.Books)
                   .filter(get_common_filters(user_id=user_id))  # Respect user permissions and library filtering
                   .order_by(db.Books.title, db.Books.timestamp.desc()))

    if candidate_ids is not None:
        if not candidate_ids:
            print("[cwa-duplicates] No candidate IDs provided, returning empty duplicate set", flush=True)
            return []
        books_query = books_query.filter(db.Books.id.in_(list(candidate_ids)))

    all_books = books_query.all()
    print(f"[cwa-duplicates] Retrieved {len(all_books)} books with user filtering applied", flush=True)

    # Safety check for very large libraries (optional performance warning)
    if len(all_books) > 50000:
        print(f"[cwa-duplicates] Warning: Processing {len(all_books)} books may take some time", flush=True)
        log.warning("[cwa-duplicates] Processing large library: %s books", len(all_books))

    # Group books by configurable criteria combination (case-insensitive)
    grouped_books = {}

    for book in all_books:
        # Build key based on selected criteria
        key_parts = []

        primary_author = None
        if use_author:
            # Ensure authors are loaded and not empty
            if book.authors and len(book.authors) > 0:
                # Get primary author (use Calibre-Web's standard approach)
                book.ordered_authors = calibre_db.order_authors([book])
                primary_author = book.ordered_authors[0].name if book.ordered_authors and len(book.ordered_authors) > 0 else "unknown"
            else:
                primary_author = "unknown"

        if use_title:
            # Handle potential None title
            title = book.title if book.title else "untitled"
            key_parts.append(normalize_title_for_duplicates(title, primary_author))

        if use_author:
            key_parts.append(primary_author.lower().strip() if primary_author else "unknown")

        if use_language:
            # Get primary language code
            if book.languages and len(book.languages) > 0:
                primary_language = book.languages[0].lang_code if book.languages[0].lang_code else "unknown"
                key_parts.append(primary_language.lower().strip())
            else:
                key_parts.append("unknown")

        if use_series:
            # Get series name
            if book.series and len(book.series) > 0:
                series_name = book.series[0].name if book.series[0].name else "no_series"
                key_parts.append(series_name.lower().strip())
            else:
                key_parts.append("no_series")

        if use_publisher:
            # Get publisher name
            if book.publishers and len(book.publishers) > 0:
                publisher_name = book.publishers[0].name if book.publishers[0].name else "unknown_publisher"
                key_parts.append(publisher_name.lower().strip())
            else:
                key_parts.append("unknown_publisher")

        if use_format:
            # Get file formats (consider books with same formats as potentially duplicate)
            if book.data and len(book.data) > 0:
                formats = sorted([data.format.lower() for data in book.data if data.format])
                format_str = ",".join(formats) if formats else "no_format"
                key_parts.append(format_str)
            else:
                key_parts.append("no_format")

        # Create composite key
        key = tuple(key_parts)

        if key not in grouped_books:
            grouped_books[key] = []
        grouped_books[key].append(book)

    print(f"[cwa-duplicates] Grouped books into {len(grouped_books)} unique combinations based on selected criteria", flush=True)

    # Filter to only groups with duplicates and prepare display data
    duplicate_groups = []
    for key, books in grouped_books.items():
        if len(books) > 1:
            # Sort books by timestamp (newest first)
            books.sort(key=lambda x: _timestamp_or_default(x.timestamp, _AWARE_MIN), reverse=True)

            # Add additional information for display
            for book in books:
                # Ensure we have ordered authors
                if not hasattr(book, 'ordered_authors') or not book.ordered_authors:
                    book.ordered_authors = calibre_db.order_authors([book])

                book.author_names = ', '.join(author.name.replace('|', ',') for author in book.ordered_authors or []
                                              if author.name and not _no_author(author.name))

                # Add cover URL
                if hasattr(book, 'has_cover') and book.has_cover:
                    book.cover_url = f"/cover/{book.id}"
                else:
                    book.cover_url = "/static/generic_cover.svg"

            # Get safe title and author for display
            display_title = books[0].title if books[0].title else 'Untitled'
            display_author = 'Unknown'
            if hasattr(books[0], 'author_names') and books[0].author_names:
                display_author = books[0].author_names.split(',')[0].strip()

            # Generate group hash for dismiss tracking
            group_hash = generate_group_hash(display_title, display_author)

            duplicate_groups.append({
                'title': display_title,
                'author': '' if _no_author(display_author) else display_author,
                'count': len(books),
                'books': books,
                'group_hash': group_hash
            })

            book_ids = [book.id for book in books]
            print(f"[cwa-duplicates] Found duplicate group: '{display_title}' by {display_author} ({len(books)} copies) - IDs: {book_ids}", flush=True)
            log.info("[cwa-duplicates] Found duplicate group: '%s' by %s (%s copies) - IDs: %s",
                    display_title, display_author, len(books), book_ids)

    # Filter out dismissed groups if requested
    if not include_dismissed and user_id:
        try:
            dismissed_hashes = set()
            dismissed_groups = ub.session.query(ub.DismissedDuplicateGroup.group_hash)\
                .filter(ub.DismissedDuplicateGroup.user_id == user_id)\
                .all()
            dismissed_hashes = {row[0] for row in dismissed_groups}

            if dismissed_hashes:
                original_count = len(duplicate_groups)
                duplicate_groups = [group for group in duplicate_groups
                                  if group['group_hash'] not in dismissed_hashes]
                filtered_count = original_count - len(duplicate_groups)
                if filtered_count > 0:
                    print(f"[cwa-duplicates] Filtered out {filtered_count} dismissed groups for user {user_id}", flush=True)
                    log.info("[cwa-duplicates] Filtered out %s dismissed groups for user %s",
                            filtered_count, user_id)
        except Exception as e:
            log.error("[cwa-duplicates] Error filtering dismissed groups: %s", str(e))

    # Sort by title, then author for consistent display
    duplicate_groups.sort(key=lambda x: (x['title'].lower(), x['author'].lower()))

    print(f"[cwa-duplicates] Found {len(duplicate_groups)} duplicate groups total", flush=True)

    return duplicate_groups


def get_common_filters(user_id=None, return_all_languages=False):
    """Build common filters using either current_user or a specific user_id.

    Falls back to no-op filters if user context is unavailable.
    """
    try:
        if user_id is None:
            return calibre_db.common_filters(return_all_languages=return_all_languages)
    except Exception:
        # No request context; fall back to permissive filter
        return true()

    try:
        user = ub.session.query(ub.User).filter(ub.User.id == int(user_id)).first()
        if not user:
            return true()

        if user.filter_language() == "all" or return_all_languages:
            lang_filter = true()
        else:
            lang_filter = db.Books.languages.any(db.Languages.lang_code == user.filter_language())

        negtags_list = user.list_denied_tags()
        postags_list = user.list_allowed_tags()
        neg_content_tags_filter = false() if negtags_list == [''] else db.Books.tags.any(db.Tags.name.in_(negtags_list))
        pos_content_tags_filter = true() if postags_list == [''] else db.Books.tags.any(db.Tags.name.in_(postags_list))

        if config.config_restricted_column:
            try:
                pos_cc_list = (user.allowed_column_value or '').split(',')
                pos_content_cc_filter = true() if pos_cc_list == [''] else \
                    getattr(db.Books, 'custom_column_' + str(config.config_restricted_column)). \
                    any(db.cc_classes[config.config_restricted_column].value.in_(pos_cc_list))
                neg_cc_list = (user.denied_column_value or '').split(',')
                neg_content_cc_filter = false() if neg_cc_list == [''] else \
                    getattr(db.Books, 'custom_column_' + str(config.config_restricted_column)). \
                    any(db.cc_classes[config.config_restricted_column].value.in_(neg_cc_list))
            except Exception:
                pos_content_cc_filter = false()
                neg_content_cc_filter = true()
        else:
            pos_content_cc_filter = true()
            neg_content_cc_filter = false()

        return and_(lang_filter, pos_content_tags_filter, ~neg_content_tags_filter,
                    pos_content_cc_filter, ~neg_content_cc_filter)
    except Exception:
        return true()
