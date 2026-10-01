# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Steady-state starts don't walk the whole library or /config: auto_library checks the
usual locations first, and the thumbnail layout migration records "nothing to do"."""

import os

import pytest

import auto_library

pytestmark = pytest.mark.unit


def _no_walk(*args, **kwargs):
    raise AssertionError("walked the directory tree")


@pytest.fixture
def lib(tmp_path):
    auto = auto_library.AutoLibrary()
    auto.library_dir = str(tmp_path / "library")
    auto.config_dir = str(tmp_path / "config")
    os.makedirs(auto.library_dir)
    os.makedirs(auto.config_dir)
    return auto


def test_library_root_metadata_db_is_used_without_walking(lib, monkeypatch):
    open(os.path.join(lib.library_dir, "metadata.db"), "wb").close()
    monkeypatch.setattr(auto_library.os, "walk", _no_walk)
    assert lib.check_for_existing_library() is True
    assert lib.lib_path == lib.library_dir


def test_nested_library_is_still_found(lib):
    nested = os.path.join(lib.library_dir, "Calibre Library")
    os.makedirs(nested)
    open(os.path.join(nested, "metadata.db"), "wb").close()
    assert lib.check_for_existing_library() is True
    assert lib.lib_path == nested


def test_existing_app_db_is_found_without_walking_config(lib, monkeypatch):
    open(os.path.join(lib.config_dir, "app.db"), "wb").close()
    monkeypatch.setattr(auto_library.os, "walk", _no_walk)
    assert lib.check_for_app_db() is None


def test_init_script_only_chowns_what_is_not_owned_by_abc():
    from pathlib import Path
    script = (Path(__file__).resolve().parents[2] / "root/etc/s6-overlay/s6-rc.d/cwa-init/run").read_text()
    assert "chown -R" not in script
    assert '! -user abc -o ! -group abc' in script
    assert "NETWORK_SHARE_MODE=true detected; skipping chown of $d" in script


def test_thumbnail_migration_marks_itself_done_when_nothing_to_migrate(tmp_path, monkeypatch):
    from cps import fs
    from cps.tasks import thumbnail_migration as tm
    monkeypatch.setattr(fs, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(tm, "MIGRATION_MARKER", str(tmp_path / ".cwa_migrations" / "marker"))
    (tmp_path / "thumbnails").mkdir()
    (tmp_path / "thumbnails" / "book_1_r1.webp").write_bytes(b"x")

    assert not tm.get_migration_status()
    tm.check_and_migrate_thumbnails()
    assert tm.get_migration_status()
