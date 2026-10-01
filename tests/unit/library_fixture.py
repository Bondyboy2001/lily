# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""A temp Calibre library with real book folders, on top of ``lily_env``.

``lily_env`` gives a real metadata.db (a copy of empty_library/metadata.db) but its books
have no files. ``library_env`` adds books the way Calibre lays them out on disk
(``Author/Title (id)/Title - Author.ext``, ``books.path`` and ``data.name`` to match),
copying small files from tests/fixtures/sample_books, and registers every blueprint so
any page renders. Kept apart from lily_env.py so other test helpers don't collide.

    from tests.unit.library_fixture import library_env

    def test_x(tmp_path):
        with library_env(tmp_path) as lib:
            book_id = lib.add_book_with_files("Title", "Author", ["test_minimal_valid.epub"])
            client = lib.admin_client()
"""

import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from tests.unit.lily_env import ADMIN_PASSWORD, lily_env

SAMPLE_BOOKS = Path(__file__).resolve().parents[1] / "fixtures" / "sample_books"


def _calibre_name(text, chars):
    from cps.helper import get_valid_filename
    return get_valid_filename(text, chars=chars)


class LibraryEnv:
    def __init__(self, env):
        self.env = env
        self.app = env.app
        self.library_dir = Path(env.library_dir)

    def __getattr__(self, name):
        return getattr(self.env, name)

    def add_book_with_files(self, title, author, sample_files):
        """Add a book whose folder and files exist on disk; returns the book id.

        ``sample_files`` are names under tests/fixtures/sample_books; each one becomes a
        format of the book (one file per extension)."""
        exts = [Path(name).suffix.lstrip(".").upper() for name in sample_files]
        book_id = self.env.add_book(title, author=author, fmt=None)

        folder = f"{_calibre_name(author, 96)}/{_calibre_name(title, 96)} ({book_id})"
        file_stem = f"{_calibre_name(title, 42)} - {_calibre_name(author, 42)}"
        book_dir = self.library_dir / folder
        book_dir.mkdir(parents=True)
        con = sqlite3.connect(self.library_dir / "metadata.db")
        # Calibre's update trigger on books calls these
        con.create_function("title_sort", 1, lambda t: t)
        con.create_function("uuid4", 0, lambda: str(uuid.uuid4()))
        try:
            con.execute("UPDATE books SET path=? WHERE id=?", (folder, book_id))
            for name, ext in zip(sample_files, exts):
                target = book_dir / f"{file_stem}.{ext.lower()}"
                shutil.copy(SAMPLE_BOOKS / name, target)
                con.execute("INSERT INTO data (book, format, uncompressed_size, name) VALUES (?,?,?,?)",
                            (book_id, ext, target.stat().st_size, file_stem))
            con.commit()
        finally:
            con.close()
        return book_id

    def book_row(self, book_id):
        """(path, [data names]) straight from metadata.db, bypassing the ORM session."""
        con = sqlite3.connect(self.library_dir / "metadata.db")
        try:
            path = con.execute("SELECT path FROM books WHERE id=?", (book_id,)).fetchone()[0]
            names = [r[0] for r in con.execute("SELECT name FROM data WHERE book=? ORDER BY format",
                                               (book_id,))]
        finally:
            con.close()
        return path, names

    def files_on_disk(self):
        """Every file under the library, relative to it (metadata.db excluded)."""
        return sorted(str(p.relative_to(self.library_dir)) for p in self.library_dir.rglob("*")
                      if p.is_file() and not p.name.startswith("metadata.db"))

    def admin_client(self):
        client = self.app.test_client()
        resp = client.post("/login", data={"username": self.env.admin().name, "password": ADMIN_PASSWORD})
        assert resp.status_code in (200, 302)
        return client


@contextmanager
def library_env(tmp_path, **config_overrides):
    """``lily_env`` plus every blueprint and file-backed books (see module docstring)."""
    with lily_env(tmp_path, **config_overrides) as env:
        env.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(env.app)
        yield LibraryEnv(env)
