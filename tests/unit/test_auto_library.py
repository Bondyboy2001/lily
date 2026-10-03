# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""First-start library selection (scripts/auto_library.py): which metadata.db is mounted."""

import json
import os
import sqlite3

import pytest

import auto_library

pytestmark = pytest.mark.unit


@pytest.fixture
def auto(tmp_path):
    a = auto_library.AutoLibrary()
    a.library_dir = str(tmp_path / "library")
    a.config_dir = str(tmp_path / "config")
    a.app_db = str(tmp_path / "config" / "app.db")
    a.dirs_path = str(tmp_path / "dirs.json")
    a.empty_metadb = str(tmp_path / "empty" / "metadata.db")
    os.makedirs(a.library_dir)
    os.makedirs(a.config_dir)
    return a


def _db(path, size=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
    return path


def test_single_library_is_found(auto):
    root = _db(os.path.join(auto.library_dir, "metadata.db"), 10)
    assert auto.check_for_existing_library() is True
    assert auto.metadb_path == root and auto.lib_path == auto.library_dir


def test_largest_of_several_nested_libraries_is_chosen(auto):
    _db(os.path.join(auto.library_dir, "Small", "metadata.db"), 10)
    big = _db(os.path.join(auto.library_dir, "Big", "metadata.db"), 1000)
    assert auto.check_for_existing_library() is True
    assert auto.metadb_path == big


def test_sidecars_backups_and_hidden_folders_are_not_libraries(auto):
    lib = os.path.join(auto.library_dir, "Calibre")
    real = _db(os.path.join(lib, "metadata.db"), 10)
    for name in ("metadata.db-wal", "metadata.db-shm", "metadata.db-journal", "metadata.db.bak"):
        _db(os.path.join(lib, name), 100_000)
    _db(os.path.join(auto.library_dir, ".hidden", "x_1", "metadata.db"), 100_000)
    assert auto.check_for_existing_library() is True
    assert auto.metadb_path == real


def test_a_backup_alone_is_not_a_library(auto):
    _db(os.path.join(auto.library_dir, "metadata.db.bak"), 100)
    assert auto.check_for_existing_library() is False
    assert auto.metadb_path is None


def test_no_library_found(auto):
    _db(os.path.join(auto.library_dir, "Author", "Book (1)", "book.epub"))
    assert auto.check_for_existing_library() is False
    assert auto.metadb_path is None and auto.lib_path is None


def test_new_library_is_created_from_the_empty_one(auto, monkeypatch):
    _db(auto.empty_metadb, 5)
    monkeypatch.setenv("NETWORK_SHARE_MODE", "true")  # no chown in tests
    auto.make_new_library()
    assert auto.metadb_path == os.path.join(auto.library_dir, "metadata.db")
    assert os.path.getsize(auto.metadb_path) == 5


def test_location_is_written_to_dirs_json_and_app_db(auto):
    nested = os.path.join(auto.library_dir, "Calibre")
    _db(os.path.join(nested, "metadata.db"))
    with open(auto.dirs_path, "w") as fh:
        json.dump({"calibre_library_dir": "/calibre-library", "ingest_folder": "/ingest"}, fh)
    con = sqlite3.connect(auto.app_db)
    con.execute("CREATE TABLE settings (config_calibre_dir TEXT)")
    con.execute("INSERT INTO settings VALUES ('/calibre-library')")
    con.commit()
    con.close()

    assert auto.check_for_existing_library() is True
    auto.set_library_location()
    with open(auto.dirs_path) as fh:
        assert json.load(fh) == {"calibre_library_dir": nested, "ingest_folder": "/ingest"}
    con = sqlite3.connect(auto.app_db)
    try:
        assert con.execute("SELECT config_calibre_dir FROM settings").fetchone()[0] == nested
    finally:
        con.close()
