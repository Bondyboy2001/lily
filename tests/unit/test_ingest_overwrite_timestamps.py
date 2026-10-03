# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Overwrite-merge imports bump the date added of the merged book only, never of books
that were merely edited recently."""

import sqlite3
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"

OLD = "2020-01-01 00:00:00+00:00"
EDITED = "2999-01-01 12:00:00+00:00"


@pytest.fixture
def ip(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    monkeypatch.setenv("CWA_INGEST_BATCH_DIRTY_FILE", str(tmp_path / "batch_dirty"))
    import ingest_processor as module
    monkeypatch.setattr(module, "mark_ingest_batch_dirty", lambda: None)
    return module


def _processor(ip, tmp_path):
    metadata_db = tmp_path / "metadata.db"
    con = sqlite3.connect(metadata_db)
    con.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, timestamp TEXT, last_modified TEXT)")
    # 1: the book the import merges into; 2: a book edited in the web app a moment ago;
    # 3: untouched
    con.executemany("INSERT INTO books VALUES (?, ?, ?)",
                    [(1, OLD, EDITED), (2, OLD, EDITED), (3, OLD, OLD)])
    con.commit()
    con.close()

    staging = tmp_path / "staging"
    staging.mkdir()
    nbp = object.__new__(ip.NewBookProcessor)
    nbp.metadata_db = str(metadata_db)
    nbp.staging_dir = str(staging)
    nbp.library_dir = str(tmp_path / "library")
    nbp.calibre_env = {}
    nbp.cwa_settings = {"auto_ingest_automerge": "overwrite", "auto_backup_imports": False}
    nbp.last_added_book_id = None
    nbp.last_added_book_ids = []
    nbp.failure_reason = ""
    nbp.fetch_metadata_if_enabled = lambda *a, **k: None
    nbp.tidy_authors = lambda ids: None
    nbp.tidy_tags = lambda ids: None
    nbp.centre_covers = lambda ids: None
    nbp._register_title_sort_function = lambda con: True
    return nbp


def _timestamps(tmp_path):
    con = sqlite3.connect(tmp_path / "metadata.db")
    try:
        return dict(con.execute("SELECT id, timestamp FROM books").fetchall())
    finally:
        con.close()


def test_overwrite_merge_only_touches_the_merged_book(ip, tmp_path, monkeypatch):
    nbp = _processor(ip, tmp_path)
    monkeypatch.setattr(ip.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 0, stdout="Merged book ids: 1\n", stderr=""))
    source = tmp_path / "Book.pdf"
    source.write_bytes(b"pdf")

    assert nbp.add_book_to_library(str(source)) is True
    stamps = _timestamps(tmp_path)
    assert stamps[2] == OLD and stamps[3] == OLD  # the edited book keeps its date added
    assert stamps[1] == EDITED  # the merged one reflects the import


def test_added_and_merged_ids_are_both_parsed(ip):
    out = "Added book ids: 4, 5\nMerged book ids: 1\n"
    assert ip.NewBookProcessor._parse_added_book_ids(out) == [4, 5, 1]
    assert ip.NewBookProcessor._parse_added_book_ids("Merged book id: 9") == [9]
    assert ip.NewBookProcessor._parse_added_book_ids("nothing here") == []
