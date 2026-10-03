# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace
import importlib.util
import json
import pathlib
import sqlite3
import sys

import pytest


@pytest.fixture(autouse=True)
def _isolate_sys_modules(isolated_sys_modules):
    """Every test here writes stubs into sys.modules; undo them afterwards."""
    yield


def _install_stub(name, attrs=None):
    module = ModuleType(name)
    if attrs:
        for key, value in attrs.items():
            setattr(module, key, value)
    sys.modules[name] = module
    return module


def _load_duplicate_index_module():
    for name in list(sys.modules):
        if name in ("cps.duplicate_index", "cps.duplicates", "cps", "sqlalchemy") or name.startswith(
            "sqlalchemy."
        ):
            sys.modules.pop(name, None)

    cps = _install_stub("cps")

    class _Logger:
        def info(self, *args, **kwargs):
            return None

    logger = _install_stub("cps.logger", {"create": lambda: _Logger()})

    class _BookId:
        def in_(self, values):
            return ("book_id_in", tuple(values))

    class _OrderColumn:
        def desc(self):
            return self

    class _Books:
        id = _BookId()
        title = _OrderColumn()
        timestamp = _OrderColumn()
        data = object()
        authors = object()
        languages = object()
        series = object()
        publishers = object()
        tags = object()
        comments = object()
        ratings = object()
        identifiers = object()

    class _DataColumn:
        def __init__(self, name):
            self.name = name

        def __eq__(self, value):
            return ("data_eq", self.name, value)

    class _Data:
        book = _DataColumn("book")
        format = _DataColumn("format")
        uncompressed_size = _DataColumn("uncompressed_size")

    db = _install_stub("cps.db", {"Books": _Books, "Data": _Data})
    calibre_db = _install_stub("cps.calibre_db", {"session": None, "order_authors": lambda books: books[0].authors})
    config = _install_stub("cps.config", {"get_book_path": lambda: "/library"})
    cps.db = db
    cps.calibre_db = calibre_db
    cps.config = config
    cps.logger = logger

    _install_stub("cwa_db", {"CWA_DB": object})
    # The real matching rules: they are pure, and the index's behaviour depends on them
    rules_path = pathlib.Path(__file__).resolve().parents[2] / "cps" / "duplicate_rules.py"
    rules_spec = importlib.util.spec_from_file_location("cps.duplicate_rules", rules_path)
    rules = importlib.util.module_from_spec(rules_spec)
    rules.__package__ = "cps"
    sys.modules["cps.duplicate_rules"] = rules
    rules_spec.loader.exec_module(rules)

    _install_stub("cps.duplicate_detection", {
        "filter_dismissed_groups": lambda groups, user_id=None: [
            group for group in groups if group.get("group_hash") != "dismissed"
        ],
        "get_common_filters": lambda user_id=None: True,
    })

    duplicate_index_path = pathlib.Path(__file__).resolve().parents[2] / "cps" / "duplicate_index.py"
    spec = importlib.util.spec_from_file_location("cps.duplicate_index", duplicate_index_path)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "cps"
    sys.modules["cps.duplicate_index"] = module
    spec.loader.exec_module(module)
    return module


class _FakeCwaDB:
    _connection = None

    def __init__(self):
        self.con = self.__class__._connection
        self.cur = self.con.cursor()

    @classmethod
    def reset(cls):
        cls._connection = sqlite3.connect(":memory:")
        cls._connection.executescript(
            """
            CREATE TABLE cwa_duplicate_book_keys (
                book_id INTEGER PRIMARY KEY,
                normalized_title TEXT NOT NULL DEFAULT '',
                normalized_author TEXT NOT NULL DEFAULT '',
                format_signature TEXT NOT NULL DEFAULT '',
                duplicate_key TEXT NOT NULL,
                criteria_fingerprint TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX idx_cwa_duplicate_book_keys_key
                ON cwa_duplicate_book_keys(criteria_fingerprint, duplicate_key);
            CREATE TABLE cwa_duplicate_file_matches (
                book_id INTEGER NOT NULL,
                format TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                file_mtime_ns INTEGER NOT NULL,
                match_key TEXT NOT NULL
            );
            CREATE UNIQUE INDEX idx_cwa_duplicate_file_matches_file ON cwa_duplicate_file_matches(book_id, format);
            CREATE TABLE cwa_duplicate_cache (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                scan_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                duplicate_groups_json TEXT,
                total_count INTEGER DEFAULT 0,
                scan_pending INTEGER DEFAULT 1,
                last_scanned_book_id INTEGER DEFAULT 0
            );
            INSERT INTO cwa_duplicate_cache (id, scan_pending) VALUES (1, 1);
            """
        )

    def get_duplicate_cache(self):
        row = self.cur.execute(
            """
            SELECT duplicate_groups_json, total_count, scan_pending, last_scanned_book_id
            FROM cwa_duplicate_cache
            WHERE id = 1
            """
        ).fetchone()
        if row and row[0]:
            return {
                "duplicate_groups": json.loads(row[0]),
                "total_count": row[1],
                "scan_pending": bool(row[2]),
                "last_scanned_book_id": row[3],
            }
        return None

    def update_duplicate_cache(self, duplicate_groups, total_count, max_book_id=None):
        serializable = [
            {
                "title": group.get("title", ""),
                "author": group.get("author", ""),
                "count": group.get("count", 0),
                "group_hash": group.get("group_hash", ""),
                "book_ids": [book.id for book in group.get("books", [])],
            }
            for group in duplicate_groups
        ]
        self.cur.execute(
            """
            UPDATE cwa_duplicate_cache
            SET duplicate_groups_json = ?, total_count = ?, scan_pending = 0, last_scanned_book_id = ?
            WHERE id = 1
            """,
            (json.dumps(serializable), total_count, max_book_id or 0),
        )
        self.con.commit()
        return True


