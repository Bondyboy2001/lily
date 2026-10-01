# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The library Trash: files and database rows of deleted books, kept so they can be restored.

A deleted book's folder is renamed to ``<books dir>/.lily-trash/<stamp>_<book id>/`` (same
filesystem, so nothing is copied) and ``<stamp>_<book id>.json`` is written next to it
with everything needed to put the book back: its metadata.db rows (book, authors, tags,
series, publishers, languages, ratings, identifiers, comments, formats, custom columns)
and its app.db rows (shelves, read status, archived flag, bookmarks, reader progress,
downloads). A deleted single format goes to ``<stamp>_<book id>_<FORMAT>/``.

The entry folder names never end in ``(<id>)``, so ``calibredb restore_database`` and the
cover enforcer don't mistake them for live books; the library mirror skips dot folders.

No Flask and no cps imports: rows are read and written through a tiny executor
(``fetch``/``execute`` with named parameters), so the same code runs against a plain
sqlite3 connection in tests and against the app's SQLAlchemy sessions (cps/trash.py).
"""

import base64
import errno
import json
import math
import os
import re
import shutil
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Protocol

TRASH_DIRNAME = ".lily-trash"
MANIFEST_VERSION = 1
DEFAULT_RETENTION_DAYS = 30
_ENTRY_RE = re.compile(r"^[0-9]{8}T[0-9]{6}_[0-9]+(?:_[A-Za-z0-9]+)*$")
# Link tables we can't map to an entity (unknown plugins) are skipped rather than
# restored with dangling ids.
_LINK_RE = re.compile(r"^books_.+_link$")

# metadata.db link tables: link column -> (entity table, columns that identify an entity)
STANDARD_LINKS = {
    "books_authors_link": ("author", "authors", ("name",)),
    "books_tags_link": ("tag", "tags", ("name",)),
    "books_series_link": ("series", "series", ("name",)),
    "books_publishers_link": ("publisher", "publishers", ("name",)),
    "books_languages_link": ("lang_code", "languages", ("lang_code",)),
    "books_ratings_link": ("rating", "ratings", ("rating",)),
}
# Tables with a `book` column that are bookkeeping, not data worth restoring
SKIP_BOOK_TABLES = frozenset({"metadata_dirtied", "annotations_dirtied"})
# app.db tables that reference a book by `book_id`
APP_TABLES = ("book_shelf_link", "book_read_link", "bookmark", "web_reader_progress",
              "archived_book", "downloads")


class TrashError(Exception):
    pass


class Executor(Protocol):
    def fetch(self, sql: str, params: dict | None = None) -> list[dict]: ...

    def execute(self, sql: str, params: dict | None = None) -> int | None: ...


class SqliteExecutor:
    """Executor over a sqlite3 connection (tests, scripts)."""

    def __init__(self, con):
        self.con = con

    def fetch(self, sql, params=None):
        cur = self.con.execute(sql, params or {})
        names = [d[0] for d in cur.description or ()]
        return [dict(zip(names, row)) for row in cur.fetchall()]

    def execute(self, sql, params=None):
        return self.con.execute(sql, params or {}).lastrowid


# ---------------------------------------------------------------------------- helpers

def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _encode(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$b64": base64.b64encode(bytes(value)).decode("ascii")}
    return value


def _decode(value):
    if isinstance(value, dict) and set(value) == {"$b64"}:
        return base64.b64decode(value["$b64"])
    return value


def _row(row: dict, drop: Iterable[str] = ()) -> dict:
    dropped = set(drop)
    return {k: _encode(v) for k, v in row.items() if k not in dropped}


def _tables(ex: Executor, schema: str) -> dict[str, list[str]]:
    names = [r["name"] for r in ex.fetch(
        "SELECT name FROM %s.sqlite_master WHERE type='table'" % _q(schema))]
    return {n: [c["name"] for c in ex.fetch("PRAGMA %s.table_info(%s)" % (_q(schema), _q(n)))]
            for n in names}


def insert_row(ex: Executor, schema: str, table: str, row: dict, columns: list[str],
            or_ignore: bool = False) -> int | None:
    cols = [c for c in row if c in columns]
    sql = "INSERT %sINTO %s.%s (%s) VALUES (%s)" % (
        "OR IGNORE " if or_ignore else "", _q(schema), _q(table),
        ", ".join(_q(c) for c in cols), ", ".join(":p%d" % i for i in range(len(cols))))
    return ex.execute(sql, {"p%d" % i: _decode(row[c]) for i, c in enumerate(cols)})


def _link_specs(tables: dict[str, list[str]], ex: Executor, schema: str) -> dict:
    specs = {t: spec for t, spec in STANDARD_LINKS.items() if t in tables}
    if "custom_columns" in tables:
        for cc in ex.fetch("SELECT id FROM %s.custom_columns" % _q(schema)):
            link = "books_custom_column_%d_link" % cc["id"]
            entity = "custom_column_%d" % cc["id"]
            if link in tables and entity in tables:
                specs[link] = ("value", entity, ("value",))
    return specs


# ---------------------------------------------------------------------------- rows

def capture_book_rows(ex: Executor, book_id: int, schema: str = "main") -> dict:
    """Everything metadata.db holds about one book, in a JSON-safe dict."""
    tables = _tables(ex, schema)
    books = ex.fetch("SELECT * FROM %s.books WHERE id = :id" % _q(schema), {"id": book_id})
    if not books:
        raise TrashError("book %s not found in metadata.db" % book_id)
    specs = _link_specs(tables, ex, schema)
    links: dict[str, list[dict]] = {}
    for link, (col, entity, _match) in specs.items():
        items = []
        for row in ex.fetch("SELECT * FROM %s.%s WHERE book = :id" % (_q(schema), _q(link)), {"id": book_id}):
            found = ex.fetch("SELECT * FROM %s.%s WHERE id = :id" % (_q(schema), _q(entity)), {"id": row[col]})
            if found:
                items.append({"link": _row(row, ("id", "book", col)), "entity": _row(found[0], ("id",))})
        if items:
            links[link] = items
    rows: dict[str, list[dict]] = {}
    for table, cols in tables.items():
        if "book" not in cols or table in specs or table in SKIP_BOOK_TABLES or _LINK_RE.match(table):
            continue
        found = ex.fetch("SELECT * FROM %s.%s WHERE book = :id" % (_q(schema), _q(table)), {"id": book_id})
        if found:
            rows[table] = [_row(r, ("id", "book")) for r in found]
    return {"books": _row(books[0]), "links": links, "rows": rows}


def book_path_for_id(old_path: str, old_id: int, new_id: int) -> str:
    """'Author/Title (12)' -> 'Author/Title (34)' (unchanged when the id is the same)."""
    if old_id == new_id:
        return old_path
    head, leaf = os.path.split(old_path)
    suffix = " (%d)" % old_id
    leaf = leaf[:-len(suffix)] if leaf.endswith(suffix) else leaf
    return "/".join(p for p in (head, "%s (%d)" % (leaf, new_id)) if p)


def restore_book_rows(ex: Executor, snapshot: dict, schema: str = "main") -> tuple[int, str]:
    """Re-inserts a captured book. Keeps its id when that is still free (ids are never
    reused by metadata.db), otherwise takes a new one and adjusts the folder name.
    Authors, tags, ... are matched by name and created if they are gone.
    Returns (book id, book path). The caller commits."""
    tables = _tables(ex, schema)
    book = dict(snapshot["books"])
    old_id = int(book["id"])
    taken = ex.fetch("SELECT id FROM %s.books WHERE id = :id" % _q(schema), {"id": old_id})
    if taken:
        book.pop("id")
    new_id = insert_row(ex, schema, "books", book, tables["books"])
    new_id = old_id if not taken else int(new_id or 0)
    path = book_path_for_id(book["path"], old_id, new_id)
    # The insert trigger replaces sort and uuid; put the originals back.
    ex.execute("UPDATE %s.books SET path = :path, sort = :sort, uuid = :uuid WHERE id = :id" % _q(schema),
               {"path": path, "sort": book.get("sort"), "uuid": book.get("uuid"), "id": new_id})
    specs = _link_specs(tables, ex, schema)
    for link, items in snapshot.get("links", {}).items():
        if link not in specs:
            continue
        col, entity, match = specs[link]
        for item in items:
            ent = item["entity"]
            where = " AND ".join("%s = :m%d" % (_q(m), i) for i, m in enumerate(match))
            found = ex.fetch("SELECT id FROM %s.%s WHERE %s" % (_q(schema), _q(entity), where),
                             {"m%d" % i: _decode(ent.get(m)) for i, m in enumerate(match)})
            entity_id = found[0]["id"] if found else insert_row(ex, schema, entity, ent, tables[entity])
            row = dict(item["link"], book=new_id)
            row[col] = entity_id
            insert_row(ex, schema, link, row, tables[link], or_ignore=True)
    for table, items in snapshot.get("rows", {}).items():
        if table not in tables:
            continue
        for item in items:
            insert_row(ex, schema, table, dict(item, book=new_id), tables[table], or_ignore=True)
    return new_id, path


def capture_app_rows(ex: Executor, book_id: int, schema: str = "main") -> dict:
    """The app.db rows (shelves, read status, progress, ...) that point at one book."""
    tables = _tables(ex, schema)
    out = {}
    for table in APP_TABLES:
        if table in tables and "book_id" in tables[table]:
            found = ex.fetch("SELECT * FROM %s.%s WHERE book_id = :id" % (_q(schema), _q(table)), {"id": book_id})
            if found:
                out[table] = [_row(r, ("id",)) for r in found]
    return out


def restore_app_rows(ex: Executor, captured: dict, book_id: int, schema: str = "main") -> int:
    """Re-links captured app.db rows to `book_id`, skipping users and shelves that no
    longer exist. Returns the number of rows inserted. The caller commits."""
    tables = _tables(ex, schema)
    users = {r["id"] for r in ex.fetch("SELECT id FROM %s.user" % _q(schema))} if "user" in tables else set()
    shelves = {r["id"] for r in ex.fetch("SELECT id FROM %s.shelf" % _q(schema))} if "shelf" in tables else set()
    count = 0
    for table, items in captured.items():
        if table not in APP_TABLES or table not in tables:
            continue
        for item in items:
            if "user_id" in item and item["user_id"] is not None and item["user_id"] not in users:
                continue
            if table == "book_shelf_link" and item.get("shelf") not in shelves:
                continue
            insert_row(ex, schema, table, dict(item, book_id=book_id), tables[table], or_ignore=True)
            count += 1
    return count


# ---------------------------------------------------------------------------- files

def trash_root(books_dir: str) -> str:
    return os.path.join(books_dir, TRASH_DIRNAME)


def valid_entry_id(entry_id: str) -> bool:
    return bool(entry_id) and bool(_ENTRY_RE.match(entry_id))


def new_entry_id(root: str, book_id: int, suffix: str = "", now: float | None = None) -> str:
    stamp = datetime.fromtimestamp(now if now is not None else time.time()).strftime("%Y%m%dT%H%M%S")
    base = "%s_%d%s" % (stamp, int(book_id), ("_" + re.sub(r"[^A-Za-z0-9]", "", suffix)) if suffix else "")
    entry_id, n = base, 1
    while os.path.exists(os.path.join(root, entry_id)) or os.path.exists(os.path.join(root, entry_id + ".json")):
        n += 1
        entry_id = "%s_%d" % (base, n)
    return entry_id


def entry_paths(root: str, entry_id: str) -> tuple[str, str]:
    if not valid_entry_id(entry_id):
        raise TrashError("invalid trash entry: %r" % entry_id)
    return os.path.join(root, entry_id), os.path.join(root, entry_id + ".json")


def write_manifest(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_manifest(root: str, entry_id: str) -> dict:
    _folder, manifest = entry_paths(root, entry_id)
    with open(manifest, encoding="utf-8") as f:
        return json.load(f)


def _remove_tree(path: str) -> list[str]:
    """rmtree that tolerates NFS '.nfsXXXX' placeholders (files still open elsewhere).
    Returns what could not be removed."""
    leftovers: list[str] = []

    def onexc(_func, p, _exc):
        if os.path.basename(p).startswith(".nfs") or os.path.isdir(p):
            leftovers.append(p)
        else:
            raise

    if os.path.isdir(path):
        shutil.rmtree(path, onexc=onexc)
    return leftovers


def move_path(src: str, dest: str) -> None:
    """os.rename, or copy-then-delete when src and dest are on different filesystems
    (mergerfs/unionfs branches). Raises OSError when nothing was moved."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        raise OSError(errno.EEXIST, "destination exists", dest)
    try:
        os.rename(src, dest)
        return
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
    if os.path.isdir(src):
        shutil.copytree(src, dest, symlinks=True)
        _remove_tree(src)
    else:
        shutil.copy2(src, dest)
        os.remove(src)


