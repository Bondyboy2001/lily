# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The EPUB fixer must never leave a half-written file in place of the original."""

import os
import stat
import zipfile
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture
def fixer_module(monkeypatch, isolated_script_locks):
    # isolated_script_locks: private lock dir + release of the import-time flock
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    import kindle_epub_fixer
    return kindle_epub_fixer


def _make_fixer(fixer_module):
    fixer = object.__new__(fixer_module.EPUBFixer)
    fixer.files = {
        "mimetype": b"application/epub+zip",
        "OEBPS/content.opf": "<package>new</package>",
    }
    fixer.binary_files = {"OEBPS/cover.jpg": b"\xff\xd8new-image"}
    fixer.file_target_encodings = {}
    return fixer


def _write_original(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("OEBPS/content.opf", "<package>original</package>")
    return path.read_bytes()


def test_write_epub_replaces_file_and_keeps_permissions(fixer_module, tmp_path):
    target = tmp_path / "book.epub"
    _write_original(target)
    os.chmod(target, 0o640)

    _make_fixer(fixer_module).write_epub(target)

    with zipfile.ZipFile(target) as zf:
        assert zf.read("OEBPS/content.opf") == b"<package>new</package>"
        assert zf.namelist()[0] == "mimetype"
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o640
    assert sorted(p.name for p in tmp_path.iterdir()) == ["book.epub"]


def test_write_epub_failure_leaves_original_intact(fixer_module, tmp_path, monkeypatch):
    target = tmp_path / "book.epub"
    original = _write_original(target)

    real_writestr = zipfile.ZipFile.writestr
    calls = {"n": 0}

    def failing_writestr(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:  # fail mid-archive, after some data has been written
            raise OSError("disk full")
        return real_writestr(self, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "writestr", failing_writestr)

    with pytest.raises(OSError):
        _make_fixer(fixer_module).write_epub(str(target))

    assert target.read_bytes() == original
    # No temp files left behind next to the library file
    assert sorted(p.name for p in tmp_path.iterdir()) == ["book.epub"]


def test_write_epub_to_new_path_is_readable(fixer_module, tmp_path):
    target = tmp_path / "out.epub"
    _make_fixer(fixer_module).write_epub(str(target))
    umask = os.umask(0)
    os.umask(umask)
    # Not mkstemp's private 0600: a new file gets the normal umask-derived mode
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o666 & ~umask
    with zipfile.ZipFile(target) as zf:
        assert zf.read("OEBPS/cover.jpg") == b"\xff\xd8new-image"
