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
from sqlalchemy.orm import lazyload, selectinload

from . import calibre_db, config, db, logger
from .duplicate_rules import (
    _AWARE_MIN,
    _timestamp_or_default,
    generate_group_hash,
    group_hash_for_books,
    normalize_author_for_duplicates,
    normalize_title_for_duplicates,
)
from .duplicate_detection import filter_dismissed_groups, get_common_filters

sys.path.insert(1, "/app/calibre-web-automated/scripts/")
from cwa_db import CWA_DB


log = logger.create()

# Bumping this makes existing indexes rescan once. v2 added the same-file pass;
# v3 folds titles and authors, stops matching books with no author by metadata
# alone and matches files that differ only in embedded metadata.
NORMALIZATION_VERSION = "duplicate-index-v3"
MAX_INCREMENTAL_BOOK_IDS = 1000
DUPLICATE_INDEX_REBUILD_BATCH_SIZE = 500
# Book ids per IN (...) query, well under SQLite's bound-parameter limit
LOAD_CHUNK_SIZE = 500
INGEST_BATCH_DIRTY_FILE = "/config/cwa_ingest_batch_dirty"
INGEST_BATCH_ACTIVE_FILE = "/config/cwa_ingest_batch_active"
FILE_BLOCK_SIZE = 1024
# Share of equal blocks at which two same-size files count as one document
SAME_DOCUMENT_BLOCK_SHARE = 0.9


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
        normalized_author=normalize_author_for_duplicates(primary_author),
        normalized_language=language.lower().strip(),
        normalized_series=series.lower().strip(),
        normalized_publisher=publisher.lower().strip(),
        format_signature=format_signature,
    )


def _enabled_key_values(parts: BookKeyParts, settings):
    """The metadata a book is matched on. Language is left out: books with no
    language match books with one, so groups are split by language afterwards
    (see _split_by_language)."""
    criteria = get_effective_duplicate_criteria(settings)
    values = []
    if criteria["title"]:
        values.append(("title", parts.normalized_title))
    if criteria["author"]:
        values.append(("author", parts.normalized_author))
    if criteria["series"]:
        values.append(("series", parts.normalized_series))
    if criteria["publisher"]:
        values.append(("publisher", parts.normalized_publisher))
    if criteria["format"]:
        values.append(("format", parts.format_signature))
    return values


def _duplicate_key(book_id, parts: BookKeyParts, settings):
    if not parts.normalized_author:
        # A book with no author is matched only by an identical file: on real
        # libraries "Untitled-1", "DjVu Document" or "Front Matter" are titles
        # that scanned PDFs carry, not signs of the same book.
        return _hash_json([("book", int(book_id))])
    return _hash_json(_enabled_key_values(parts, settings))


def _book_query(book_ids, keys_only=False):
    # selectinload, not joinedload: SQLite answers the nested link-table joins of a
    # joinedload by copying each whole link table, ~20 ms a query on a 17k library.
    query = (
        calibre_db.session.query(db.Books)
        .options(selectinload(db.Books.data))
        .options(selectinload(db.Books.authors))
        .options(selectinload(db.Books.languages))
        .options(selectinload(db.Books.series))
        .options(selectinload(db.Books.publishers))
        .filter(db.Books.id.in_(list(book_ids)))
    )
    if keys_only:
        # Keys never read these; loading them was half of a full rebuild. Lazy, not
        # empty, so a later keep-strategy in this session still sees the real values.
        query = query.options(lazyload(db.Books.tags), lazyload(db.Books.comments),
                              lazyload(db.Books.ratings), lazyload(db.Books.identifiers))
    return query


def _load_books_by_ids(book_ids, user_id=None, keys_only=False):
    """The books with these ids (those `user_id` may see, when given), in chunks."""
    visibility = get_common_filters(user_id=user_id) if user_id is not None else None
    books = []
    for chunk in _chunks(sorted({int(book_id) for book_id in book_ids}), LOAD_CHUNK_SIZE):
        query = _book_query(chunk, keys_only=keys_only)
        if visibility is not None:
            query = query.filter(visibility)
        books.extend(query.all())
    return books


def _visible_book_ids(book_ids, user_id):
    """The subset of `book_ids` that `user_id` may see, without loading the books."""
    visibility = get_common_filters(user_id=user_id)
    visible = set()
    for chunk in _chunks(sorted({int(book_id) for book_id in book_ids}), LOAD_CHUNK_SIZE):
        rows = calibre_db.session.query(db.Books.id).filter(db.Books.id.in_(chunk)).filter(visibility).all()
        visible.update(int(row[0]) for row in rows)
    return visible


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