class _Query:
    def __init__(self, books, scalar_value=None, data_rows=False):
        self.books = list(books)
        self.scalar_value = scalar_value
        # A query on Data.book returns one (book id,) row per matching book
        self.data_rows = data_rows

    def options(self, *args):
        return self

    def filter(self, *expressions):
        for expression in expressions:
            if isinstance(expression, tuple) and expression[0] == "book_id_in":
                wanted = set(expression[1])
                self.books = [book for book in self.books if book.id in wanted]
            elif isinstance(expression, tuple) and expression[0] == "exclude_ids":
                self.books = [book for book in self.books if book.id not in expression[1]]
            elif isinstance(expression, tuple) and expression[0] == "data_eq":
                _kind, field, value = expression
                self.books = [book for book in self.books if any(
                    (data.format.upper() if field == "format" else getattr(data, field)) == value
                    for data in book.data)]
        return self

    def order_by(self, *args):
        return self

    def all(self):
        if self.data_rows:
            return [(book.id,) for book in self.books]
        return list(self.books)

    def scalar(self):
        return self.scalar_value


class _Session:
    def __init__(self, books):
        self.books = list(books)

    def query(self, subject):
        subject_text = str(subject)
        if "max" in subject_text:
            return _Query([], max([book.id for book in self.books], default=0))
        if "count" in subject_text:
            return _Query([], len(self.books))
        # Data.book or Books.id: one (book id,) row per matching book
        if getattr(subject, "name", None) == "book" or type(subject).__name__ == "_BookId":
            return _Query(self.books, data_rows=True)
        return _Query(self.books)


def _book(
    book_id,
    title,
    author,
    formats=None,
    timestamp=None,
):
    return SimpleNamespace(
        id=book_id,
        title=title,
        authors=[SimpleNamespace(name=author)],
        data=[SimpleNamespace(format=fmt, uncompressed_size=0, name=title) for fmt in (formats or [])],
        path=f"Author/Book ({book_id})",
        timestamp=timestamp or datetime(2024, 1, book_id, tzinfo=timezone.utc),
        has_cover=False,
    )


@pytest.fixture
def duplicate_index(monkeypatch):
    module = _load_duplicate_index_module()
    _FakeCwaDB.reset()
    monkeypatch.setattr(module, "CWA_DB", _FakeCwaDB)
    monkeypatch.setattr(module, "selectinload", lambda value: value)
    monkeypatch.setattr(module, "lazyload", lambda value: value)
    yield module
    for name in (
        "cps.duplicate_index",
        "cps.duplicates",
        "cps.duplicate_rules",
        "cps.duplicate_detection",
        "cps.calibre_db",
        "cps.config",
        "cps.db",
        "cps.logger",
        "cps",
        "cwa_db",
    ):
        sys.modules.pop(name, None)


def test_effective_criteria_falls_back_to_title_author(duplicate_index):
    criteria = duplicate_index.get_effective_duplicate_criteria(
        {
            "duplicate_detection_title": 0,
            "duplicate_detection_author": 0,
            "duplicate_detection_format": 0,
        }
    )

    assert criteria == {
        "title": True,
        "author": True,
        "format": False,
    }


def test_fingerprint_changes_when_effective_criteria_change(duplicate_index):
    title_author = duplicate_index.get_criteria_fingerprint(
        {"duplicate_detection_title": 1, "duplicate_detection_author": 1, "duplicate_detection_format": 0}
    )
    title_author_format = duplicate_index.get_criteria_fingerprint(
        {"duplicate_detection_title": 1, "duplicate_detection_author": 1, "duplicate_detection_format": 1}
    )

    assert title_author != title_author_format


def test_build_book_key_parts_matches_python_duplicate_fallbacks(duplicate_index):
    book = _book(
        1,
        "Homer, The Iliad",
        "Homer",
        formats=["EPUB", "PDF"],
    )

    parts = duplicate_index.build_book_key_parts(book, {})

    assert parts.normalized_title == "the iliad"
    assert parts.normalized_author == "homer"
    assert parts.format_signature == "epub,pdf"


