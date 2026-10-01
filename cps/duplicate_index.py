# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The duplicate-key index: per-book keys, group queries and cache merging, so scans don't re-read the whole library."""

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.orm import selectinload

from . import calibre_db, db, logger
from .duplicate_rules import (
    _AWARE_MIN,
    _timestamp_or_default,
    generate_group_hash,
    normalize_title_for_duplicates,
)
from .duplicate_detection import filter_dismissed_groups, get_common_filters

sys.path.insert(1, "/app/calibre-web-automated/scripts/")
from cwa_db import CWA_DB


log = logger.create()

NORMALIZATION_VERSION = "duplicate-index-v1"
MAX_INCREMENTAL_BOOK_IDS = 1000
DUPLICATE_INDEX_REBUILD_BATCH_SIZE = 250
INGEST_BATCH_DIRTY_FILE = "/config/cwa_ingest_batch_dirty"
INGEST_BATCH_ACTIVE_FILE = "/config/cwa_ingest_batch_active"


@dataclass(frozen=True)
class BookKeyParts:
    normalized_title: str
    normalized_author: str
    normalized_language: str
    normalized_series: str
    normalized_publisher: str
    format_signature: str

    def as_db_tuple(self):
        return (
            self.normalized_title,
            self.normalized_author,
            self.normalized_language,
            self.normalized_series,
            self.normalized_publisher,
            self.format_signature,
        )


def _hash_json(payload) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _setting_enabled(settings, key, default):
    return bool(int(settings.get(key, default) or 0))


def get_effective_duplicate_criteria(settings):
    criteria = {
        "title": _setting_enabled(settings, "duplicate_detection_title", 1),
        "author": _setting_enabled(settings, "duplicate_detection_author", 1),
        "language": _setting_enabled(settings, "duplicate_detection_language", 1),
        "series": _setting_enabled(settings, "duplicate_detection_series", 0),
        "publisher": _setting_enabled(settings, "duplicate_detection_publisher", 0),
        "format": _setting_enabled(settings, "duplicate_detection_format", 0),
    }
    if not any(criteria.values()):
        criteria["title"] = True
        criteria["author"] = True
    return criteria


def get_criteria_fingerprint(settings):
    return _hash_json(
        {
            "normalization_version": NORMALIZATION_VERSION,
            "criteria": get_effective_duplicate_criteria(settings),
        }
    )


def _primary_author(book):
    if not getattr(book, "authors", None):
        return "unknown"
    book.ordered_authors = calibre_db.order_authors([book])
    if book.ordered_authors and len(book.ordered_authors) > 0 and book.ordered_authors[0].name:
        return book.ordered_authors[0].name
    return "unknown"


def build_book_key_parts(book, settings):
    primary_author = _primary_author(book)
    title = book.title if getattr(book, "title", None) else "untitled"

    if getattr(book, "languages", None):
        language = book.languages[0].lang_code if book.languages[0].lang_code else "unknown"
    else:
        language = "unknown"

    if getattr(book, "series", None):
        series = book.series[0].name if book.series[0].name else "no_series"
    else:
        series = "no_series"

    if getattr(book, "publishers", None):
        publisher = book.publishers[0].name if book.publishers[0].name else "unknown_publisher"
    else:
        publisher = "unknown_publisher"

    if getattr(book, "data", None):
        formats = sorted([data.format.lower() for data in book.data if data.format])
        format_signature = ",".join(formats) if formats else "no_format"
    else:
        format_signature = "no_format"

    # Keep title normalization stable across criteria: even title-only keys strip a
    # leading primary-author prefix, unlike the old Python fallback's no-author mode.
    return BookKeyParts(
        normalized_title=normalize_title_for_duplicates(title, primary_author),
        normalized_author=primary_author.lower().strip() if primary_author else "unknown",
        normalized_language=language.lower().strip(),
        normalized_series=series.lower().strip(),
        normalized_publisher=publisher.lower().strip(),
        format_signature=format_signature,
    )


def _enabled_key_values(parts: BookKeyParts, settings):
    criteria = get_effective_duplicate_criteria(settings)
    values = []
    if criteria["title"]:
        values.append(("title", parts.normalized_title))
    if criteria["author"]:
        values.append(("author", parts.normalized_author))
    if criteria["language"]:
        values.append(("language", parts.normalized_language))
    if criteria["series"]:
        values.append(("series", parts.normalized_series))
    if criteria["publisher"]:
        values.append(("publisher", parts.normalized_publisher))
    if criteria["format"]:
        values.append(("format", parts.format_signature))
    return values