def _book_files(book):
    """(book_id, FORMAT, size, path) for each stored file of `book`, as calibre records it."""
    files = []
    for data in getattr(book, "data", None) or []:
        size = int(getattr(data, "uncompressed_size", 0) or 0)
        if not data.format or size <= 0:
            continue
        path = os.path.join(config.get_book_path(), book.path, f"{data.name}.{data.format.lower()}")
        files.append((int(book.id), data.format.upper(), size, path))
    return files


def _same_size_files(book_files):
    """The library's other files sharing a format and exact byte size with one of `book_files`."""
    wanted = {(fmt, size) for _book_id, fmt, size, _path in book_files}
    own_ids = {book_id for book_id, _fmt, _size, _path in book_files}
    if not wanted:
        return []
    matches = []
    for fmt, size in wanted:
        rows = (
            calibre_db.session.query(db.Data.book)
            .filter(db.Data.format == fmt, db.Data.uncompressed_size == size)
            .all()
        )
        other_ids = {int(row[0]) for row in rows} - own_ids
        if other_ids:
            for book in _load_books_by_ids(other_ids, keys_only=True):
                matches.extend(file for file in _book_files(book) if (file[1], file[2]) == (fmt, size))
    return matches


def _file_signature(path):
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def _block_digests(path):
    """An 8-byte digest per FILE_BLOCK_SIZE block of the file, concatenated."""
    digests = bytearray()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(FILE_BLOCK_SIZE), b""):
            digests += hashlib.blake2b(block, digest_size=8).digest()
    return bytes(digests)


def _same_document(digests_a, digests_b):
    """Two equal-size files hold the same document when nearly all their blocks match.

    Copies of one PDF that only got different embedded metadata differ in a block or
    two (the header, the trailer ID); different documents that happen to share a
    byte count differ in almost every block.
    """
    if len(digests_a) != len(digests_b) or not digests_a:
        return False
    if digests_a == digests_b:
        return True
    blocks = len(digests_a) // 8
    equal = sum(1 for offset in range(0, len(digests_a), 8)
                if digests_a[offset:offset + 8] == digests_b[offset:offset + 8])
    return equal >= SAME_DOCUMENT_BLOCK_SHARE * blocks


def _known_file_matches(cwa_db):
    cwa_db.cur.execute("SELECT book_id, format, file_size, file_mtime_ns, match_key FROM cwa_duplicate_file_matches")
    return {(int(book_id), fmt): (size, mtime_ns, match_key)
            for book_id, fmt, size, mtime_ns, match_key in cwa_db.cur.fetchall()}


def _file_match_rows(book_files, known_matches):
    """Rows (book_id, format, size, mtime_ns, match_key) for files that hold the
    same document as another book's file of the same format and byte size.

    Only same-size files are read. A size bucket whose files are all unchanged
    since the last check keeps its stored keys without being read again.
    """
    buckets = {}
    for book_id, fmt, size, path in book_files:
        buckets.setdefault((fmt, size), {})[book_id] = path
    rows = []
    for (fmt, size), paths in buckets.items():
        if len(paths) < 2:
            continue
        signatures = {book_id: _file_signature(path) for book_id, path in paths.items()}
        signatures = {book_id: signature for book_id, signature in signatures.items() if signature}
        known = {book_id: known_matches.get((book_id, fmt)) for book_id in signatures}
        if all(entry and tuple(entry[:2]) == signatures[book_id] for book_id, entry in known.items()):
            rows.extend((book_id, fmt, *signatures[book_id], known[book_id][2]) for book_id in signatures)
            continue
        digests = {}
        for book_id in sorted(signatures):
            try:
                digests[book_id] = _block_digests(paths[book_id])
            except OSError as ex:
                log.warning("[cwa-duplicates] Could not read %s for the same-file check: %s", paths[book_id], ex)
        ordered = sorted(digests)
        matched = [{a, b} for index, a in enumerate(ordered) for b in ordered[index + 1:]
                   if _same_document(digests[a], digests[b])]
        for members in _union(matched):
            match_key = f"{fmt}:{size}:{min(members)}"
            rows.extend((book_id, fmt, *signatures[book_id], match_key) for book_id in members)
    return rows