def test_title_only_key_parts_still_strip_primary_author_prefix(duplicate_index):
    book = _book(1, "Homer, The Iliad", "Homer")
    settings = {"duplicate_detection_title": 1, "duplicate_detection_author": 0}

    parts = duplicate_index.build_book_key_parts(book, settings)
    key_values = duplicate_index._enabled_key_values(parts, settings)

    assert parts.normalized_title == "the iliad"
    assert parts.normalized_author == "homer"
    assert key_values == [("title", "the iliad")]


def test_upsert_and_delete_book_keys(duplicate_index):
    books = [_book(1, "Dune", "Frank Herbert"), _book(2, "Dune", "Frank Herbert")]
    duplicate_index.calibre_db.session = _Session(books)
    settings = {"duplicate_detection_title": 1, "duplicate_detection_author": 1}

    result = duplicate_index.upsert_book_keys({1, 2}, settings)
    cwa_db = _FakeCwaDB()
    rows = cwa_db.cur.execute(
        """
        SELECT book_id, normalized_title, normalized_author, criteria_fingerprint
        FROM cwa_duplicate_book_keys
        ORDER BY book_id
        """
    ).fetchall()

    assert result["updated"] == 2
    assert [(row[0], row[1], row[2]) for row in rows] == [
        (1, "dune", "herbert f"),
        (2, "dune", "herbert f"),
    ]
    assert all(row[3] == result["fingerprint"] for row in rows)

    assert duplicate_index.delete_book_keys({1}) == 1
    remaining = cwa_db.cur.execute("SELECT book_id FROM cwa_duplicate_book_keys").fetchall()
    assert remaining == [(2,)]


def test_grouped_index_queries_and_dismissed_filtering(duplicate_index):
    books = [_book(1, "Dune", "Frank Herbert"), _book(2, "Dune", "Frank Herbert"), _book(3, "Other", "Writer")]
    duplicate_index.calibre_db.session = _Session(books)
    settings = {"duplicate_detection_title": 1, "duplicate_detection_author": 1}
    duplicate_index.upsert_book_keys({1, 2, 3}, settings)

    groups = duplicate_index.get_duplicate_groups_from_index(settings, include_dismissed=True)

    assert len(groups) == 1
    assert groups[0]["title"] == "Dune"
    assert groups[0]["author"] == "Frank Herbert"
    assert groups[0]["count"] == 2
    assert [book.id for book in groups[0]["books"]] == [2, 1]

    duplicate_index.filter_dismissed_groups = lambda groups, user_id=None: []
    assert duplicate_index.get_duplicate_groups_from_index(settings, include_dismissed=False, user_id=7) == []


def _visibility_filters(hidden_ids):
    """Stub get_common_filters: hides `hidden_ids`."""
    def get_common_filters(user_id=None):
        return ("exclude_ids", frozenset(hidden_ids))
    return get_common_filters


def test_visible_cached_groups_drop_groups_the_user_cannot_see_two_copies_of(duplicate_index, monkeypatch):
    books = [_book(1, "Dune", "Frank Herbert"), _book(2, "Dune", "Frank Herbert"),
             _book(3, "Emma", "Jane Austen"), _book(4, "Emma", "Jane Austen")]
    duplicate_index.calibre_db.session = _Session(books)
    monkeypatch.setattr(duplicate_index, "get_common_filters", _visibility_filters(hidden_ids={4}))
    cached = [{"title": "Dune", "group_hash": "a", "book_ids": [1, 2]},
              {"title": "Emma", "group_hash": "b", "book_ids": [3, 4]}]

    assert [group["title"] for group in duplicate_index.visible_cached_groups(cached, user_id=1)] == ["Dune"]
    assert duplicate_index.visible_cached_groups(cached, user_id=None) == cached


def test_a_group_of_books_with_no_author_shows_none_and_keeps_its_legacy_hash(duplicate_index):
    # calibre's "Unknown" stand-in is not shown; the legacy hash still has it, so a group
    # dismissed before stays dismissed. The group itself is keyed by its books.
    books = [SimpleNamespace(id=i, title="Lecture Notes", timestamp=None, has_cover=0,
                             ordered_authors=[SimpleNamespace(name="Unknown")]) for i in (1, 2)]
    group = duplicate_index._group_from_books(books)
    assert group["author"] == "" and [book.author_names for book in books] == ["", ""]
    assert group["legacy_group_hash"] == duplicate_index.generate_group_hash("Lecture Notes", "Unknown")
    assert group["group_hash"] == duplicate_index.group_hash_for_books([2, 1])

    named = [SimpleNamespace(id=3, title="Dune", timestamp=None, has_cover=0,
                             ordered_authors=[SimpleNamespace(name="Herbert| Frank"), SimpleNamespace(name="Unknown")])]
    assert duplicate_index._group_from_books(named)["author"] == "Herbert"
    assert named[0].author_names == "Herbert, Frank"