# Books per IN (...) query when loading many groups at once.
GROUP_LOAD_BATCH_SIZE = 500
# books.title is COLLATE NOCASE in Calibre's schema: ASCII-only case folding.
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _book_query(book_ids=None):
    # selectinload: one "WHERE book IN (...)" query per relationship. joinedload joined all
    # five collections into one row set per book (formats x authors x languages x ...).
    query = (
        calibre_db.session.query(db.Books)
        .options(selectinload(db.Books.data))
        .options(selectinload(db.Books.authors))
        .options(selectinload(db.Books.languages))
        .options(selectinload(db.Books.series))
        .options(selectinload(db.Books.publishers))
    )
    if book_ids is not None:
        query = query.filter(db.Books.id.in_(list(book_ids)))
    return query


def _load_books_by_ids(book_ids=None, user_id=None, user_filter=None):
    query = _book_query(book_ids)
    if user_filter is None and user_id is not None:
        user_filter = get_common_filters(user_id=user_id)
    if user_filter is not None:
        query = query.filter(user_filter)
    return query.order_by(db.Books.title, db.Books.timestamp.desc()).all()


def _load_books_for_groups(groups_book_ids, user_id=None):
    """Load the books of many groups with a few batched IN queries (not one query per group).

    Returns one list per group, in the order _load_books_by_ids would give: title, then
    newest first. The user's visibility filter (two app.db queries) is built once.
    """
    all_ids = sorted({book_id for book_ids in groups_book_ids for book_id in book_ids})
    user_filter = get_common_filters(user_id=user_id) if user_id is not None else None
    books_by_id = {}
    for batch_ids in _chunks(all_ids, GROUP_LOAD_BATCH_SIZE):
        for book in _load_books_by_ids(batch_ids, user_filter=user_filter):
            books_by_id[int(book.id)] = book
    result = []
    for book_ids in groups_book_ids:
        books = [books_by_id[book_id] for book_id in book_ids if book_id in books_by_id]
        books.sort(key=lambda book: _timestamp_or_default(book.timestamp, _AWARE_MIN), reverse=True)
        books.sort(key=lambda book: (book.title or "").translate(_ASCII_LOWER))
        result.append(books)
    return result


def _current_max_book_id():
    max_book_id = calibre_db.session.query(func.max(db.Books.id)).scalar()
    return int(max_book_id or 0)


def library_has_books():
    return _current_max_book_id() > 0


def _current_library_book_ids():
    book_ids = set()
    for row in calibre_db.session.query(db.Books.id).all():
        if hasattr(row, "id"):
            value = row.id
        else:
            try:
                value = row[0]
            except (TypeError, IndexError):
                value = row
        if value is not None:
            book_ids.add(int(value))
    return book_ids


def _chunks(values, size):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def upsert_book_keys(book_ids: Iterable[int], settings):
    book_ids = {int(book_id) for book_id in book_ids if book_id is not None}
    if len(book_ids) > MAX_INCREMENTAL_BOOK_IDS:
        raise ValueError(f"Incremental duplicate index update exceeds {MAX_INCREMENTAL_BOOK_IDS} books")
    if not book_ids:
        return {"updated": 0, "missing": 0, "missing_ids": [], "fingerprint": get_criteria_fingerprint(settings)}

    fingerprint = get_criteria_fingerprint(settings)
    books = _load_books_by_ids(book_ids)
    loaded_book_ids = {int(book.id) for book in books}
    cwa_db = CWA_DB()
    updated = 0
    for book in books:
        parts = build_book_key_parts(book, settings)
        duplicate_key = _hash_json(_enabled_key_values(parts, settings))
        cwa_db.cur.execute(
            """
            INSERT INTO cwa_duplicate_book_keys (
                book_id, normalized_title, normalized_author, normalized_language,
                normalized_series, normalized_publisher, format_signature,
                duplicate_key, criteria_fingerprint, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(book_id) DO UPDATE SET
                normalized_title = excluded.normalized_title,
                normalized_author = excluded.normalized_author,
                normalized_language = excluded.normalized_language,
                normalized_series = excluded.normalized_series,
                normalized_publisher = excluded.normalized_publisher,
                format_signature = excluded.format_signature,
                duplicate_key = excluded.duplicate_key,
                criteria_fingerprint = excluded.criteria_fingerprint,
                updated_at = CURRENT_TIMESTAMP
            """,
            (book.id, *parts.as_db_tuple(), duplicate_key, fingerprint),
        )
        updated += 1
    cwa_db.con.commit()
    missing_ids = sorted(book_ids - loaded_book_ids)
    return {"updated": updated, "missing": len(missing_ids), "missing_ids": missing_ids, "fingerprint": fingerprint}


