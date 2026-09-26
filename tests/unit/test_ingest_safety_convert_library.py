# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""convert_library must treat non-zero exit codes from its tools as failures."""

import importlib
import logging
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture
def convert_library(monkeypatch, isolated_script_locks):
    """Import convert_library with its container-only import-time side effects stubbed.

    isolated_script_locks gives it (and kindle_epub_fixer, which it imports) a
    private lock dir and releases their import-time flocks afterwards.
    """
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    import grp
    import pwd

    monkeypatch.setattr(pwd, "getpwnam", lambda name: types.SimpleNamespace(pw_uid=os.getuid()))
    monkeypatch.setattr(grp, "getgrnam", lambda name: types.SimpleNamespace(gr_gid=os.getgid()))
    monkeypatch.setattr(logging, "FileHandler", lambda *a, **k: logging.NullHandler())
    real_run = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))
    real_scandir = os.scandir
    monkeypatch.setattr(os, "scandir", lambda p=".": iter(()) if str(p).startswith("/config") else real_scandir(p))

    module = importlib.import_module("convert_library")

    monkeypatch.setattr(subprocess, "run", real_run)
    monkeypatch.setattr(os, "scandir", real_scandir)
    yield module


class StubDb:
    def __init__(self):
        self.conversions = []
        self.imports = []

    def conversion_add_entry(self, *args):
        self.conversions.append(args)

    def import_add_entry(self, *args):
        self.imports.append(args)


def _tool(bin_dir, name, body):
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body))
    path.chmod(0o755)


def _converter(convert_library, tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    book_dir = tmp_path / "library" / "Author" / "Title (42)"
    book_dir.mkdir(parents=True)
    book = book_dir / "Title - Author.azw3"
    book.write_bytes(b"book")
    conv_dir = tmp_path / "conversion"
    conv_dir.mkdir()

    c = object.__new__(convert_library.LibraryConverter)
    c.verbose = False
    c.current_book = 1
    c.converted_count = 0
    c.failed_books = []
    c.to_convert = [str(book)]
    c.target_format = "mobi"
    c.kindle_epub_fixer = False
    c.cwa_settings = {"auto_backup_conversions": False, "auto_backup_imports": False}
    c.tmp_conversion_dir = str(conv_dir) + "/"
    c.library_dir = str(tmp_path / "library")
    c.calibre_env = dict(os.environ)
    c.db = StubDb()
    c.set_library_permissions = lambda: None
    return c, bin_dir, book


def test_failed_conversion_is_not_reported_as_success(convert_library, tmp_path, monkeypatch, capsys):
    c, bin_dir, book = _converter(convert_library, tmp_path, monkeypatch)
    _tool(bin_dir, "ebook-convert", 'echo "conversion exploded" >&2\nexit 3\n')
    _tool(bin_dir, "calibredb", f'echo called >> "{tmp_path}/calibredb.log"\nexit 0\n')

    c.convert_library()

    out = capsys.readouterr().out
    assert c.failed_books == [str(book)]
    assert c.converted_count == 0
    assert c.db.conversions == []
    assert not (tmp_path / "calibredb.log").exists()
    assert "successful!" not in out
    assert "exited with code 3" in out and "conversion exploded" in out


def test_failed_import_is_counted_as_failure(convert_library, tmp_path, monkeypatch):
    c, bin_dir, book = _converter(convert_library, tmp_path, monkeypatch)
    _tool(bin_dir, "ebook-convert", 'echo converted > "$2"\nexit 0\n')
    _tool(bin_dir, "calibredb", 'echo "database locked" >&2\nexit 1\n')

    c.convert_library()

    assert c.failed_books == [str(book)]
    assert c.converted_count == 0
    assert c.db.imports == []


def test_successful_conversion_and_import(convert_library, tmp_path, monkeypatch):
    c, bin_dir, book = _converter(convert_library, tmp_path, monkeypatch)
    _tool(bin_dir, "ebook-convert", 'echo converted > "$2"\nexit 0\n')
    _tool(bin_dir, "calibredb", "exit 0\n")

    c.convert_library()

    assert c.failed_books == []
    assert c.converted_count == 1
    assert len(c.db.imports) == 1