def test_cache_merge_keeps_serialization_shape(duplicate_index):
    books = [_book(1, "Dune", "Frank Herbert"), _book(2, "Dune", "Frank Herbert"), _book(3, "Other", "Writer")]
    duplicate_index.calibre_db.session = _Session(books)
    settings = {"duplicate_detection_title": 1, "duplicate_detection_author": 1}
    duplicate_index.upsert_book_keys({1, 2, 3}, settings)

    result = duplicate_index.merge_affected_groups_into_cache({1}, settings)
    cache = _FakeCwaDB().get_duplicate_cache()

    assert result["updated"] is True
    assert cache["total_count"] == 1
    assert set(cache["duplicate_groups"][0]) == {"title", "author", "count", "group_hash", "legacy_group_hash",
                                                 "same_file", "book_ids"}
    assert cache["duplicate_groups"][0]["book_ids"] == [2, 1]


def test_cache_merge_preserves_retained_group_book_ids(duplicate_index):
    books = [
        _book(1, "Dune", "Frank Herbert"),
        _book(2, "Dune", "Frank Herbert"),
        _book(3, "Foundation", "Isaac Asimov"),
        _book(4, "Foundation", "Isaac Asimov"),
    ]
    duplicate_index.calibre_db.session = _Session(books)
    settings = {"duplicate_detection_title": 1, "duplicate_detection_author": 1}
    duplicate_index.upsert_book_keys({1, 2, 3, 4}, settings)

    cwa_db = _FakeCwaDB()
    retained_group = {
        "title": "Foundation",
        "author": "Isaac Asimov",
        "count": 2,
        "group_hash": "foundation-hash",
        "book_ids": [4, 3],
    }
    cwa_db.cur.execute(
        """
        UPDATE cwa_duplicate_cache
        SET duplicate_groups_json = ?, total_count = ?, scan_pending = 0, last_scanned_book_id = ?
        WHERE id = 1
        """,
        (json.dumps([retained_group]), 1, 4),
    )
    cwa_db.con.commit()

    result = duplicate_index.merge_affected_groups_into_cache({1}, settings)
    cache = _FakeCwaDB().get_duplicate_cache()
    groups_by_title = {group["title"]: group for group in cache["duplicate_groups"]}

    assert result["updated"] is True
    assert groups_by_title["Foundation"]["book_ids"] == [4, 3]
    assert groups_by_title["Dune"]["book_ids"] == [2, 1]


def test_cache_merge_deletes_key_rows_for_missing_candidate_ids(duplicate_index):
    books = [_book(1, "Dune", "Frank Herbert"), _book(2, "Dune", "Frank Herbert")]
    duplicate_index.calibre_db.session = _Session(books)
    settings = {"duplicate_detection_title": 1, "duplicate_detection_author": 1}
    duplicate_index.upsert_book_keys({1, 2}, settings)
    metadata = duplicate_index.rebuild_duplicate_index(settings)
    groups = duplicate_index.get_duplicate_groups_from_index(settings, include_dismissed=True)
    _FakeCwaDB().update_duplicate_cache(groups, len(groups), metadata["max_book_id"])

    duplicate_index.calibre_db.session = _Session([books[1]])
    result = duplicate_index.merge_affected_groups_into_cache({1}, settings)
    key_rows = _FakeCwaDB().cur.execute("SELECT book_id FROM cwa_duplicate_book_keys ORDER BY book_id").fetchall()
    cache = _FakeCwaDB().get_duplicate_cache()

    assert result["updated"] is True
    assert key_rows == [(2,)]
    assert cache["duplicate_groups"] == []


