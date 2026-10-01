"""Library mirror: incremental copy-only backup of book files."""

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import library_mirror as mod  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture
def lib(tmp_path):
    src = tmp_path / "library"
    (src / "Ann" / "Book (1)").mkdir(parents=True)
    (src / "Ann" / "Book (1)" / "Book.epub").write_bytes(b"epub-bytes")
    (src / "Ann" / "Book (1)" / "cover.jpg").write_bytes(b"jpg")
    (src / "metadata.db").write_bytes(b"live-db")
    (src / ".caltrash").mkdir()
    (src / ".caltrash" / "old.epub").write_bytes(b"trash")
    return src, tmp_path / "mirror"


def _files(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_first_run_copies_books_and_covers_but_not_the_live_db_or_hidden_dirs(lib):
    src, dest = lib
    result = mod.mirror_library(str(src), str(dest))
    assert result["copied"] == 2 and not result["errors"]
    assert _files(dest) == ["Ann/Book (1)/Book.epub", "Ann/Book (1)/cover.jpg"]


def test_second_run_copies_nothing_then_only_what_changed(lib):
    src, dest = lib
    mod.mirror_library(str(src), str(dest))
    assert mod.mirror_library(str(src), str(dest))["copied"] == 0
    book = src / "Ann" / "Book (1)" / "Book.epub"
    book.write_bytes(b"longer epub bytes now")
    (src / "New").mkdir()
    (src / "New" / "n.epub").write_bytes(b"n")
    assert mod.mirror_library(str(src), str(dest))["copied"] == 2
    assert (dest / "Ann" / "Book (1)" / "Book.epub").read_bytes() == b"longer epub bytes now"


def test_newer_source_with_same_size_is_recopied(lib):
    src, dest = lib
    mod.mirror_library(str(src), str(dest))
    cover = src / "Ann" / "Book (1)" / "cover.jpg"
    cover.write_bytes(b"png")  # same size, different content
    future = os.path.getmtime(cover) + 60
    os.utime(cover, (future, future))
    assert mod.mirror_library(str(src), str(dest))["copied"] == 1
    assert (dest / "Ann" / "Book (1)" / "cover.jpg").read_bytes() == b"png"


def test_files_removed_from_the_library_stay_in_the_mirror(lib):
    src, dest = lib
    mod.mirror_library(str(src), str(dest))
    (src / "Ann" / "Book (1)" / "Book.epub").unlink()
    mod.mirror_library(str(src), str(dest))
    assert (dest / "Ann" / "Book (1)" / "Book.epub").exists()


def test_symlinks_and_partial_files_are_skipped(lib):
    src, dest = lib
    (src / "link.epub").symlink_to(src / "Ann" / "Book (1)" / "Book.epub")
    (src / "half.epub.partial").write_bytes(b"x")
    mod.mirror_library(str(src), str(dest))
    assert "link.epub" not in _files(dest) and "half.epub.partial" not in _files(dest)


@pytest.mark.parametrize("make_dest", [
    lambda src, tmp: "relative/path",
    lambda src, tmp: str(src),
    lambda src, tmp: str(src / "inside"),
    lambda src, tmp: str(tmp),  # contains the library
    lambda src, tmp: "",
])
def test_unsafe_destinations_are_refused_and_nothing_is_copied(lib, tmp_path, make_dest):
    src, _ = lib
    with pytest.raises(mod.MirrorError):
        mod.mirror_library(str(src), make_dest(src, tmp_path))
    assert not (src / "inside").exists()


def test_missing_library_is_an_error(tmp_path):
    with pytest.raises(mod.MirrorError):
        mod.mirror_library(str(tmp_path / "nope"), str(tmp_path / "m"))


def test_refuses_when_destination_is_too_small(lib, monkeypatch):
    src, dest = lib
    import collections
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(mod.shutil, "disk_usage", lambda p: usage(10, 10, 0))
    with pytest.raises(mod.MirrorError, match="free space"):
        mod.mirror_library(str(src), str(dest))
    assert _files(dest) == [] if dest.exists() else True


def test_one_unreadable_file_does_not_stop_the_rest(lib, monkeypatch):
    src, dest = lib
    real = mod._copy_atomic

    def flaky(source, target):
        if source.endswith("cover.jpg"):
            raise OSError("disk hiccup")
        real(source, target)

    monkeypatch.setattr(mod, "_copy_atomic", flaky)
    result = mod.mirror_library(str(src), str(dest))
    assert result["copied"] == 1 and list(result["errors"]) == ["Ann/Book (1)/cover.jpg"]


def test_progress_callback_reports_each_file(lib):
    src, dest = lib
    seen = []
    mod.mirror_library(str(src), str(dest), lambda done, total: seen.append((done, total)))
    assert seen == [(1, 2), (2, 2)]


def test_task_mirrors_and_reports_errors(lib, monkeypatch):
    from cps import config
    from cps.services.worker import STAT_FAIL, STAT_FINISH_SUCCESS
    from cps.tasks import library_mirror as task_mod
    src, dest = lib
    monkeypatch.setattr(config, "config_calibre_dir", str(src), raising=False)

    monkeypatch.setattr(task_mod, "get_mirror_dir", lambda: "")
    off = task_mod.TaskMirrorLibrary()
    off.start(None)
    assert off.stat == STAT_FINISH_SUCCESS and not dest.exists()  # not configured: no-op

    monkeypatch.setattr(task_mod, "get_mirror_dir", lambda: str(dest))
    on = task_mod.TaskMirrorLibrary()
    on.start(None)
    assert on.stat == STAT_FINISH_SUCCESS, on.error
    assert (dest / "Ann" / "Book (1)" / "Book.epub").exists()

    monkeypatch.setattr(task_mod, "get_mirror_dir", lambda: str(src / "inside"))
    bad = task_mod.TaskMirrorLibrary()
    bad.start(None)
    assert bad.stat == STAT_FAIL
