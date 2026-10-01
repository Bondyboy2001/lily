"""Library mirror: incremental copy-only backup of book files."""

import io
import os
import sys
import zipfile
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import library_mirror as mod  # noqa: E402

pytestmark = pytest.mark.unit


def _epub(text, size=0):
    """A valid zip with one member; `size` pads it with incompressible bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("content.xhtml", text)
        if size:
            zf.writestr("pad.bin", os.urandom(size))
    return buf.getvalue()


@pytest.fixture
def lib(tmp_path):
    src = tmp_path / "library"
    (src / "Ann" / "Book (1)").mkdir(parents=True)
    (src / "Ann" / "Book (1)" / "Book.epub").write_bytes(_epub("epub-bytes"))
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
    book.write_bytes(_epub("longer epub bytes now"))
    (src / "New").mkdir()
    (src / "New" / "n.epub").write_bytes(b"n")
    result = mod.mirror_library(str(src), str(dest))
    assert result["copied"] == 2 and result["versioned"] == 1 and not result["suspicious"]
    assert (dest / "Ann" / "Book (1)" / "Book.epub").read_bytes() == _epub("longer epub bytes now")


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

    def flaky(source, target, before_replace=None):
        if source.endswith("cover.jpg"):
            raise OSError("disk hiccup")
        real(source, target, before_replace)

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


BOOK = "Ann/Book (1)/Book.epub"


def test_replaced_copy_is_kept_under_versions_by_day(lib):
    src, dest = lib
    mod.mirror_library(str(src), str(dest), today=date(2026, 3, 1))
    original = (dest / BOOK).read_bytes()
    (src / BOOK).write_bytes(_epub("second edition"))
    result = mod.mirror_library(str(src), str(dest), today=date(2026, 3, 2))
    assert result["versioned"] == 1
    assert (dest / ".versions" / "2026-03-02" / BOOK).read_bytes() == original
    assert (dest / BOOK).read_bytes() == _epub("second edition")

    # A second change the same day doesn't overwrite the first saved version
    (src / BOOK).write_bytes(_epub("the third edition"))
    mod.mirror_library(str(src), str(dest), today=date(2026, 3, 2))
    assert (dest / ".versions" / "2026-03-02" / BOOK).read_bytes() == original
    assert (dest / ".versions" / "2026-03-02_1" / BOOK).read_bytes() == _epub("second edition")


def test_corrupt_zip_replacement_is_refused_and_reported(lib):
    src, dest = lib
    mod.mirror_library(str(src), str(dest))
    good = (dest / BOOK).read_bytes()
    (src / BOOK).write_bytes(b"PK\x03\x04 this is not really a zip" * 3)
    result = mod.mirror_library(str(src), str(dest))
    assert result["copied"] == 0 and list(result["suspicious"]) == [BOOK]
    assert "zip" in result["suspicious"][BOOK]
    assert (dest / BOOK).read_bytes() == good
    assert not (dest / ".versions").exists()


def test_zip_with_bad_crc_is_refused(lib):
    src, dest = lib
    mod.mirror_library(str(src), str(dest))
    data = bytearray(_epub("x" * 200))
    data[data.index(b"xxxx")] = ord("y")  # flip a stored byte: the CRC no longer matches
    (src / BOOK).write_bytes(bytes(data))
    result = mod.mirror_library(str(src), str(dest))
    assert result["suspicious"][BOOK] == "corrupt zip member content.xhtml"


def test_file_that_lost_more_than_half_its_size_is_refused(lib):
    src, dest = lib
    cover = src / "Ann" / "Book (1)" / "cover.jpg"
    cover.write_bytes(b"j" * 1000)
    mod.mirror_library(str(src), str(dest))
    cover.write_bytes(b"j" * 400)
    result = mod.mirror_library(str(src), str(dest))
    assert result["suspicious"] == {"Ann/Book (1)/cover.jpg": "shrank from 1000 to 400 bytes"}
    assert (dest / "Ann" / "Book (1)" / "cover.jpg").stat().st_size == 1000
    cover.write_bytes(b"j" * 600)  # a smaller but plausible edit goes through
    assert mod.mirror_library(str(src), str(dest))["copied"] == 1


def test_new_files_are_copied_even_if_they_look_damaged(lib):
    src, dest = lib
    (src / "broken.cbz").write_bytes(b"nope")
    result = mod.mirror_library(str(src), str(dest))
    assert (dest / "broken.cbz").exists() and not result["suspicious"]


def test_old_version_folders_are_pruned(lib):
    src, dest = lib
    versions = dest / ".versions"
    for name in ("2026-01-01", "2026-01-01_1", "2026-02-15", "2026-03-01", "notes", "2026-13-01"):
        (versions / name).mkdir(parents=True)
    (versions / "stray.txt").write_text("x")
    (versions / "2025-01-01").symlink_to(versions / "notes")
    result = mod.mirror_library(str(src), str(dest), version_days=30, today=date(2026, 3, 1))
    assert sorted(os.path.basename(p) for p in result["pruned_versions"]) == ["2026-01-01", "2026-01-01_1"]
    assert sorted(os.listdir(versions)) == ["2025-01-01", "2026-02-15", "2026-03-01", "2026-13-01",
                                            "notes", "stray.txt"]
    assert mod.prune_versions(str(dest), 0, date(2030, 1, 1)) == []
    assert mod.prune_versions(str(dest / "nowhere"), 30) == []


@pytest.mark.parametrize("value,expected", [(30, 30), ("7", 7), ("0", 0), (-3, 30), ("x", 30),
                                            (None, 30), (True, 30)])
def test_normalize_version_days(value, expected):
    assert mod.normalize_version_days(value) == expected


def test_task_reports_suspicious_files_as_failure(lib, monkeypatch):
    from cps import config
    from cps.services.worker import STAT_FAIL
    from cps.tasks import library_mirror as task_mod
    src, dest = lib
    monkeypatch.setattr(config, "config_calibre_dir", str(src), raising=False)
    monkeypatch.setattr(task_mod, "get_mirror_dir", lambda: str(dest))
    monkeypatch.setattr(task_mod, "get_version_days", lambda: 30)
    task_mod.TaskMirrorLibrary().start(None)
    (src / BOOK).write_bytes(b"garbage")
    task = task_mod.TaskMirrorLibrary()
    task.start(None)
    assert task.stat == STAT_FAIL and "1 suspicious" in task.error


def test_task_version_days_setting(monkeypatch):
    from cps.tasks import library_mirror as task_mod
    monkeypatch.setattr(task_mod, "_setting", lambda name: "12" if name == "library_mirror_version_days" else "")
    assert task_mod.get_version_days() == 12
    monkeypatch.setattr(task_mod, "_setting", lambda name: "")
    assert task_mod.get_version_days() == 30