def test_rebuild_duplicate_index_replaces_active_fingerprint_and_removes_orphans(duplicate_index):
    books = [_book(1, "Dune", "Frank Herbert"), _book(2, "Dune", "Frank Herbert")]
    duplicate_index.calibre_db.session = _Session(books)
    settings = {"duplicate_detection_title": 1, "duplicate_detection_author": 1}
    fingerprint = duplicate_index.get_criteria_fingerprint(settings)

    cwa_db = _FakeCwaDB()
    cwa_db.cur.execute(
        """
        INSERT INTO cwa_duplicate_book_keys (
            book_id, normalized_title, normalized_author, duplicate_key, criteria_fingerprint
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (1, "stale", "stale", "stale-key", fingerprint),
    )
    cwa_db.cur.execute(
        """
        INSERT INTO cwa_duplicate_book_keys (
            book_id, normalized_title, normalized_author, duplicate_key, criteria_fingerprint
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (99, "orphan", "orphan", "orphan-key", "abandoned-fingerprint"),
    )
    cwa_db.con.commit()

    result = duplicate_index.rebuild_duplicate_index(settings)
    rows = cwa_db.cur.execute(
        """
        SELECT book_id, normalized_title, criteria_fingerprint
        FROM cwa_duplicate_book_keys
        ORDER BY book_id
        """
    ).fetchall()

    assert result == {"max_book_id": 2, "indexed_count": 2, "fingerprint": fingerprint}
    assert rows == [(1, "dune", fingerprint), (2, "dune", fingerprint)]


def _seed_cache(scan_pending=False, last_scanned_book_id=1):
    cwa_db = _FakeCwaDB()
    cwa_db.cur.execute(
        """
        UPDATE cwa_duplicate_cache
        SET duplicate_groups_json = ?, scan_pending = ?, last_scanned_book_id = ?
        WHERE id = 1
        """,
        (json.dumps([]), int(scan_pending), last_scanned_book_id),
    )
    cwa_db.con.commit()


def test_has_valid_duplicate_index_baseline_states(duplicate_index):
    books = [_book(1, "Dune", "Frank Herbert"), _book(2, "Dune", "Frank Herbert")]
    settings = {"duplicate_detection_title": 1, "duplicate_detection_author": 1}

    duplicate_index.calibre_db.session = _Session(books)
    duplicate_index.upsert_book_keys({1, 2}, settings)
    _seed_cache(scan_pending=False, last_scanned_book_id=2)
    assert duplicate_index.has_valid_duplicate_index_baseline(settings) is True

    _seed_cache(scan_pending=True, last_scanned_book_id=2)
    assert duplicate_index.has_valid_duplicate_index_baseline(settings) is False
    assert duplicate_index.has_valid_duplicate_index_baseline(settings, candidate_book_ids={2}) is True

    _seed_cache(scan_pending=False, last_scanned_book_id=0)
    assert duplicate_index.has_valid_duplicate_index_baseline(settings) is False

    duplicate_index.delete_book_keys({2})
    _seed_cache(scan_pending=True, last_scanned_book_id=2)
    assert duplicate_index.has_valid_duplicate_index_baseline(settings) is False
    assert duplicate_index.has_valid_duplicate_index_baseline(settings, candidate_book_ids={2}) is True

    _FakeCwaDB.reset()
    duplicate_index.calibre_db.session = _Session([])
    _seed_cache(scan_pending=False, last_scanned_book_id=0)
    assert duplicate_index.has_valid_duplicate_index_baseline(settings) is True


def test_has_valid_duplicate_index_baseline_requires_candidate_to_cover_missing_current_book(duplicate_index):
    books = [
        _book(1, "Dune", "Frank Herbert"),
        _book(2, "Dune Messiah", "Frank Herbert"),
        _book(3, "Children of Dune", "Frank Herbert"),
        _book(4, "God Emperor of Dune", "Frank Herbert"),
    ]
    settings = {"duplicate_detection_title": 1, "duplicate_detection_author": 1}

    duplicate_index.calibre_db.session = _Session(books)
    duplicate_index.upsert_book_keys({1, 2, 3}, settings)
    _seed_cache(scan_pending=False, last_scanned_book_id=4)

    assert duplicate_index.has_valid_duplicate_index_baseline(settings, candidate_book_ids={3}) is False
    assert duplicate_index.has_valid_duplicate_index_baseline(settings, candidate_book_ids={4}) is True


def test_has_valid_duplicate_index_baseline_allows_initial_incremental_when_candidates_cover_library(duplicate_index):
    books = [_book(1, "Dune", "Frank Herbert")]
    settings = {"duplicate_detection_title": 1, "duplicate_detection_author": 1}

    duplicate_index.calibre_db.session = _Session(books)

    assert duplicate_index.has_valid_duplicate_index_baseline(settings) is False
    assert duplicate_index.has_valid_duplicate_index_baseline(settings, candidate_book_ids={1}) is True
    assert duplicate_index.has_valid_duplicate_index_baseline(settings, candidate_book_ids={2}) is False


def test_manual_full_scan_not_needed_for_new_books_during_dirty_ingest(duplicate_index, monkeypatch, tmp_path):
    books = [
        _book(1, "Dune", "Frank Herbert"),
        _book(2, "Dune Messiah", "Frank Herbert"),
        _book(3, "Children of Dune", "Frank Herbert"),
        _book(4, "God Emperor of Dune", "Frank Herbert"),
    ]
    settings = {"duplicate_detection_title": 1, "duplicate_detection_author": 1}
    dirty_file = tmp_path / "cwa_ingest_batch_dirty"

    duplicate_index.calibre_db.session = _Session(books)
    duplicate_index.upsert_book_keys({1, 2}, settings)
    _seed_cache(scan_pending=True, last_scanned_book_id=2)
    monkeypatch.setattr(duplicate_index, "INGEST_BATCH_DIRTY_FILE", str(dirty_file))
    dirty_file.write_text("dirty_at=1\n")

    assert duplicate_index.duplicate_index_needs_manual_full_scan(settings) is False


def test_manual_full_scan_not_needed_for_new_books_during_running_ingest_follow_up(
    duplicate_index, monkeypatch, tmp_path
):
    books = [
        _book(1, "Dune", "Frank Herbert"),
        _book(2, "Dune Messiah", "Frank Herbert"),
    ]
    settings = {"duplicate_detection_title": 1, "duplicate_detection_author": 1}
    dirty_file = tmp_path / "cwa_ingest_batch_dirty"

    duplicate_index.calibre_db.session = _Session(books)
    duplicate_index.upsert_book_keys({1}, settings)
    _seed_cache(scan_pending=True, last_scanned_book_id=1)
    monkeypatch.setattr(duplicate_index, "INGEST_BATCH_DIRTY_FILE", str(dirty_file))
    dirty_file.with_suffix(dirty_file.suffix + ".running").write_text("dirty_at=1\n")

    assert duplicate_index.duplicate_index_needs_manual_full_scan(settings) is False


def test_manual_full_scan_not_needed_while_ingest_active(duplicate_index, monkeypatch, tmp_path):
    books = [_book(1, "Dune", "Frank Herbert")]
    settings = {"duplicate_detection_title": 1, "duplicate_detection_author": 1}
    active_file = tmp_path / "cwa_ingest_batch_active"

    duplicate_index.calibre_db.session = _Session(books)
    _seed_cache(scan_pending=True, last_scanned_book_id=0)
    monkeypatch.setattr(duplicate_index, "INGEST_BATCH_ACTIVE_FILE", str(active_file))
    active_file.write_text("active_at=1\n")

    assert duplicate_index.duplicate_index_needs_manual_full_scan(settings) is False


def test_initial_manual_full_scan_not_needed_during_dirty_ingest(duplicate_index, monkeypatch, tmp_path):
    books = [_book(1, "Dune", "Frank Herbert")]
    settings = {"duplicate_detection_title": 1, "duplicate_detection_author": 1}
    dirty_file = tmp_path / "cwa_ingest_batch_dirty"

    duplicate_index.calibre_db.session = _Session(books)
    _seed_cache(scan_pending=True, last_scanned_book_id=0)
    monkeypatch.setattr(duplicate_index, "INGEST_BATCH_DIRTY_FILE", str(dirty_file))
    dirty_file.write_text("dirty_at=1\n")

    assert duplicate_index.duplicate_index_needs_manual_full_scan(settings) is False


def test_manual_full_scan_not_needed_when_pending_cache_has_complete_index(duplicate_index):
    books = [
        _book(1, "Dune", "Frank Herbert"),
        _book(2, "Dune Messiah", "Frank Herbert"),
    ]
    settings = {"duplicate_detection_title": 1, "duplicate_detection_author": 1}

    duplicate_index.calibre_db.session = _Session(books)
    duplicate_index.upsert_book_keys({1, 2}, settings)
    _seed_cache(scan_pending=True, last_scanned_book_id=2)

    assert duplicate_index.duplicate_index_needs_manual_full_scan(settings) is False


def test_manual_full_scan_needed_for_old_missing_book_even_during_dirty_ingest(duplicate_index, monkeypatch, tmp_path):
    books = [
        _book(1, "Dune", "Frank Herbert"),
        _book(2, "Dune Messiah", "Frank Herbert"),
        _book(3, "Children of Dune", "Frank Herbert"),
    ]
    settings = {"duplicate_detection_title": 1, "duplicate_detection_author": 1}
    dirty_file = tmp_path / "cwa_ingest_batch_dirty"

    duplicate_index.calibre_db.session = _Session(books)
    duplicate_index.upsert_book_keys({1, 3}, settings)
    _seed_cache(scan_pending=True, last_scanned_book_id=3)
    monkeypatch.setattr(duplicate_index, "INGEST_BATCH_DIRTY_FILE", str(dirty_file))
    dirty_file.write_text("dirty_at=1\n")

    assert duplicate_index.duplicate_index_needs_manual_full_scan(settings) is True


def test_mark_duplicate_index_pending_sets_cache_pending(duplicate_index):
    _seed_cache(scan_pending=False, last_scanned_book_id=4)

    assert duplicate_index.mark_duplicate_index_pending("criteria changed") is True

    cache = _FakeCwaDB().get_duplicate_cache()
    assert cache["scan_pending"] is True
    assert cache["last_scanned_book_id"] == 4


def test_schema_contains_duplicate_book_key_table():
    schema = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "cwa_schema.sql"
    sql = schema.read_text()
    connection = sqlite3.connect(":memory:")
    connection.executescript(sql)

    table = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'cwa_duplicate_book_keys'"
    ).fetchone()
    index = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_cwa_duplicate_book_keys_key'"
    ).fetchone()

    assert table == ("cwa_duplicate_book_keys",)
    assert index == ("idx_cwa_duplicate_book_keys_key",)