def delete_book_keys(book_ids: Iterable[int]):
    book_ids = {int(book_id) for book_id in book_ids if book_id is not None}
    if not book_ids:
        return 0
    cwa_db = CWA_DB()
    placeholders = ",".join("?" for _ in book_ids)
    cwa_db.cur.execute(f"DELETE FROM cwa_duplicate_book_keys WHERE book_id IN ({placeholders})", tuple(book_ids))
    deleted = cwa_db.cur.rowcount
    cwa_db.con.commit()
    return deleted


def rebuild_duplicate_index(settings, progress_callback=None):
    fingerprint = get_criteria_fingerprint(settings)
    book_ids = sorted(_current_library_book_ids())
    total_books = len(book_ids)
    key_rows = []

    indexed_count = 0
    if progress_callback:
        progress_callback(indexed_count, total_books)
    for batch_ids in _chunks(book_ids, DUPLICATE_INDEX_REBUILD_BATCH_SIZE):
        books_by_id = {int(book.id): book for book in _load_books_by_ids(batch_ids)}
        for book_id in batch_ids:
            book = books_by_id.get(int(book_id))
            if book is None:
                continue
            parts = build_book_key_parts(book, settings)
            duplicate_key = _hash_json(_enabled_key_values(parts, settings))
            key_rows.append(
                (book.id, *parts.as_db_tuple(), duplicate_key, fingerprint)
            )
            indexed_count += 1
            if progress_callback and (indexed_count % 25 == 0 or indexed_count == total_books):
                progress_callback(indexed_count, total_books)

    cwa_db = CWA_DB()
    cwa_db.cur.execute("DELETE FROM cwa_duplicate_book_keys")
    cwa_db.cur.executemany(
        """
        INSERT OR REPLACE INTO cwa_duplicate_book_keys (
            book_id, normalized_title, normalized_author, normalized_language,
            normalized_series, normalized_publisher, format_signature,
            duplicate_key, criteria_fingerprint, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        key_rows,
    )
    cwa_db.con.commit()
    return {
        "max_book_id": max(book_ids, default=0),
        "indexed_count": indexed_count,
        "fingerprint": fingerprint,
    }


def _duplicate_key_rows(settings, candidate_book_ids=None):
    fingerprint = get_criteria_fingerprint(settings)
    cwa_db = CWA_DB()
    params = [fingerprint]
    where = "criteria_fingerprint = ?"
    if candidate_book_ids is not None:
        candidate_book_ids = {int(book_id) for book_id in candidate_book_ids if book_id is not None}
        if not candidate_book_ids:
            return []
        placeholders = ",".join("?" for _ in candidate_book_ids)
        where = (
            "criteria_fingerprint = ? AND duplicate_key IN ("
            "SELECT duplicate_key FROM cwa_duplicate_book_keys "
            f"WHERE criteria_fingerprint = ? AND book_id IN ({placeholders})"
            ")"
        )
        params = [fingerprint, fingerprint, *candidate_book_ids]
    cwa_db.cur.execute(
        f"""
        SELECT duplicate_key, GROUP_CONCAT(book_id), COUNT(*)
        FROM cwa_duplicate_book_keys
        WHERE {where}
        GROUP BY duplicate_key
        HAVING COUNT(*) > 1
        """,
        tuple(params),
    )
    return cwa_db.cur.fetchall()


def _indexed_group_book_ids_for_books(settings, book_ids):
    book_ids = {int(book_id) for book_id in book_ids if book_id is not None}
    if not book_ids:
        return set()

    fingerprint = get_criteria_fingerprint(settings)
    placeholders = ",".join("?" for _ in book_ids)
    cwa_db = CWA_DB()
    cwa_db.cur.execute(
        f"""
        SELECT GROUP_CONCAT(book_id)
        FROM cwa_duplicate_book_keys
        WHERE criteria_fingerprint = ?
          AND duplicate_key IN (
              SELECT duplicate_key
              FROM cwa_duplicate_book_keys
              WHERE criteria_fingerprint = ? AND book_id IN ({placeholders})
          )
        GROUP BY duplicate_key
        """,
        (fingerprint, fingerprint, *book_ids),
    )
    affected_ids = set()
    for (book_ids_str,) in cwa_db.cur.fetchall():
        if book_ids_str:
            affected_ids.update(int(book_id) for book_id in book_ids_str.split(",") if book_id)
    return affected_ids


def _decorate_books_for_group(books):
    for book in books:
        if not hasattr(book, "ordered_authors") or not book.ordered_authors:
            book.ordered_authors = calibre_db.order_authors([book])
        if book.ordered_authors and len(book.ordered_authors) > 0:
            book.author_names = ", ".join(
                [author.name.replace("|", ",") for author in book.ordered_authors if author.name]
            )
        else:
            book.author_names = "Unknown"
        book.cover_url = f"/cover/{book.id}" if getattr(book, "has_cover", None) else "/static/generic_cover.svg"


def _group_from_books(books):
    books.sort(key=lambda book: _timestamp_or_default(book.timestamp, _AWARE_MIN), reverse=True)
    _decorate_books_for_group(books)
    display_title = books[0].title if books[0].title else "Untitled"
    display_author = "Unknown"
    if hasattr(books[0], "author_names") and books[0].author_names:
        display_author = books[0].author_names.split(",")[0].strip()
    return {
        "title": display_title,
        "author": display_author,
        "count": len(books),
        "books": books,
        "group_hash": generate_group_hash(display_title, display_author),
    }


def get_duplicate_groups_from_index(settings, include_dismissed=False, user_id=None, candidate_book_ids=None):
    groups_book_ids = [
        [int(book_id) for book_id in book_ids_str.split(",") if book_id]
        for _duplicate_key, book_ids_str, _count in _duplicate_key_rows(settings, candidate_book_ids=candidate_book_ids)
    ]
    duplicate_groups = []
    for books in _load_books_for_groups(groups_book_ids, user_id=user_id):
        if len(books) < 2:
            continue
        duplicate_groups.append(_group_from_books(books))

    duplicate_groups.sort(key=lambda group: (group["title"].lower(), group["author"].lower()))
    if not include_dismissed:
        duplicate_groups = filter_dismissed_groups(duplicate_groups, user_id=user_id)
    return duplicate_groups


def _cached_group_book_ids(group):
    if "book_ids" in group:
        return {int(book_id) for book_id in group.get("book_ids", [])}
    return {int(book.id) for book in group.get("books", [])}


def _serialize_group_for_cache(group):
    if "book_ids" in group:
        book_ids = [int(book_id) for book_id in group.get("book_ids", [])]
    else:
        book_ids = [book.id for book in group.get("books", [])]
    return {
        "title": group.get("title", ""),
        "author": group.get("author", ""),
        "count": group.get("count", 0),
        "group_hash": group.get("group_hash", ""),
        "book_ids": book_ids,
    }


def _write_duplicate_cache_groups(cwa_db, duplicate_groups, max_book_id):
    serialized_groups = [_serialize_group_for_cache(group) for group in duplicate_groups]
    cwa_db.cur.execute(
        """
        UPDATE cwa_duplicate_cache
        SET scan_timestamp = ?,
            duplicate_groups_json = ?,
            total_count = ?,
            scan_pending = 0,
            last_scanned_book_id = ?
        WHERE id = 1
        """,
        (datetime.now().isoformat(), json.dumps(serialized_groups), len(serialized_groups), max_book_id),
    )
    cwa_db.con.commit()


def merge_affected_groups_into_cache(candidate_book_ids, settings):
    candidate_book_ids = {int(book_id) for book_id in candidate_book_ids if book_id is not None}
    if len(candidate_book_ids) > MAX_INCREMENTAL_BOOK_IDS:
        mark_duplicate_index_pending("incremental candidate set too large")
        return {"updated": False, "pending": True, "reason": "candidate set too large"}
    if not candidate_book_ids:
        return {"updated": False, "pending": False, "merged_count": 0}

    affected_ids = set(candidate_book_ids)
    affected_ids.update(_indexed_group_book_ids_for_books(settings, candidate_book_ids))

    upsert_result = upsert_book_keys(candidate_book_ids, settings)
    missing_ids = upsert_result.get("missing_ids", [])
    if missing_ids:
        delete_book_keys(missing_ids)
    affected_rows = _duplicate_key_rows(settings, candidate_book_ids=candidate_book_ids)
    for _duplicate_key, book_ids_str, _count in affected_rows:
        affected_ids.update(int(book_id) for book_id in book_ids_str.split(",") if book_id)

    cwa_db = CWA_DB()
    cache_data = cwa_db.get_duplicate_cache() or {}
    cached_groups = cache_data.get("duplicate_groups", []) or []
    retained_groups = [group for group in cached_groups if not (_cached_group_book_ids(group) & affected_ids)]
    fresh_groups = get_duplicate_groups_from_index(settings, include_dismissed=True, candidate_book_ids=affected_ids)
    merged_groups = retained_groups + fresh_groups
    merged_groups.sort(key=lambda group: (group["title"].lower(), group["author"].lower()))
    _write_duplicate_cache_groups(cwa_db, merged_groups, _current_max_book_id())
    return {"updated": True, "pending": False, "merged_count": len(merged_groups)}


def drop_books_from_duplicate_index(book_ids):
    """Remove deleted books from the duplicate index and the cached groups, without a rescan.

    Deletes their key rows, takes them out of every cached group, and drops groups left
    with fewer than two books. The rest of the cache (scan time, pending flag, last scanned
    id) is left as it is. Returns the number of cached groups removed.
    """
    book_ids = {int(book_id) for book_id in book_ids if book_id is not None}
    if not book_ids:
        return 0
    delete_book_keys(book_ids)
    cwa_db = CWA_DB()
    cache_data = cwa_db.get_duplicate_cache()
    if not cache_data:
        return 0
    cached_groups = cache_data.get("duplicate_groups", []) or []
    kept_groups = []
    changed = False
    for group in cached_groups:
        group = _serialize_group_for_cache(group)
        remaining = [book_id for book_id in group["book_ids"] if book_id not in book_ids]
        if len(remaining) != len(group["book_ids"]):
            changed = True
            if len(remaining) < 2:
                continue
            group.update(book_ids=remaining, count=len(remaining))
        kept_groups.append(group)
    if changed:
        cwa_db.cur.execute(
            "UPDATE cwa_duplicate_cache SET duplicate_groups_json = ?, total_count = ? WHERE id = 1",
            (json.dumps(kept_groups), len(kept_groups)),
        )
        cwa_db.con.commit()
    return len(cached_groups) - len(kept_groups)


def mark_duplicate_index_pending(reason=None):
    cwa_db = CWA_DB()
    cwa_db.cur.execute("UPDATE cwa_duplicate_cache SET scan_pending = 1 WHERE id = 1")
    cwa_db.con.commit()
    if reason:
        log.info("[cwa-duplicates] Duplicate index marked pending: %s", reason)
    return True


def has_valid_duplicate_index_baseline(settings, candidate_book_ids=None):
    cwa_db = CWA_DB()
    cache_data = cwa_db.get_duplicate_cache()

    candidate_ids = {int(book_id) for book_id in candidate_book_ids or [] if book_id is not None}
    library_book_ids = _current_library_book_ids()
    if not library_book_ids:
        return True

    # A fresh library can build its initial duplicate index incrementally when
    # the candidate set covers every current book.
    if not cache_data:
        return bool(candidate_ids) and library_book_ids.issubset(candidate_ids)

    if cache_data.get("scan_pending") and not candidate_ids:
        return False

    if library_book_ids and int(cache_data.get("last_scanned_book_id") or 0) <= 0:
        if candidate_ids and library_book_ids.issubset(candidate_ids):
            return True
        return False

    fingerprint = get_criteria_fingerprint(settings)
    cwa_db.cur.execute(
        "SELECT book_id FROM cwa_duplicate_book_keys WHERE criteria_fingerprint = ?",
        (fingerprint,),
    )
    indexed_book_ids = {int(row[0]) for row in cwa_db.cur.fetchall()}
    missing_book_ids = library_book_ids - indexed_book_ids
    if not missing_book_ids:
        return True

    return missing_book_ids.issubset(candidate_ids)


def ingest_batch_follow_up_pending():
    return (
        os.path.exists(INGEST_BATCH_ACTIVE_FILE)
        or os.path.exists(INGEST_BATCH_DIRTY_FILE)
        or os.path.exists(f"{INGEST_BATCH_DIRTY_FILE}.running")
    )


_CACHE_NOT_PROVIDED = object()

# In-process memo of the (expensive) missing-book classification, keyed on cheap
# aggregate fingerprints of the calibre library and the duplicate key index.
# Holds a single (key, classification) tuple; replaced atomically.
_MANUAL_SCAN_STATE_MEMO = None
_MISSING_NONE = "none"
_MISSING_EXISTING = "existing"
_MISSING_NEW_ONLY = "new_only"


def _scalar_or_none(query):
    try:
        return query.scalar()
    except Exception:
        return None


def _library_id_aggregates():
    """Cheap COUNT/MAX/SUM over Books.id used as a change fingerprint for the id set."""
    session = calibre_db.session
    count = _scalar_or_none(session.query(func.count(db.Books.id)))
    max_id = _scalar_or_none(session.query(func.max(db.Books.id)))
    sum_ids = _scalar_or_none(session.query(func.sum(db.Books.id)))
    return int(count or 0), int(max_id or 0), sum_ids


def _light_duplicate_cache_state(cwa_db):
    """Return {'scan_timestamp', 'last_scanned_book_id'} when a cache with groups exists,
    else None, without parsing duplicate_groups_json."""
    try:
        cwa_db.cur.execute(
            """
            SELECT scan_timestamp, last_scanned_book_id
            FROM cwa_duplicate_cache
            WHERE id = 1 AND duplicate_groups_json IS NOT NULL AND duplicate_groups_json != ''
            """
        )
        row = cwa_db.cur.fetchone()
    except Exception:
        return cwa_db.get_duplicate_cache()
    if not row:
        return None
    return {"scan_timestamp": row[0], "last_scanned_book_id": row[1]}


def _classify_missing_book_ids(cwa_db, fingerprint, last_scanned_book_id):
    library_book_ids = _current_library_book_ids()
    cwa_db.cur.execute(
        "SELECT book_id FROM cwa_duplicate_book_keys WHERE criteria_fingerprint = ?",
        (fingerprint,),
    )
    indexed_book_ids = {int(row[0]) for row in cwa_db.cur.fetchall()}
    missing_book_ids = library_book_ids - indexed_book_ids
    if not missing_book_ids:
        return _MISSING_NONE
    if any(book_id <= last_scanned_book_id for book_id in missing_book_ids):
        return _MISSING_EXISTING
    return _MISSING_NEW_ONLY


def duplicate_index_needs_manual_full_scan(settings, cwa_db=None, cache_data=_CACHE_NOT_PROVIDED):
    """Return True only when UI should ask for a manual full scan.

    During active imports, the debounced after-import scan is responsible for
    indexing books newer than the last full baseline. Do not turn that temporary
    lag into a manual full-scan requirement.

    ``cwa_db`` and an already-loaded ``cache_data`` (result of
    ``CWA_DB.get_duplicate_cache()``) may be passed to avoid opening another
    connection / re-parsing the cached groups JSON.
    """
    global _MANUAL_SCAN_STATE_MEMO
    owns_db = cwa_db is None
    if owns_db:
        cwa_db = CWA_DB()
    try:
        if cache_data is _CACHE_NOT_PROVIDED:
            cache_data = _light_duplicate_cache_state(cwa_db)
        if not cache_data:
            return library_has_books()

        library_count, library_max_id, library_id_sum = _library_id_aggregates()
        if library_count <= 0:
            return False

        last_scanned_book_id = int(cache_data.get("last_scanned_book_id") or 0)
        if last_scanned_book_id <= 0:
            return not ingest_batch_follow_up_pending()

        fingerprint = get_criteria_fingerprint(settings)
        cwa_db.cur.execute(
            "SELECT COUNT(*), MAX(book_id), SUM(book_id) FROM cwa_duplicate_book_keys "
            "WHERE criteria_fingerprint = ?",
            (fingerprint,),
        )
        index_aggregates = tuple(cwa_db.cur.fetchone() or ())

        memo_key = (
            fingerprint,
            last_scanned_book_id,
            cache_data.get("scan_timestamp"),
            library_count,
            library_max_id,
            library_id_sum,
            index_aggregates,
        )
        memo = _MANUAL_SCAN_STATE_MEMO
        if memo is not None and memo[0] == memo_key:
            classification = memo[1]
        else:
            classification = _classify_missing_book_ids(cwa_db, fingerprint, last_scanned_book_id)
            _MANUAL_SCAN_STATE_MEMO = (memo_key, classification)

        if classification == _MISSING_NONE:
            return False
        if classification == _MISSING_EXISTING:
            return True

        if ingest_batch_follow_up_pending():
            return False

        return True
    finally:
        if owns_db:
            close = getattr(cwa_db, "close", None)
            if callable(close):
                close()