def remove_empty_dir(path: str) -> None:
    try:
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
    except OSError:
        pass


def _dir_size(path: str) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def list_entries(root: str, now: float | None = None, retention_days: int = DEFAULT_RETENTION_DAYS) -> list[dict]:
    """Every trash entry, newest first, with its manifest summary and size."""
    if not os.path.isdir(root):
        return []
    now = now if now is not None else time.time()
    ids = set()
    for name in os.listdir(root):
        base = name[:-5] if name.endswith(".json") else name
        if valid_entry_id(base):
            ids.add(base)
    entries = []
    for entry_id in ids:
        folder, manifest_path = entry_paths(root, entry_id)
        try:
            manifest = read_manifest(root, entry_id)
        except (OSError, ValueError):
            manifest = {}
        trashed_at = trashed_timestamp(manifest, folder, manifest_path)
        days_left = None
        if retention_days > 0:
            days_left = max(0, math.ceil((trashed_at + retention_days * 86400 - now) / 86400))
        entries.append({
            "id": entry_id,
            "kind": manifest.get("kind", "unknown"),
            "book_id": manifest.get("book_id"),
            "title": manifest.get("title") or entry_id,
            "authors": manifest.get("authors") or [],
            "formats": manifest.get("formats") or [],
            "reason": manifest.get("reason", ""),
            "trashed_by": manifest.get("trashed_by", ""),
            "trashed_at": datetime.fromtimestamp(trashed_at),
            "days_left": days_left,
            "has_files": os.path.isdir(folder),
            "has_manifest": bool(manifest),
            "size": _dir_size(folder) if os.path.isdir(folder) else 0,
        })
    entries.sort(key=lambda e: e["trashed_at"], reverse=True)
    return entries