def _book_with_file(library, book_id, title, author, content, fmt="PDF"):
    """A book whose one `fmt` file holds `content`, stored under `library` the way calibre lays it out."""
    book = _book(book_id, title, author, formats=[fmt])
    folder = library / book.path
    folder.mkdir(parents=True)
    (folder / f"{title}.{fmt.lower()}").write_bytes(content)
    book.data[0].uncompressed_size = len(content)
    return book


@pytest.fixture
def library(duplicate_index, tmp_path, monkeypatch):
    monkeypatch.setattr(duplicate_index.config, "get_book_path", lambda: str(tmp_path))
    return tmp_path


TITLE_AUTHOR = {"duplicate_detection_title": 1, "duplicate_detection_author": 1}


def test_byte_identical_files_are_duplicates_whatever_their_metadata(duplicate_index, library):
    books = [
        _book_with_file(library, 1, "Generalized Tate Cohomology", "Greenlees", b"same pdf bytes"),
        _book_with_file(library, 2, "Generalized Tate Cohomolog", "Greenless", b"same pdf bytes"),
        # Same format and byte size by chance, different content: not a duplicate
        _book_with_file(library, 3, "Morrey Spaces", "Adams", b"other pdf byte"),
    ]
    duplicate_index.calibre_db.session = _Session(books)

    duplicate_index.rebuild_duplicate_index(TITLE_AUTHOR)
    groups = duplicate_index.get_duplicate_groups_from_index(TITLE_AUTHOR, include_dismissed=True)

    assert [sorted(book.id for book in group["books"]) for group in groups] == [[1, 2]]
    assert groups[0]["same_file"] is True