def _write_file_match_rows(cwa_db, rows):
    cwa_db.cur.executemany(
        """
        INSERT OR REPLACE INTO cwa_duplicate_file_matches (book_id, format, file_size, file_mtime_ns, match_key)
        VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )


def _upsert_file_matches(cwa_db, books):
    """Re-check the files of `books` against same-size files elsewhere in the library."""
    book_files = [file for book in books for file in _book_files(book)]
    book_ids = {int(book.id) for book in books}
    # Copies can match each other inside one import batch, so the batch's own files count too
    others = _same_size_files(book_files)
    rows = _file_match_rows(book_files + others, _known_file_matches(cwa_db))
    if book_ids:
        placeholders = ",".join("?" for _ in book_ids)
        cwa_db.cur.execute(f"DELETE FROM cwa_duplicate_file_matches WHERE book_id IN ({placeholders})",
                           tuple(book_ids))
    # The other books' size buckets were re-checked too; their other formats were not
    cwa_db.cur.executemany("DELETE FROM cwa_duplicate_file_matches WHERE book_id = ? AND format = ?",
                           [(book_id, fmt) for book_id, fmt, _size, _path in others])
    _write_file_match_rows(cwa_db, rows)


def upsert_book_keys(book_ids: Iterable[int], settings):
    book_ids = {int(book_id) for book_id in book_ids if book_id is not None}
    if len(book_ids) > MAX_INCREMENTAL_BOOK_IDS:
        raise ValueError(f"Incremental duplicate index update exceeds {MAX_INCREMENTAL_BOOK_IDS} books")
    if not book_ids:
        return {"updated": 0, "missing": 0, "missing_ids": [], "fingerprint": get_criteria_fingerprint(settings)}

    fingerprint = get_criteria_fingerprint(settings)
    books = _load_books_by_ids(book_ids, keys_only=True)
    loaded_book_ids = {int(book.id) for book in books}
    cwa_db = CWA_DB()
    updated = 0
    for book in books:
        parts = build_book_key_parts(book, settings)
        duplicate_key = _duplicate_key(book.id, parts, settings)
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
    _upsert_file_matches(cwa_db, books)
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
    cwa_db.cur.execute(f"DELETE FROM cwa_duplicate_file_matches WHERE book_id IN ({placeholders})", tuple(book_ids))
    cwa_db.con.commit()
    return deleted


def rebuild_duplicate_index(settings, progress_callback=None):
    fingerprint = get_criteria_fingerprint(settings)
    book_ids = sorted(_current_library_book_ids())
    total_books = len(book_ids)
    key_rows = []
    book_files = []

    indexed_count = 0
    if progress_callback:
        progress_callback(indexed_count, total_books)
    for batch_ids in _chunks(book_ids, DUPLICATE_INDEX_REBUILD_BATCH_SIZE):
        books_by_id = {int(book.id): book for book in _load_books_by_ids(batch_ids, keys_only=True)}
        for book_id in batch_ids:
            book = books_by_id.get(int(book_id))
            if book is None:
                continue
            parts = build_book_key_parts(book, settings)
            duplicate_key = _duplicate_key(book.id, parts, settings)
            key_rows.append(
                (book.id, *parts.as_db_tuple(), duplicate_key, fingerprint)
            )
            book_files.extend(_book_files(book))
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
    file_match_rows = _file_match_rows(book_files, _known_file_matches(cwa_db))
    cwa_db.cur.execute("DELETE FROM cwa_duplicate_file_matches")
    _write_file_match_rows(cwa_db, file_match_rows)
    # Books deleted while this rebuild ran would otherwise come back as keys
    gone = set(book_ids) - _current_library_book_ids()
    if gone:
        placeholders = ",".join("?" for _ in gone)
        for table in ("cwa_duplicate_book_keys", "cwa_duplicate_file_matches"):
            cwa_db.cur.execute(f"DELETE FROM {table} WHERE book_id IN ({placeholders})", tuple(gone))
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


def _same_file_rows(candidate_book_ids=None):
    """(match key, book ids) for each set of books holding the same document file."""
    cwa_db = CWA_DB()
    where = ""
    params = ()
    if candidate_book_ids is not None:
        candidate_book_ids = {int(book_id) for book_id in candidate_book_ids if book_id is not None}
        if not candidate_book_ids:
            return []
        placeholders = ",".join("?" for _ in candidate_book_ids)
        where = (
            "WHERE match_key IN (SELECT match_key FROM cwa_duplicate_file_matches "
            f"WHERE book_id IN ({placeholders}))"
        )
        params = tuple(candidate_book_ids)
    cwa_db.cur.execute(
        f"""
        SELECT match_key, GROUP_CONCAT(DISTINCT book_id)
        FROM cwa_duplicate_file_matches
        {where}
        GROUP BY match_key
        HAVING COUNT(DISTINCT book_id) > 1
        """,
        params,
    )
    return cwa_db.cur.fetchall()


def _split_ids(book_ids_str):
    return {int(book_id) for book_id in (book_ids_str or "").split(",") if book_id}


def _merged_book_id_sets(metadata_sets, file_sets):
    """Combine metadata matches with identical-file matches into groups.

    Returns (book ids, holds identical files) pairs. Identical files join a
    metadata group only when every copy is in it. Copies with different
    metadata form their own group and leave their metadata groups, so one
    mislabelled file can't chain two different works into a group that
    "Select duplicates" or auto-resolve would thin out.
    """
    file_groups = _union(file_sets)
    owner = {book_id: index for index, group in enumerate(file_groups) for book_id in group}
    groups = []
    contained = set()
    for book_ids in metadata_sets:
        book_ids = set(book_ids)
        same_file = False
        for index in {owner[book_id] for book_id in book_ids if book_id in owner}:
            if file_groups[index] <= book_ids:
                contained.add(index)
                same_file = True
            else:
                book_ids -= file_groups[index]
        groups.append((book_ids, same_file))
    groups.extend((group, True) for index, group in enumerate(file_groups) if index not in contained)
    return [(book_ids, same_file) for book_ids, same_file in groups if len(book_ids) > 1]


def _union(sets):
    """Join sets that share a member (a book with two formats can link two file hashes)."""
    parent = {}

    def find(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for members in sets:
        members = list(members)
        for member in members:
            parent.setdefault(member, member)
        for member in members[1:]:
            parent[find(member)] = find(members[0])
    joined = {}
    for member in parent:
        joined.setdefault(find(member), set()).add(member)
    return list(joined.values())


def _split_by_language(books):
    """Split a metadata match by language. A book with no language goes with the
    others when they share one language, and stays apart when they don't."""
    by_language = {}
    for book in books:
        languages = getattr(book, "languages", None) or []
        code = (languages[0].lang_code or "").strip().lower() if languages else ""
        by_language.setdefault(code, []).append(book)
    unknown = by_language.pop("", [])
    if len(by_language) <= 1:
        return [unknown + next(iter(by_language.values()), [])]
    return list(by_language.values()) + [unknown]


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
        affected_ids.update(_split_ids(book_ids_str))
    for _match_key, book_ids_str in _same_file_rows(book_ids):
        affected_ids.update(_split_ids(book_ids_str))
    return affected_ids


def _no_author(name) -> bool:
    """calibre's "Unknown" stand-in (constants.UNKNOWN_AUTHOR): a book with no author shows none."""
    return (name or "").strip().lower() == "unknown"


def _decorate_books_for_group(books):
    for book in books:
        if not hasattr(book, "ordered_authors") or not book.ordered_authors:
            book.ordered_authors = calibre_db.order_authors([book])
        book.author_names = ", ".join(
            author.name.replace("|", ",") for author in book.ordered_authors or []
            if author.name and not _no_author(author.name)
        )
        book.cover_url = f"/cover/{book.id}" if getattr(book, "has_cover", None) else "/static/generic_cover.svg"


def _group_from_books(books, same_file=False):
    books.sort(key=lambda book: _timestamp_or_default(book.timestamp, _AWARE_MIN), reverse=True)
    _decorate_books_for_group(books)
    display_title = books[0].title if books[0].title else "Untitled"
    display_author = "Unknown"
    if hasattr(books[0], "author_names") and books[0].author_names:
        display_author = books[0].author_names.split(",")[0].strip()
    return {
        "title": display_title,
        # Shown blank for a book with no author; the legacy hash keeps the stand-in,
        # so groups dismissed before stay dismissed
        "author": "" if _no_author(display_author) else display_author,
        "count": len(books),
        "books": books,
        "group_hash": group_hash_for_books(book.id for book in books),
        # What older versions keyed a dismissal on; still honoured (see filter_dismissed_groups)
        "legacy_group_hash": generate_group_hash(display_title, display_author),
        # At least two of the books hold byte-identical files, whatever their metadata says
        "same_file": same_file,
    }


def get_duplicate_groups_from_index(settings, include_dismissed=False, user_id=None, candidate_book_ids=None):
    metadata_sets = [_split_ids(book_ids_str) for _key, book_ids_str, _count
                     in _duplicate_key_rows(settings, candidate_book_ids=candidate_book_ids)]
    file_sets = [_split_ids(book_ids_str) for _hash, book_ids_str in _same_file_rows(candidate_book_ids)]
    all_ids = set().union(*metadata_sets, *file_sets)
    # One batched load for every group; books `user_id` may not see drop out here
    books_by_id = {int(book.id): book for book in _load_books_by_ids(all_ids, user_id=user_id)}

    if get_effective_duplicate_criteria(settings)["language"]:
        split_sets = []
        for book_ids in metadata_sets:
            books = [books_by_id[book_id] for book_id in book_ids if book_id in books_by_id]
            split_sets.extend({int(book.id) for book in part} for part in _split_by_language(books))
        metadata_sets = split_sets

    duplicate_groups = []
    for book_ids, same_file in _merged_book_id_sets(metadata_sets, file_sets):
        books = [books_by_id[book_id] for book_id in book_ids if book_id in books_by_id]
        if len(books) < 2:
            continue
        duplicate_groups.append(_group_from_books(books, same_file=same_file))

    duplicate_groups.sort(key=lambda group: (group["title"].lower(), group["author"].lower()))
    if not include_dismissed:
        duplicate_groups = filter_dismissed_groups(duplicate_groups, user_id=user_id)
    return duplicate_groups


def visible_cached_groups(cached_groups, user_id=None):
    """Keep the cached groups in which `user_id` can see at least two books, as the Duplicates page does."""
    if not cached_groups or user_id is None:
        return cached_groups
    cached_ids = set()
    for group in cached_groups:
        cached_ids.update(_cached_group_book_ids(group))
    visible_ids = _visible_book_ids(cached_ids, user_id)
    return [group for group in cached_groups if len(_cached_group_book_ids(group) & visible_ids) > 1]


def forget_deleted_books(book_ids):
    """Drop deleted books from the index and the cached groups, without regrouping.

    Groups left with one book disappear. last_scanned_book_id stays put, so books
    imported but not yet indexed are still picked up by the next incremental scan.
    """
    book_ids = {int(book_id) for book_id in book_ids if book_id is not None}
    if not book_ids:
        return
    delete_book_keys(book_ids)
    cwa_db = CWA_DB()
    cache_data = cwa_db.get_duplicate_cache()
    if not cache_data:
        return
    groups = []
    for group in cache_data.get("duplicate_groups") or []:
        remaining = [book_id for book_id in group.get("book_ids", []) if int(book_id) not in book_ids]
        if len(remaining) > 1:
            groups.append({**group, "book_ids": remaining, "count": len(remaining)})
    cwa_db.cur.execute(
        "UPDATE cwa_duplicate_cache SET duplicate_groups_json = ?, total_count = ? WHERE id = 1",
        (json.dumps(groups), len(groups)),
    )
    cwa_db.con.commit()


def unresolved_cached_group_count(user_id):
    """Groups the Duplicates page shows `user_id`, counted from the cache rather than a rescan."""
    cache_data = CWA_DB().get_duplicate_cache() or {}
    groups = filter_dismissed_groups(cache_data.get("duplicate_groups") or [], user_id=user_id)
    return len(visible_cached_groups(groups, user_id=user_id))


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
        "legacy_group_hash": group.get("legacy_group_hash", ""),
        "same_file": bool(group.get("same_file")),
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


def merge_affected_groups_into_cache(candidate_book_ids, settings, scanned_new_books=True):
    """Regroup the books `candidate_book_ids` touch and splice them into the cache.

    `scanned_new_books` says the candidates include every book added since the last
    scan; only then may last_scanned_book_id move up to the library's newest book.
    """
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
        affected_ids.update(_split_ids(book_ids_str))
    for _match_key, book_ids_str in _same_file_rows(candidate_book_ids):
        affected_ids.update(_split_ids(book_ids_str))

    cwa_db = CWA_DB()
    cache_data = cwa_db.get_duplicate_cache() or {}
    cached_groups = cache_data.get("duplicate_groups", []) or []
    retained_groups = [group for group in cached_groups if not (_cached_group_book_ids(group) & affected_ids)]
    fresh_groups = get_duplicate_groups_from_index(settings, include_dismissed=True, candidate_book_ids=affected_ids)
    merged_groups = retained_groups + fresh_groups
    merged_groups.sort(key=lambda group: (group["title"].lower(), group["author"].lower()))
    last_scanned_book_id = (_current_max_book_id() if scanned_new_books
                            else int(cache_data.get("last_scanned_book_id") or 0))
    _write_duplicate_cache_groups(cwa_db, merged_groups, last_scanned_book_id)
    return {"updated": True, "pending": False, "merged_count": len(merged_groups)}


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
