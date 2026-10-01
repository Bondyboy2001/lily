# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Listing, retrying and deleting files the ingest pipeline rejected.

Rejected sources are moved to <processed_books>/failed as '<timestamp>_<name>'.
Retrying moves one back into the ingest folder under its original name so the
watcher picks it up again. Kept free of Flask/cps imports so it can be tested
on its own.
"""

import os
import re
import shutil
from datetime import datetime

FAILED_DIR = "/config/processed_books/failed"
_TIMESTAMP_PREFIX = re.compile(r"^\d{8}_\d{6}_")


def original_name(failed_name: str) -> str:
    """'20260101_030000_Book.epub' -> 'Book.epub'."""
    return _TIMESTAMP_PREFIX.sub("", failed_name, count=1) or failed_name


def _entry_size(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def resolve_failed(failed_dir: str, name: str) -> str:
    """Path of `name` inside failed_dir; refuses anything that is not a direct child."""
    if not name or name != os.path.basename(name) or name in (".", "..") or name.startswith("."):
        raise ValueError(f"Not a failed-file name: {name!r}")
    path = os.path.join(failed_dir, name)
    if not os.path.lexists(path):
        raise FileNotFoundError(name)
    return path


def list_failed(failed_dir: str = FAILED_DIR) -> list[dict]:
    """Rejected files, newest first."""
    try:
        entries = [e for e in os.scandir(failed_dir) if not e.name.startswith(".")]
    except FileNotFoundError:
        return []
    items = []
    for e in entries:
        try:
            mtime = datetime.fromtimestamp(e.stat().st_mtime)
            size = _entry_size(e.path)
        except OSError:
            continue
        items.append({"name": e.name, "original": original_name(e.name), "size": size,
                      "mtime": mtime, "is_dir": e.is_dir()})
    return sorted(items, key=lambda i: i["mtime"], reverse=True)


def _free_path(directory: str, filename: str) -> str:
    stem, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    counter = 1
    while os.path.lexists(candidate):
        candidate = os.path.join(directory, f"{stem}_{counter}{ext}")
        counter += 1
    return candidate


def retry_failed(failed_dir: str, ingest_dir: str, name: str) -> str:
    """Moves a rejected file back into the ingest folder; returns its new path."""
    source = resolve_failed(failed_dir, name)
    destination = _free_path(ingest_dir, original_name(name))
    shutil.move(source, destination)
    return destination


def delete_failed(failed_dir: str, name: str) -> None:
    path = resolve_failed(failed_dir, name)
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)