def test_same_file_copies_join_the_metadata_group_they_belong_to(duplicate_index, library):
    books = [
        _book_with_file(library, 1, "Dune", "Frank Herbert", b"first edition"),
        _book_with_file(library, 2, "Dune", "Frank Herbert", b"second print"),
        _book_with_file(library, 3, "Dune", "Frank Herbert", b"second print"),
    ]
    duplicate_index.calibre_db.session = _Session(books)

    duplicate_index.rebuild_duplicate_index(TITLE_AUTHOR)
    groups = duplicate_index.get_duplicate_groups_from_index(TITLE_AUTHOR, include_dismissed=True)

    assert [(sorted(book.id for book in group["books"]), group["same_file"]) for group in groups] == [([1, 2, 3], True)]


def test_a_mislabelled_copy_does_not_chain_two_works_together(duplicate_index, library):
    # Book 3 holds book 1's file under book 2's title: 1 and 3 are one document, but
    # 1 and 2 are different works and must not end up in one group
    books = [
        _book_with_file(library, 1, "Morrey Spaces", "David Adams", b"morrey pdf"),
        _book_with_file(library, 2, "Number Theory", "Kenneth Williams", b"number pdf one"),
        _book_with_file(library, 3, "Number Theory", "Kenneth Williams", b"morrey pdf"),
    ]
    duplicate_index.calibre_db.session = _Session(books)

    duplicate_index.rebuild_duplicate_index(TITLE_AUTHOR)
    groups = duplicate_index.get_duplicate_groups_from_index(TITLE_AUTHOR, include_dismissed=True)

    assert [(sorted(book.id for book in group["books"]), group["same_file"]) for group in groups] == [([1, 3], True)]


def test_metadata_only_groups_are_not_marked_identical(duplicate_index, library):
    books = [_book_with_file(library, 1, "Dune", "Frank Herbert", b"one"),
             _book_with_file(library, 2, "Dune", "Frank Herbert", b"three")]
    duplicate_index.calibre_db.session = _Session(books)

    duplicate_index.rebuild_duplicate_index(TITLE_AUTHOR)

    assert duplicate_index.get_duplicate_groups_from_index(TITLE_AUTHOR, include_dismissed=True)[0]["same_file"] is False


def test_an_imported_copy_of_an_existing_file_is_found_incrementally(duplicate_index, library):
    old = _book_with_file(library, 1, "Knots and Links", "Cromwell", b"knots pdf")
    duplicate_index.calibre_db.session = _Session([old])
    duplicate_index.rebuild_duplicate_index(TITLE_AUTHOR)
    duplicate_index._write_duplicate_cache_groups(_FakeCwaDB(), [], 1)

    new = _book_with_file(library, 2, "Knots and Links and", "Cromwell", b"knots pdf")
    duplicate_index.calibre_db.session = _Session([old, new])
    duplicate_index.merge_affected_groups_into_cache({2}, TITLE_AUTHOR)

    cached = _FakeCwaDB().get_duplicate_cache()["duplicate_groups"]
    assert [sorted(group["book_ids"]) for group in cached] == [[1, 2]]


def test_unchanged_files_keep_their_stored_hash(duplicate_index, library, monkeypatch):
    books = [_book_with_file(library, 1, "A", "X", b"same"), _book_with_file(library, 2, "B", "Y", b"same")]
    duplicate_index.calibre_db.session = _Session(books)
    duplicate_index.rebuild_duplicate_index(TITLE_AUTHOR)

    hashed = []
    real_digests = duplicate_index._block_digests
    monkeypatch.setattr(duplicate_index, "_block_digests", lambda path: hashed.append(path) or real_digests(path))
    duplicate_index.rebuild_duplicate_index(TITLE_AUTHOR)

    assert hashed == []
    assert len(duplicate_index.get_duplicate_groups_from_index(TITLE_AUTHOR, include_dismissed=True)) == 1


def test_deleting_a_book_drops_its_file_key(duplicate_index, library):
    books = [_book_with_file(library, 1, "A", "X", b"same"), _book_with_file(library, 2, "B", "Y", b"same")]
    duplicate_index.calibre_db.session = _Session(books)
    duplicate_index.rebuild_duplicate_index(TITLE_AUTHOR)

    duplicate_index.delete_book_keys({2})

    assert duplicate_index.get_duplicate_groups_from_index(TITLE_AUTHOR, include_dismissed=True) == []


