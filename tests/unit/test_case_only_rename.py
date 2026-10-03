# SPDX-License-Identifier: GPL-3.0-or-later
"""A title that changes only in case renames the book's folder and files in place. On a
case-insensitive disk (macOS, a colima bind mount) the new spelling already "exists", and
treating it as another file used to delete the book (helper.rename_all_files_on_change)."""

import os
from types import SimpleNamespace

import pytest

from cps import helper


def _book(name, fmt="EPUB"):
    return SimpleNamespace(data=[SimpleNamespace(name=name, format=fmt)])


@pytest.fixture(params=["this disk", "case-insensitive disk"])
def disk(request, monkeypatch):
    """Run on the real temp disk, and once more with the two spellings reported as one entry."""
    if request.param == "case-insensitive disk":
        monkeypatch.setattr(helper, "_same_entry", lambda a, b: a.lower() == b.lower() and a != b)
    return request.param


def test_a_case_only_file_rename_keeps_the_book(tmp_path, disk):
    folder = tmp_path / "Mary Shelley" / "Frankenstein (34)"
    folder.mkdir(parents=True)
    (folder / "Frankenstein; or, the modern prometheus - Mary Shelley.epub").write_bytes(b"epub")
    book = _book("Frankenstein; or, the modern prometheus - Mary Shelley")

    helper.rename_all_files_on_change(book, str(folder), str(folder),
                                      "Frankenstein; or, The Modern Prometheus - Mary Shelley")

    assert os.listdir(folder) == ["Frankenstein; or, The Modern Prometheus - Mary Shelley.epub"]
    assert (folder / "Frankenstein; or, The Modern Prometheus - Mary Shelley.epub").read_bytes() == b"epub"
    assert book.data[0].name == "Frankenstein; or, The Modern Prometheus - Mary Shelley"


def test_a_case_only_folder_rename_moves_the_folder_itself(tmp_path, disk, monkeypatch):
    old = tmp_path / "Mary Shelley" / "Frankenstein; or, the modern prometheus (34)"
    old.mkdir(parents=True)
    (old / "book.epub").write_bytes(b"epub")
    (old / "cover.jpg").write_bytes(b"jpg")
    new_title = "Frankenstein; or, The Modern Prometheus (34)"
    if disk == "case-insensitive disk":
        # The new spelling "exists" already, as it does on a case-insensitive disk
        real_exists = os.path.exists
        monkeypatch.setattr(helper.os.path, "exists",
                            lambda p: real_exists(p) or str(p).lower() == str(tmp_path / "Mary Shelley" / new_title).lower())
    book = SimpleNamespace(id=34, path="Mary Shelley/Frankenstein; or, the modern prometheus (34)")

    error = helper.move_files_on_change(str(tmp_path), "Mary Shelley", new_title, book, "book", None, str(old))

    assert not error
    assert os.listdir(tmp_path / "Mary Shelley") == [new_title]
    assert sorted(os.listdir(tmp_path / "Mary Shelley" / new_title)) == ["book.epub", "cover.jpg"]
    assert book.path == "Mary Shelley/" + new_title