def trashed_timestamp(manifest: dict, folder: str, manifest_path: str) -> float:
    try:
        return datetime.fromisoformat(manifest["trashed_at"]).timestamp()
    except (KeyError, TypeError, ValueError):
        for p in (manifest_path, folder):
            try:
                return os.path.getmtime(p)
            except OSError:
                continue
    return time.time()


def delete_entry(root: str, entry_id: str) -> list[str]:
    """Removes an entry for good. Returns leftovers (NFS placeholders) if any."""
    folder, manifest = entry_paths(root, entry_id)
    leftovers = _remove_tree(folder) if os.path.isdir(folder) else []
    if os.path.isfile(folder):
        os.remove(folder)
    if os.path.exists(manifest):
        os.remove(manifest)
    return leftovers


def purge(root: str, days: int, now: float | None = None,
          on_error: Callable[[str, Exception], None] | None = None) -> list[str]:
    """Deletes entries trashed more than `days` days ago (days <= 0 keeps everything).
    Returns the purged entry ids."""
    if days <= 0 or not os.path.isdir(root):
        return []
    cutoff = (now if now is not None else time.time()) - days * 86400
    purged = []
    for entry in list_entries(root, now=now, retention_days=days):
        if entry["trashed_at"].timestamp() >= cutoff:
            continue
        try:
            delete_entry(root, entry["id"])
            purged.append(entry["id"])
        except OSError as e:
            if on_error:
                on_error(entry["id"], e)
    return purged


def new_manifest(kind: str, book_id: int | None, **fields: Any) -> dict:
    data = {"version": MANIFEST_VERSION, "kind": kind, "book_id": book_id,
            "trashed_at": datetime.now(timezone.utc).isoformat()}
    data.update(fields)
    return data
