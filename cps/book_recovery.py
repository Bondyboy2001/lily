# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Book-level delete recovery.

capture_book() snapshots a book's files plus its metadata.db and app.db rows into
<app.db directory>/book_recovery/<uuid>/ (overridable with BOOK_RECOVERY_DIR).
There is no restore page: an archive holds plain files and JSON rows, so a book can
be put back by hand. Everything fails closed.
"""

import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import uuid as uuid_module
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config, logger, ub

log = logger.create()

MANIFEST_VERSION = 1
MANIFEST_NAME = "manifest.json"

RECOVERY_LOCK = threading.RLock()
_SERVICE_PAUSE_STATE = threading.local()


@contextmanager
def paused_services():
    """Hold the ingest-processor/cover-enforcer locks while mutating the library."""
    if getattr(_SERVICE_PAUSE_STATE, "held", False):
        yield
        return
    from .tasks.restore import acquire_service_locks, release_service_locks
    handles = acquire_service_locks()
    _SERVICE_PAUSE_STATE.held = True
    try:
        yield
    finally:
        _SERVICE_PAUSE_STATE.held = False
        release_service_locks(handles)

APP_TABLES = {
    "book_shelf_link": {"book": "book_id", "refs": {"shelf": "shelf"}},
    "book_read_link": {"book": "book_id", "refs": {"user_id": "user"}},
    "bookmark": {"book": "book_id", "refs": {"user_id": "user"}},
    "web_reader_progress": {"book": "book_id", "refs": {"user_id": "user"}},
    "reader_position": {"book": "book_id", "refs": {"user_id": "user"}},
    "downloads": {"book": "book_id", "refs": {"user_id": "user"}},
}

LINK_TABLES = {
    "books_authors_link": ("authors", "author", ("name",)),
    "books_tags_link": ("tags", "tag", ("name",)),
    "books_series_link": ("series", "series", ("name",)),
    "books_ratings_link": ("ratings", "rating", ("rating",)),
    "books_languages_link": ("languages", "lang_code", ("lang_code",)),
    "books_publishers_link": ("publishers", "publisher", ("name",)),
}

BOOK_TABLES = ("books", "data", "comments", "identifiers")
SKIP_TABLES = {"metadata_dirtied", "library_id", "custom_columns", "books_id"}

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CUSTOM_VALUE = re.compile(r"^custom_column_[0-9]+$")
_CUSTOM_LINK = re.compile(r"^books_custom_column_[0-9]+_link$")
_UUID_HEX = re.compile(r"^[0-9a-fA-F]{32}$")
_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


class RecoveryError(Exception):
    pass


def _ident(name):
    if not isinstance(name, str) or not _IDENT.match(name):
        raise RecoveryError("invalid identifier in recovery data: %r" % name)
    return '"%s"' % name


def recovery_root():
    root = os.environ.get("BOOK_RECOVERY_DIR") or \
        os.path.join(os.path.dirname(os.path.abspath(ub.app_DB_path)), "book_recovery")
    return os.path.abspath(root)


def _check_root(root):
    abs_root = os.path.abspath(root)
    real_root = os.path.realpath(abs_root)
    # Both the metadata.db folder and, in a split library, the books folder are off limits
    for library in {config.config_calibre_dir, config.get_book_path()}:
        if not library:
            continue
        real_lib = os.path.realpath(library)
        if real_root == real_lib or real_root.startswith(real_lib + os.sep):
            raise RecoveryError("book_recovery root must not be inside the library")
    if os.path.islink(abs_root):
        raise RecoveryError("book_recovery root must not be a symlink")
    trusted = set()
    for alias in ("/tmp", "/var", "/private/tmp", "/private/var"):
        if os.path.exists(alias):
            trusted.add(os.path.realpath(alias))
    cursor = os.sep
    parts = abs_root.split(os.sep)
    for part in parts[:-1]:
        if not part:
            continue
        cursor = os.path.join(cursor, part)
        if os.path.islink(cursor) and os.path.realpath(cursor) not in trusted:
            raise RecoveryError("book_recovery path contains a symlink")


def _path_has_symlink(path, within):
    path = os.path.abspath(path)
    within = os.path.abspath(within)
    if not (path == within or path.startswith(within + os.sep)):
        return True
    while path != within:
        if os.path.islink(path):
            return True
        path = os.path.dirname(path)
    return False


def _safe_join(base, rel):
    if not isinstance(rel, str) or not rel or os.path.isabs(rel):
        raise RecoveryError("unsafe path in recovery entry: %r" % rel)
    parts = rel.replace("\\", "/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise RecoveryError("unsafe path in recovery entry: %r" % rel)
    base_abs = os.path.abspath(base)
    dest = os.path.join(base_abs, *parts)
    real_base = os.path.realpath(base_abs)
    real_dest = os.path.realpath(dest)
    if not (real_dest == real_base or real_dest.startswith(real_base + os.sep)):
        raise RecoveryError("unsafe path in recovery entry: %r" % rel)
    if _path_has_symlink(dest, base_abs):
        raise RecoveryError("symlinked path in recovery entry: %r" % rel)
    return dest


def _book_dir(book):
    # Book folders live under get_book_path(), which differs from config_calibre_dir
    # (where metadata.db is) in a split library.
    library = os.path.abspath(config.get_book_path())
    path = os.path.normpath(os.path.join(library, book.path))
    real_path = os.path.realpath(path)
    real_lib = os.path.realpath(library)
    if not real_path.startswith(real_lib + os.sep):
        raise RecoveryError("book path escapes the library: %s" % book.path)
    if _path_has_symlink(path, library):
        raise RecoveryError("book path contains a symlink: %s" % book.path)
    return path


def _encode(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__blob__": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    return value


def _decode(value):
    if isinstance(value, dict):
        if "__blob__" in value:
            return base64.b64decode(value["__blob__"])
        if "__datetime__" in value:
            return datetime.fromisoformat(value["__datetime__"])
    return value


def _rows(con, table, where=None, param=None, schema=""):
    cols = _table_columns(con, table, schema)
    if not cols:
        raise RecoveryError("expected table missing: %s" % table)
    sql = 'SELECT %s FROM %s%s' % (",".join(_ident(c) for c in cols), schema, _ident(table))
    if where:
        _ident(where)
        sql += " WHERE %s=?" % _ident(where)
        cur = con.execute(sql, (param,))
    else:
        cur = con.execute(sql)
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _table_columns(con, table, schema=""):
    _ident(table)
    prefix = "%s." % schema.rstrip(".") if schema else ""
    return {r[1] for r in con.execute("PRAGMA %stable_info(%s)" % (prefix, _ident(table)))}


def _tables(con, schema=""):
    sql = "SELECT name FROM %ssqlite_master WHERE type='table'" % schema
    return {r[0] for r in con.execute(sql)}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _encode_rows(rows):
    return [{k: _encode(v) for k, v in row.items()} for row in rows]


def _connect_meta(path):
    con = sqlite3.connect(path)
    con.isolation_level = None
    con.create_function("title_sort", 1, lambda t: t)
    con.create_function("uuid4", 0, lambda: str(uuid_module.uuid4()))
    return con


def _library_uuid(meta_con):
    try:
        row = meta_con.execute("SELECT uuid FROM library_id LIMIT 1").fetchone()
    except sqlite3.Error:
        row = None
    return row[0] if row else None


def _copy_hashed(src, dest):
    before = _sha256(src)
    size = os.path.getsize(src)
    shutil.copyfile(src, dest)
    shutil.copystat(src, dest)
    if _sha256(src) != before:
        raise RecoveryError("source file changed during copy: %s" % src)
    if _sha256(dest) != before or os.path.getsize(dest) != size:
        raise RecoveryError("copied file failed verification: %s" % src)
    return {"sha256": before, "size": size}


def capture_book(book, book_format="", reason="delete"):
    """Snapshot one book's files and DB rows into the recovery root.

    book_format '' captures the whole book folder; a format name captures only
    that file plus its data row. Returns the recovery id. Raises RecoveryError
    on any failure - callers must treat it as fatal."""
    with RECOVERY_LOCK, paused_services():
        root = recovery_root()
        _check_root(root)

        src_dir = _book_dir(book)
        fmt = (book_format or "").upper() or None
        if fmt:
            data_rows = [d for d in book.data if d.format.upper() == fmt]
            if not data_rows:
                raise RecoveryError("format %s not found on book %s" % (fmt, book.id))
            names = [d.name + "." + d.format.lower() for d in data_rows]
        else:
            if not os.path.isdir(src_dir):
                raise RecoveryError("book folder missing: %s" % book.path)
            names = None

        recovery_id = uuid_module.uuid4().hex
        entry_dir = os.path.join(root, recovery_id)
        os.makedirs(root, exist_ok=True)
        stage_dir = tempfile.mkdtemp(prefix=".lily_recovery_", dir=root)
        files_dir = os.path.join(stage_dir, "files")

        meta_db = os.path.join(config.config_calibre_dir, "metadata.db")
        app_db = os.path.abspath(ub.app_DB_path)
        meta_con = None
        app_con = None
        created = []
        try:
            meta_con = _connect_meta(meta_db)
            app_con = sqlite3.connect(app_db)
            lib_uuid = _library_uuid(meta_con)
            if not lib_uuid:
                raise RecoveryError("library identity unavailable; cannot capture")
            if names is None:
                expected = {os.path.normpath(d.name + "." + d.format.lower())
                            for d in book.data}
                seen = set()
                for dirpath, dirnames, filenames in os.walk(src_dir):
                    rel_dir = os.path.relpath(dirpath, src_dir)
                    for d in dirnames:
                        if os.path.islink(os.path.join(dirpath, d)):
                            raise RecoveryError("symlinked subfolder in book path: %s" % d)
                    for f in filenames:
                        src = os.path.join(dirpath, f)
                        if os.path.islink(src):
                            raise RecoveryError("symlinked file in book path: %s" % f)
                        rel = os.path.normpath(os.path.join(rel_dir, f))
                        dest = _safe_join(files_dir, rel)
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        info = _copy_hashed(src, dest)
                        info["rel"] = rel
                        created.append(info)
                        seen.add(rel)
                missing = sorted(expected - seen)
                if missing:
                    raise RecoveryError("book files missing on disk: %s" % ", ".join(missing))
                if not created:
                    raise RecoveryError("book folder is empty: %s" % book.path)
            else:
                os.makedirs(files_dir, exist_ok=True)
                for name in names:
                    src = _safe_join(src_dir, name)
                    if not os.path.isfile(src) or os.path.islink(src):
                        raise RecoveryError("book file missing: %s" % name)
                    dest = os.path.join(files_dir, name)
                    info = _copy_hashed(src, dest)
                    info["rel"] = name
                    created.append(info)

            book_row = _rows(meta_con, "books", "id", book.id)
            if not book_row:
                raise RecoveryError("book %s missing from metadata.db" % book.id)
            metadata = {
                "books": _encode_rows(book_row),
                "links": {},
                "referenced": {},
                "other_book_tables": {},
                "custom_columns": {"defs": [], "values": {}, "links": {}},
            }
            live_tables = _tables(meta_con)
            for link_table, (ref_table, ref_col, _nk) in LINK_TABLES.items():
                if link_table not in live_tables:
                    continue
                link_rows = _rows(meta_con, link_table, "book", book.id)
                metadata["links"][link_table] = _encode_rows(link_rows)
                ref_ids = [r[ref_col] for r in link_rows if r.get(ref_col) is not None]
                if ref_ids:
                    if ref_table not in live_tables:
                        raise RecoveryError("referenced table %s missing from metadata.db" % ref_table)
                    placeholders = ",".join("?" * len(ref_ids))
                    cols = sorted(_table_columns(meta_con, ref_table))
                    cur = meta_con.execute(
                        "SELECT %s FROM %s WHERE id IN (%s)"
                        % (",".join(_ident(c) for c in cols), _ident(ref_table), placeholders),
                        ref_ids)
                    metadata["referenced"][ref_table] = _encode_rows(
                        [dict(zip(cols, row)) for row in cur.fetchall()])

            for table in BOOK_TABLES[1:]:
                if table in live_tables:
                    metadata["other_book_tables"][table] = _encode_rows(
                        _rows(meta_con, table, "book", book.id))
            for table in sorted(live_tables - set(BOOK_TABLES) - set(LINK_TABLES) - SKIP_TABLES):
                if _CUSTOM_VALUE.match(table) or _CUSTOM_LINK.match(table):
                    continue
                tcols = _table_columns(meta_con, table)
                if "book" in tcols:
                    metadata["other_book_tables"][table] = _encode_rows(
                        _rows(meta_con, table, "book", book.id))

            if "custom_columns" in live_tables:
                defs = _rows(meta_con, "custom_columns")
                metadata["custom_columns"]["defs"] = _encode_rows(defs)
                for definition in defs:
                    cc_id = definition.get("id")
                    value_table = "custom_column_%d" % cc_id
                    link_table = "books_custom_column_%d_link" % cc_id
                    if value_table in live_tables:
                        vcols = _table_columns(meta_con, value_table)
                        if "book" in vcols:
                            metadata["custom_columns"]["values"][value_table] = _encode_rows(
                                _rows(meta_con, value_table, "book", book.id))
                        elif link_table in live_tables:
                            link_rows = _rows(meta_con, link_table, "book", book.id)
                            vids = [r["value"] for r in link_rows if r.get("value") is not None]
                            if vids:
                                cur = meta_con.execute(
                                    "SELECT %s FROM %s WHERE id IN (%s)"
                                    % (",".join(_ident(c) for c in sorted(vcols)),
                                       _ident(value_table), ",".join("?" * len(vids))),
                                    vids)
                                metadata["custom_columns"]["values"][value_table] = _encode_rows(
                                    [dict(zip(sorted(vcols), row)) for row in cur.fetchall()])
                    if link_table in live_tables:
                        metadata["custom_columns"]["links"][link_table] = _encode_rows(
                            _rows(meta_con, link_table, "book", book.id))

            app_data = {}
            app_tables = _tables(app_con)
            for table, spec in APP_TABLES.items():
                if table not in app_tables or spec["book"] not in _table_columns(app_con, table):
                    continue
                if table == "reader_position" and "library_uuid" in _table_columns(app_con, table):
                    cols = _table_columns(app_con, table)
                    rows = [dict(zip(cols, row)) for row in app_con.execute(
                        "SELECT %s FROM reader_position WHERE book_id=? AND library_uuid=?"
                        % ",".join(_ident(c) for c in cols), (book.id, lib_uuid))]
                    app_data[table] = _encode_rows(rows)
                    continue
                app_data[table] = _encode_rows(
                    _rows(app_con, table, spec["book"], book.id))

            manifest = {
                "version": MANIFEST_VERSION,
                "recovery_id": recovery_id,
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "library_uuid": lib_uuid,
                "book_id": book.id,
                "book_uuid": book_row[0].get("uuid"),
                "book_path": book.path,
                "title": book_row[0].get("title"),
                "reason": reason,
                "format": fmt,
                "files": created,
                "metadata": metadata,
                "app": app_data,
                "restored_utc": None,
                "skipped_associations": [],
            }
            manifest_path = os.path.join(stage_dir, MANIFEST_NAME)
            tmp_manifest = manifest_path + ".tmp"
            with open(tmp_manifest, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, sort_keys=True)
            os.replace(tmp_manifest, manifest_path)
        except Exception:
            shutil.rmtree(stage_dir, ignore_errors=True)
            raise
        finally:
            if meta_con is not None:
                meta_con.close()
            if app_con is not None:
                app_con.close()

        os.rename(stage_dir, entry_dir)
        return recovery_id


def _validate_manifest(manifest, entry_dir):
    if not isinstance(manifest, dict):
        raise RecoveryError("manifest is not an object")
    if manifest.get("version") != MANIFEST_VERSION:
        raise RecoveryError("unsupported manifest version")
    rid = manifest.get("recovery_id")
    if not isinstance(rid, str) or not _UUID_HEX.match(rid) or rid != os.path.basename(entry_dir):
        raise RecoveryError("manifest recovery id mismatch")
    try:
        datetime.fromisoformat(manifest["created_utc"])
    except (TypeError, ValueError, KeyError):
        raise RecoveryError("manifest has an invalid created timestamp")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise RecoveryError("manifest has no file list")
    for info in files:
        if not isinstance(info, dict) or not isinstance(info.get("rel"), str) \
                or not isinstance(info.get("sha256"), str) \
                or not _SHA256_HEX.match(info["sha256"]) \
                or not isinstance(info.get("size"), int) or isinstance(info.get("size"), bool):
            raise RecoveryError("manifest file entry is malformed")
    fmt = manifest.get("format")
    if fmt is not None and (not isinstance(fmt, str) or not _IDENT.match(fmt)):
        raise RecoveryError("manifest format is malformed")
    for key in ("book_path", "book_uuid", "reason"):
        if manifest.get(key) is not None and not isinstance(manifest[key], str):
            raise RecoveryError("manifest %s is malformed" % key)
    bid = manifest.get("book_id")
    if not isinstance(bid, int) or isinstance(bid, bool) or bid <= 0:
        raise RecoveryError("manifest book id is malformed")
    meta = manifest.get("metadata")
    if not isinstance(meta, dict) or not isinstance(meta.get("books"), list) \
            or not meta["books"] or not isinstance(meta["books"][0], dict):
        raise RecoveryError("manifest metadata is missing the book row")
    book_row = meta["books"][0]
    if book_row.get("id") != bid:
        raise RecoveryError("manifest book row id does not match the entry")
    if manifest.get("book_path") is not None \
            and book_row.get("path") != manifest["book_path"]:
        raise RecoveryError("manifest book row path does not match the entry")
    if manifest.get("book_uuid") is not None \
            and book_row.get("uuid") != manifest["book_uuid"]:
        raise RecoveryError("manifest book row uuid does not match the entry")
    links = meta.get("links") or {}
    referenced = meta.get("referenced") or {}
    others = meta.get("other_book_tables") or {}
    if not isinstance(links, dict) or not isinstance(referenced, dict) or not isinstance(others, dict):
        raise RecoveryError("manifest metadata is malformed")
    for name in links:
        if name not in LINK_TABLES:
            raise RecoveryError("manifest has unexpected link table %r" % name)
    for name in referenced:
        if name not in {v[0] for v in LINK_TABLES.values()}:
            raise RecoveryError("manifest has unexpected referenced table %r" % name)
    for name in others:
        _ident(name)
    custom = meta.get("custom_columns") or {}
    values = custom.get("values") if isinstance(custom, dict) else None
    links_cc = custom.get("links") if isinstance(custom, dict) else None
    if not isinstance(custom, dict) or not isinstance(custom.get("defs", []), list) \
            or not isinstance(values or {}, dict) or not isinstance(links_cc or {}, dict):
        raise RecoveryError("manifest custom column data is malformed")
    for definition in custom.get("defs") or []:
        if not isinstance(definition, dict):
            raise RecoveryError("manifest custom column definition is malformed")
    for name in list(values or {}) + list(links_cc or {}):
        if not (_CUSTOM_VALUE.match(name) or _CUSTOM_LINK.match(name)):
            raise RecoveryError("manifest has invalid custom table %r" % name)
    app = manifest.get("app") or {}
    if not isinstance(app, dict):
        raise RecoveryError("manifest app data is malformed")
    for name, rows in app.items():
        if name not in APP_TABLES or not isinstance(rows, list):
            raise RecoveryError("manifest has unexpected app table %r" % name)
        for row in rows:
            if not isinstance(row, dict):
                raise RecoveryError("manifest app row is malformed")
    for collection in (links, referenced, others, values or {}, links_cc or {}):
        for rows in collection.values():
            if not isinstance(rows, list):
                raise RecoveryError("manifest table data is malformed")
            for row in rows:
                if not isinstance(row, dict):
                    raise RecoveryError("manifest row is malformed")
    return manifest


def _load_manifest(entry_dir):
    path = os.path.join(entry_dir, MANIFEST_NAME)
    if not os.path.isfile(path) or os.path.islink(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        return _validate_manifest(manifest, entry_dir)
    except (OSError, ValueError, TypeError, AttributeError, RecoveryError):
        return None


def _verify_files(entry_dir, manifest):
    files_dir = os.path.join(entry_dir, "files")
    if os.path.islink(files_dir) or _path_has_symlink(files_dir, os.path.abspath(entry_dir)):
        raise RecoveryError("archived files folder is a symlink")
    for info in manifest["files"]:
        path = _safe_join(files_dir, info["rel"])
        if os.path.islink(path) or not os.path.isfile(path):
            raise RecoveryError("archived file missing: %s" % info["rel"])
        if _sha256(path) != info["sha256"]:
            raise RecoveryError("archived file checksum mismatch: %s" % info["rel"])


def _drop_orphaned_refs(con, metadata):
    """Delete the authors, tags, series and custom values only the removed book used.
    The recovery archive keeps a copy of each by natural key."""
    pairs = [(link_table, ref_table, ref_col, metadata["links"].get(link_table) or [])
             for link_table, (ref_table, ref_col, _nk) in LINK_TABLES.items()]
    for link_table, rows in metadata["custom_columns"]["links"].items():
        value_table = link_table.replace("books_custom_column", "custom_column")[:-5]
        pairs.append((link_table, value_table, "value", rows))
    live_tables = _tables(con)
    for link_table, ref_table, ref_col, rows in pairs:
        if link_table not in live_tables or ref_table not in live_tables:
            continue
        ref_ids = {_decode(row[ref_col]) for row in rows if row.get(ref_col) is not None}
        for ref_id in sorted(ref_ids):
            con.execute(
                "DELETE FROM %s WHERE id=? AND NOT EXISTS (SELECT 1 FROM %s WHERE %s=?)"
                % (_ident(ref_table), _ident(link_table), _ident(ref_col)),
                (ref_id, ref_id))


def _install_exclusive(stage_path, dest, expected_sha256, created_files, created_dirs):
    if os.path.lexists(dest):
        raise RecoveryError("destination file already exists: %s" % dest)
    parent = os.path.dirname(dest)
    missing = []
    probe = parent
    while probe and not os.path.exists(probe):
        missing.append(probe)
        probe = os.path.dirname(probe)
    if missing:
        os.makedirs(parent)
        created_dirs.extend(reversed(missing))
    fd = None
    opened = False
    try:
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        opened = True
        with os.fdopen(fd, "wb") as out:
            fd = None
            with open(stage_path, "rb") as src:
                shutil.copyfileobj(src, out)
        created_files.append(dest)
        if _sha256(dest) != expected_sha256:
            raise RecoveryError("installed file failed verification: %s" % dest)
    except Exception:
        if fd is not None:
            os.close(fd)
        if opened and os.path.isfile(dest) and dest not in created_files:
            try:
                os.remove(dest)
            except OSError:
                pass
        raise


def delete_captured_book(book, book_format="", recovery_id=None):
    """Delete an already-captured book (or one format) atomically.

    Runs inside RECOVERY_LOCK: confirms the live row still matches the capture,
    quarantines the files into a temp dir inside the library, deletes the
    metadata rows (and app-association rows for whole-book deletes) in a single
    attached SQLite transaction, then commits once. On failure the transaction
    is rolled back and quarantined files are moved back without overwriting
    anything that appeared meanwhile."""
    with RECOVERY_LOCK, paused_services():
        if recovery_id is None:
            recovery_id = capture_book(book, book_format, reason='delete')
        entry_dir = os.path.join(recovery_root(), recovery_id)
        manifest = _load_manifest(entry_dir)
        if manifest is None:
            raise RecoveryError("recovery archive %s is incomplete" % recovery_id)
        _verify_files(entry_dir, manifest)

        book_dir = _book_dir(book)
        fmt = (book_format or "").upper()
        # Quarantine beside the book folders so the moves are renames on one filesystem
        quar_dir = tempfile.mkdtemp(prefix=".lily_delete_",
                                    dir=os.path.abspath(config.get_book_path()))
        moved = []
        meta_db = os.path.join(os.path.abspath(config.config_calibre_dir), "metadata.db")
        app_db = os.path.abspath(ub.app_DB_path)
        con = None
        committed = False
        try:
            try:
                from . import calibre_db as _calibre_db
                _calibre_db.session.rollback()
                ub.session.rollback()
            except Exception:
                pass
            con = _connect_meta(meta_db)
            con.execute("ATTACH DATABASE ? AS app_settings", (app_db,))
            con.execute("BEGIN IMMEDIATE")
            live = con.execute("SELECT id, uuid, path FROM books WHERE id=?",
                               (book.id,)).fetchone()
            if not live:
                raise RecoveryError("book %s no longer exists" % book.id)
            live_lib = con.execute("SELECT uuid FROM library_id").fetchone()
            live_uuid = live_lib[0] if live_lib else ""
            if manifest.get("library_uuid") and manifest["library_uuid"] != live_uuid:
                raise RecoveryError("recovery archive belongs to another library")
            if manifest.get("book_id") is not None and manifest["book_id"] != book.id:
                raise RecoveryError("recovery archive is for a different book")
            if manifest.get("book_uuid") and live[1] != manifest["book_uuid"]:
                raise RecoveryError("book %s changed since capture" % book.id)
            if manifest.get("book_path") and live[2] != manifest["book_path"]:
                raise RecoveryError("book %s path changed since capture" % book.id)
            if fmt and (manifest.get("format") or "").upper() != fmt:
                raise RecoveryError(
                    "recovery archive %s does not cover a format-only delete of %s"
                    % (recovery_id, fmt))
            if not fmt and manifest.get("format"):
                raise RecoveryError(
                    "format-only archive %s cannot cover a whole-book delete"
                    % recovery_id)

            for info in manifest["files"]:
                src = _safe_join(book_dir, info["rel"])
                if not os.path.isfile(src) or os.path.islink(src):
                    raise RecoveryError("expected file missing: %s" % info["rel"])
                if os.path.getsize(src) != info["size"] \
                        or _sha256(src) != info["sha256"]:
                    raise RecoveryError("file changed since capture: %s" % info["rel"])

            if fmt:
                for info in manifest["files"]:
                    src = _safe_join(book_dir, info["rel"])
                    dst = _safe_join(quar_dir, info["rel"])
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    os.rename(src, dst)
                    moved.append((dst, src))
            else:
                if not os.path.isdir(book_dir):
                    raise RecoveryError("book folder missing: %s" % book.path)
                captured = {i["rel"] for i in manifest["files"]}
                live_files = set()
                for base, _dirs, names in os.walk(book_dir):
                    for name in names:
                        live_files.add(os.path.relpath(os.path.join(base, name),
                                                       book_dir))
                extra_files = live_files - captured
                if extra_files:
                    raise RecoveryError(
                        "book folder has files not in the recovery archive: %s"
                        % sorted(extra_files))
                dst = os.path.join(quar_dir, "book")
                os.rename(book_dir, dst)
                moved.append((dst, book_dir))

            if fmt:
                con.execute("DELETE FROM data WHERE book=? AND format=?",
                            (book.id, fmt))
            else:
                for table in sorted(_tables(con)):
                    tcols = _table_columns(con, table)
                    if "book" in tcols:
                        con.execute("DELETE FROM %s WHERE book=?" % _ident(table),
                                    (book.id,))
                con.execute("DELETE FROM books WHERE id=?", (book.id,))
                _drop_orphaned_refs(con, manifest["metadata"])
                app_tables = _tables(con, "app_settings.")
                for table, spec in APP_TABLES.items():
                    if table in app_tables and spec["book"] in _table_columns(
                            con, table, "app_settings."):
                        if table == "reader_position" and "library_uuid" in _table_columns(
                                con, table, "app_settings."):
                            con.execute(
                                "DELETE FROM app_settings.reader_position "
                                "WHERE book_id=? AND library_uuid=?",
                                (book.id, live_uuid))
                            continue
                        con.execute(
                            "DELETE FROM app_settings.%s WHERE %s=?"
                            % (_ident(table), _ident(spec["book"])),
                            (book.id,))
            con.execute("COMMIT")
            committed = True
        except Exception as e:
            if con is not None:
                try:
                    con.execute("ROLLBACK")
                except Exception:
                    pass
            conflicts = []
            for quar_path, orig in reversed(moved):
                try:
                    if os.path.lexists(orig):
                        conflicts.append(orig)
                        continue
                    os.makedirs(os.path.dirname(orig), exist_ok=True)
                    os.rename(quar_path, orig)
                    moved.remove((quar_path, orig))
                except OSError as e:
                    conflicts.append("%s (%s)" % (orig, e))
            if conflicts:
                raise RecoveryError(
                    "deletion failed and files could not all be moved back: %s; "
                    "the recovery archive %s still holds full copies"
                    % (", ".join(conflicts), recovery_id))
            raise RecoveryError("book deletion failed: %s" % e)
        finally:
            if con is not None:
                con.close()
            if committed:
                try:
                    shutil.rmtree(quar_dir)
                except OSError as e:
                    log.warning("could not remove delete quarantine %s: %s", quar_dir, e)
                try:
                    if not fmt and os.path.isdir(os.path.dirname(book_dir)):
                        os.rmdir(os.path.dirname(book_dir))
                except OSError:
                    pass
                if not fmt:
                    try:
                        from . import helper
                        helper.clear_cover_thumbnail_cache(book.id)
                    except Exception as e:
                        log.warning("could not clear cover thumbnails for book %s: %s",
                                    book.id, e)
            else:
                try:
                    if os.path.isdir(quar_dir) and not os.listdir(quar_dir):
                        os.rmdir(quar_dir)
                except OSError:
                    pass
        return recovery_id


def get_recovery_retention_days():
    """Configured days to keep recovery entries; 0 keeps them forever."""
    try:
        if '/app/calibre-web-automated/scripts/' not in sys.path:
            sys.path.insert(1, '/app/calibre-web-automated/scripts/')
        from cwa_db import CWA_DB
        with CWA_DB() as cwa_db:
            raw = cwa_db.cwa_settings.get("book_recovery_retention_days", "0")
        days = int(str(raw).strip())
        return days if days >= 0 else 0
    except Exception:
        return 0


def prune_book_recovery(root=None, days=0, now=None):
    """Removes completed recovery entries older than `days` (manifest created_utc).
    days <= 0 keeps everything; anything that is not a fully-formed entry
    (missing manifest, symlink) is left alone. Returns removed entry paths."""
    if days <= 0:
        return []
    with RECOVERY_LOCK, paused_services():
        root = root or recovery_root()
        _check_root(root)
        removed = []
        if not os.path.isdir(root) or os.path.islink(root):
            return removed
        cutoff = (now if now is not None else time.time()) - days * 86400
        for name in os.listdir(root):
            entry = os.path.join(root, name)
            if os.path.islink(entry) or not os.path.isdir(entry):
                continue
            manifest = _load_manifest(entry)
            if manifest is None:
                continue
            try:
                created = datetime.fromisoformat(manifest["created_utc"]).timestamp()
            except (TypeError, ValueError, KeyError):
                continue
            if created < cutoff:
                shutil.rmtree(entry)
                removed.append(entry)
        return removed


def _data_file_path(library_root, book_path, element):
    return os.path.normpath(os.path.join(
        library_root, book_path, element.name + "." + element.format.lower()))


def merge_preflight(target_book, source_books, library_root):
    """Shared check for manual and automatic merges: every file that would land
    on an existing format must be byte-identical (sha256) to the first copy seen
    across the target and all sources, and every file must be readable and
    inside the library. Returns a list of conflict strings (empty = safe)."""
    library_root = os.path.realpath(library_root)
    conflicts = []
    seen = {}
    all_books = [target_book] + list(source_books)
    for book in all_books:
        role = "target" if book is target_book else "book %s" % book.id
        if not getattr(book, "path", None):
            conflicts.append("%s has no library path" % role)
            continue
        book_dir = os.path.normpath(os.path.join(library_root, book.path))
        real_dir = os.path.realpath(book_dir)
        if not real_dir.startswith(library_root + os.sep):
            conflicts.append("%s path escapes the library" % role)
            continue
        if _path_has_symlink(book_dir, library_root):
            conflicts.append("%s path contains a symlink" % role)
            continue
        for element in book.data:
            fmt = (element.format or "").upper()
            src = _data_file_path(library_root, book.path, element)
            real_src = os.path.realpath(src)
            if not real_src.startswith(real_dir + os.sep):
                conflicts.append("%s format %s resolves outside the book folder"
                                 % (role, element.format))
                continue
            if not os.path.isfile(src) or os.path.islink(src):
                conflicts.append("%s format %s is missing on disk" % (role, element.format))
                continue
            if not os.access(src, os.R_OK):
                conflicts.append("%s format %s is not readable" % (role, element.format))
                continue
            first = seen.get(fmt)
            if first is None:
                seen[fmt] = (book, src)
                continue
            first_book, first_src = first
            if _sha256(src) != _sha256(first_src):
                conflicts.append("book %s format %s differs from book %s"
                                 % (book.id, element.format, first_book.id))
    return conflicts
