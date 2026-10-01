# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Book folders the library database doesn't know about.

Restoring an older metadata.db snapshot leaves the folders of books added since that
snapshot on disk with no database row: they are invisible in Lily but still take space.
``record_after_restore`` lists them (``Author/Title (id)`` folders that hold files and
are not any book's ``path``) and saves the list to ``<config>/lily_unreferenced_folders.json``;
the Trash page shows it and can send those books back through the ingest folder.

No Flask and no cps imports, so the backup task can call it and tests can use it alone.
"""

import json
import os
import sqlite3
import unicodedata
from datetime import datetime, timezone

REPORT_NAME = "lily_unreferenced_folders.json"
# Files Calibre keeps beside the book itself; not worth re-importing on their own
SIDE_FILES = frozenset({"cover.jpg", "metadata.opf"})


def _nfc(path: str) -> str:
    return unicodedata.normalize("NFC", path)


def referenced_paths(metadata_db_path: str) -> set[str] | None:
    """Every books.path in metadata.db, or None when it has no books table."""
    if not os.path.isfile(metadata_db_path):
        return None
    con = sqlite3.connect(metadata_db_path, timeout=30)
    try:
        try:
            rows = con.execute("SELECT path FROM books").fetchall()
        except sqlite3.DatabaseError:
            return None
    finally:
        con.close()
    return {_nfc(r[0]) for r in rows if r[0]}


def book_files(folder: str) -> list[str]:
    """The ebook files in a book folder (no cover, no OPF, no hidden files)."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [n for n in names if not n.startswith(".") and n not in SIDE_FILES
            and os.path.isfile(os.path.join(folder, n))]


def find_unreferenced_folders(books_dir: str, referenced: set[str]) -> list[str]:
    """'Author/Title (id)' folders under books_dir with at least one file that no book
    references. Dot folders (.lily-trash, .caltrash) are skipped."""
    found = []
    try:
        authors = sorted(os.listdir(books_dir))
    except OSError:
        return []
    for author in authors:
        author_dir = os.path.join(books_dir, author)
        if author.startswith(".") or not os.path.isdir(author_dir) or os.path.islink(author_dir):
            continue
        try:
            titles = sorted(os.listdir(author_dir))
        except OSError:
            continue
        for title in titles:
            folder = os.path.join(author_dir, title)
            rel = "%s/%s" % (author, title)
            if title.startswith(".") or not os.path.isdir(folder) or os.path.islink(folder):
                continue
            if _nfc(rel) in referenced:
                continue
            try:
                if any(os.path.isfile(os.path.join(folder, n)) for n in os.listdir(folder)):
                    found.append(rel)
            except OSError:
                continue
    return found


def report_path(config_dir: str) -> str:
    return os.path.join(config_dir, REPORT_NAME)


def load_report(config_dir: str) -> dict:
    try:
        with open(report_path(config_dir), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_report(config_dir: str, folders: list[str], source: str = "") -> None:
    path = report_path(config_dir)
    if not folders:
        clear_report(config_dir)
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"created": datetime.now(timezone.utc).isoformat(), "source": source,
                   "folders": folders}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def clear_report(config_dir: str) -> None:
    try:
        os.remove(report_path(config_dir))
    except FileNotFoundError:
        pass


def record_after_restore(books_dir: str, metadata_db_path: str, config_dir: str, source: str = "") -> list[str]:
    """Called after metadata.db was replaced: lists the unreferenced book folders and
    saves them for the Trash page. Returns the list (empty when nothing is out of step)."""
    referenced = referenced_paths(metadata_db_path)
    if referenced is None or not books_dir or not os.path.isdir(books_dir):
        return []
    folders = find_unreferenced_folders(books_dir, referenced)
    save_report(config_dir, folders, source)
    return folders
