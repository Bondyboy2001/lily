# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Editing a book's title or author renames its folder and files on disk and updates
books.path / data.name to match (editbooks.do_edit_book -> helper.update_dir_structure)."""

import pytest

from tests.unit.library_fixture import library_env

pytestmark = pytest.mark.unit


@pytest.fixture
def lib(tmp_path, monkeypatch):
    # Edits queue a duplicate scan and touch cwa.db; keep that inside tmp_path.
    monkeypatch.setenv("CWA_DB_PATH", str(tmp_path / "cwa"))
    with library_env(tmp_path) as lib:
        yield lib


def _edit(client, book_id, title, authors):
    form = {"title": title, "authors": authors, "tags": "", "series": "", "series_index": "1",
            "comments": "", "publisher": "", "languages": "", "pubdate": "", "rating": "",
            "detail_view": "1"}
    return client.post(f"/admin/book/{book_id}", data=form)


def test_title_change_renames_folder_and_files(lib):
    book_id = lib.add_book_with_files("Old Title", "Jane Doe", ["test_minimal_valid.epub", "metamorphosis.txt"])
    assert lib.files_on_disk() == [f"Jane Doe/Old Title ({book_id})/Old Title - Jane Doe.epub",
                                   f"Jane Doe/Old Title ({book_id})/Old Title - Jane Doe.txt"]

    resp = _edit(lib.admin_client(), book_id, "New Title", "Jane Doe")
    assert resp.status_code == 302

    path, names = lib.book_row(book_id)
    assert path == f"Jane Doe/New Title ({book_id})"
    assert names == ["New Title - Jane Doe", "New Title - Jane Doe"]
    assert lib.files_on_disk() == [f"Jane Doe/New Title ({book_id})/New Title - Jane Doe.epub",
                                   f"Jane Doe/New Title ({book_id})/New Title - Jane Doe.txt"]


def test_author_change_moves_book_to_new_author_folder(lib):
    book_id = lib.add_book_with_files("Some Book", "Jane Doe", ["test_minimal_valid.epub"])

    resp = _edit(lib.admin_client(), book_id, "Some Book", "John Roe")
    assert resp.status_code == 302

    path, names = lib.book_row(book_id)
    assert path == f"John Roe/Some Book ({book_id})"
    assert names == ["Some Book - John Roe"]
    assert lib.files_on_disk() == [f"John Roe/Some Book ({book_id})/Some Book - John Roe.epub"]
    # The emptied author folder is cleaned up
    assert not (lib.library_dir / "Jane Doe").exists()


def test_author_change_keeps_other_books_of_the_old_author(lib):
    moved = lib.add_book_with_files("First", "Jane Doe", ["test_minimal_valid.epub"])
    stays = lib.add_book_with_files("Second", "Jane Doe", ["metamorphosis.txt"])

    assert _edit(lib.admin_client(), moved, "First", "John Roe").status_code == 302

    assert lib.book_row(moved)[0] == f"John Roe/First ({moved})"
    assert lib.book_row(stays)[0] == f"Jane Doe/Second ({stays})"
    assert lib.files_on_disk() == [f"Jane Doe/Second ({stays})/Second - Jane Doe.txt",
                                   f"John Roe/First ({moved})/First - John Roe.epub"]


def test_unchanged_title_and_author_leave_files_alone(lib):
    book_id = lib.add_book_with_files("Stable", "Jane Doe", ["test_minimal_valid.epub"])
    before = lib.files_on_disk()

    assert _edit(lib.admin_client(), book_id, "Stable", "Jane Doe").status_code == 302

    assert lib.book_row(book_id)[0] == f"Jane Doe/Stable ({book_id})"
    assert lib.files_on_disk() == before