def test_identical_copies_imported_in_one_batch_find_each_other(duplicate_index, library):
    books = [_book_with_file(library, 1, "Lecture Notes Week 1", "Unknown", b"notes"),
             _book_with_file(library, 2, "MATH101 handout", "Unknown", b"notes")]
    duplicate_index.calibre_db.session = _Session(books)

    duplicate_index.merge_affected_groups_into_cache({1, 2}, TITLE_AUTHOR)

    cached = _FakeCwaDB().get_duplicate_cache()["duplicate_groups"]
    assert [sorted(group["book_ids"]) for group in cached] == [[1, 2]]


def _groups(duplicate_index, settings=TITLE_AUTHOR):
    return [sorted(book.id for book in group["books"])
            for group in duplicate_index.get_duplicate_groups_from_index(settings, include_dismissed=True)]


def test_same_size_copies_differing_only_in_embedded_metadata_are_one_document(duplicate_index, library):
    # As on a real library: a re-tagged PDF keeps its size and differs in a block or two
    body = bytes(range(256)) * 80
    retagged = bytearray(body)
    retagged[100:116] = b"different header"
    other = bytes(reversed(body))  # same size, different everywhere: a chance match
    books = [
        _book_with_file(library, 1, "Low-Dimensional Topology", "Johannson", body),
        _book_with_file(library, 2, "LDT scan", "Unknown", bytes(retagged)),
        _book_with_file(library, 3, "Morrey Spaces", "Adams", other),
    ]
    duplicate_index.calibre_db.session = _Session(books)

    duplicate_index.rebuild_duplicate_index(TITLE_AUTHOR)

    assert _groups(duplicate_index) == [[1, 2]]


def test_books_with_no_author_are_not_matched_by_a_placeholder_title(duplicate_index):
    books = [_book(1, "Untitled-1", "Unknown"), _book(2, "Untitled-1", "Unknown"),
             _book(3, "DjVu Document", "Unknown"), _book(4, "DjVu Document", "Unknown")]
    duplicate_index.calibre_db.session = _Session(books)

    duplicate_index.rebuild_duplicate_index(TITLE_AUTHOR)

    assert _groups(duplicate_index) == []


def test_titles_and_authors_match_across_punctuation_case_and_initials(duplicate_index):
    books = [_book(1, "Low-Dimensional Topology", "Klaus Johannson"),
             _book(2, "low dimensional topology", "K. Johannson"),
             _book(3, "Learning Non-Gaussian Models", "Ricardo Baptista"),
             _book(4, "Learning non-Gaussian models", "Baptista| R"),
             _book(5, "Learning Non-Gaussian Models", "Rebecca Morrison")]
    duplicate_index.calibre_db.session = _Session(books)

    duplicate_index.rebuild_duplicate_index(TITLE_AUTHOR)

    assert sorted(_groups(duplicate_index)) == [[1, 2], [3, 4]]


def test_forgetting_a_deleted_book_updates_the_cache_without_moving_the_scan_mark(duplicate_index):
    books = [_book(i, "Dune", "Frank Herbert") for i in (1, 2, 3)] + [_book(i, "Emma", "Jane Austen") for i in (4, 5)]
    duplicate_index.calibre_db.session = _Session(books)
    duplicate_index.rebuild_duplicate_index(TITLE_AUTHOR)
    groups = duplicate_index.get_duplicate_groups_from_index(TITLE_AUTHOR, include_dismissed=True)
    duplicate_index._write_duplicate_cache_groups(_FakeCwaDB(), groups, 5)

    duplicate_index.forget_deleted_books([3, 5])

    cache = _FakeCwaDB().get_duplicate_cache()
    assert [sorted(group["book_ids"]) for group in cache["duplicate_groups"]] == [[1, 2]]
    assert cache["last_scanned_book_id"] == 5
    remaining = _FakeCwaDB().cur.execute("SELECT book_id FROM cwa_duplicate_book_keys ORDER BY book_id").fetchall()
    assert remaining == [(1,), (2,), (4,)]


def test_an_edit_only_scan_leaves_unindexed_new_books_for_the_next_scan(duplicate_index):
    books = [_book(1, "Dune", "Frank Herbert"), _book(2, "Dune", "Frank Herbert")]
    duplicate_index.calibre_db.session = _Session(books)
    duplicate_index.rebuild_duplicate_index(TITLE_AUTHOR)
    duplicate_index._write_duplicate_cache_groups(_FakeCwaDB(), [], 2)
    # Book 3 was imported but not indexed yet; book 1 was edited
    duplicate_index.calibre_db.session = _Session(books + [_book(3, "Emma", "Jane Austen")])

    duplicate_index.merge_affected_groups_into_cache({1}, TITLE_AUTHOR, scanned_new_books=False)

    assert _FakeCwaDB().get_duplicate_cache()["last_scanned_book_id"] == 2
