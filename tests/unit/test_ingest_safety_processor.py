# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Ingest sources must never be deleted unless imported or safely in failed/."""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture
def ingest_processor(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    import ingest_processor as module
    return module


@pytest.fixture
def env(ingest_processor, monkeypatch, tmp_path):
    """Isolated ingest folder + failed dir, with heavy runtime pieces stubbed out."""
    ingest_dir = tmp_path / "ingest"
    failed_dir = tmp_path / "processed_books" / "failed"
    ingest_dir.mkdir()
    failed_dir.mkdir(parents=True)
    monkeypatch.setattr(ingest_processor, "backup_destinations", {"failed": str(failed_dir)})
    monkeypatch.setattr(ingest_processor, "initialize_runtime", lambda: True)
    # These tests use tiny stand-in payloads, not real books; the integrity check has its own tests
    real_incomplete_reason = ingest_processor.book_integrity.incomplete_reason
    monkeypatch.setattr(ingest_processor.book_integrity, "incomplete_reason", lambda path: None)

    state = {"import_result": True, "import_calls": [], "ready": True, "db_locked": False,
             "real_incomplete_reason": real_incomplete_reason}
    real_cls = ingest_processor.NewBookProcessor

    def fake_nbp(filepath):
        nbp = object.__new__(real_cls)
        nbp.filepath = filepath
        nbp.filename = os.path.basename(filepath)
        nbp.ingest_folder = os.path.normpath(str(ingest_dir))
        nbp.ingest_ignored_formats = ["crdownload", "download", "part", "uploading", "temp"]
        nbp.supported_book_formats = {"epub", "mobi"}
        nbp.cwa_settings = {"ingest_timeout_minutes": 1}
        nbp.input_format = Path(filepath).suffix[1:].lower()
        nbp.tmp_conversion_dir = str(tmp_path / "conversion") + "/"
        nbp.last_added_book_id = None
        nbp.is_file_in_use = lambda timeout=None: state["ready"]
        nbp.db_locked = False
        nbp.set_library_permissions = lambda: None
        nbp.is_supported_audiobook = lambda: False

        def fake_add(book_path, text=True, format="text"):
            state["import_calls"].append(book_path)
            nbp.db_locked = state["db_locked"]
            result = state["import_result"]
            if isinstance(result, Exception):
                raise result
            return result

        nbp.add_book_to_library = fake_add
        return nbp

    monkeypatch.setattr(ingest_processor, "NewBookProcessor", fake_nbp)
    state["ingest_dir"] = ingest_dir
    state["failed_dir"] = failed_dir
    return state


def _failed_files(env):
    return sorted(p.name for p in env["failed_dir"].iterdir())


def test_successful_import_deletes_source(ingest_processor, env):
    src = env["ingest_dir"] / "book.epub"
    src.write_bytes(b"book")

    assert ingest_processor.main(str(src)) == 0

    assert env["import_calls"] == [str(src)]
    assert not src.exists()
    assert _failed_files(env) == []


def test_failed_import_moves_source_to_failed(ingest_processor, env):
    env["import_result"] = False
    src = env["ingest_dir"] / "book.epub"
    src.write_bytes(b"precious")

    assert ingest_processor.main(str(src)) == 0

    assert not src.exists()
    failed = _failed_files(env)
    assert len(failed) == 1 and failed[0].endswith("_book.epub")
    assert (env["failed_dir"] / failed[0]).read_bytes() == b"precious"


def test_exception_during_import_moves_source_to_failed(ingest_processor, env):
    env["import_result"] = RuntimeError("boom")
    src = env["ingest_dir"] / "book.epub"
    src.write_bytes(b"precious")

    with pytest.raises(RuntimeError):
        ingest_processor.main(str(src))

    assert not src.exists()
    assert len(_failed_files(env)) == 1


def test_unsupported_format_is_preserved_not_deleted(ingest_processor, env):
    src = env["ingest_dir"] / "notes.xyz"
    src.write_bytes(b"data")

    assert ingest_processor.main(str(src)) == 0

    assert env["import_calls"] == []
    assert not src.exists()
    assert [n for n in _failed_files(env) if n.endswith("_notes.xyz")]


def test_repeat_failures_with_same_name_do_not_overwrite(ingest_processor, env):
    env["import_result"] = False
    src = env["ingest_dir"] / "book.epub"
    for payload in (b"first", b"second", b"third"):
        src.write_bytes(payload)
        ingest_processor.main(str(src))

    failed = _failed_files(env)
    assert len(failed) == 3
    contents = sorted((env["failed_dir"] / name).read_bytes() for name in failed)
    assert contents == [b"first", b"second", b"third"]


def test_source_left_in_place_when_failed_dir_unusable(ingest_processor, env, monkeypatch, tmp_path):
    env["import_result"] = False
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    monkeypatch.setattr(ingest_processor, "backup_destinations", {"failed": str(blocker / "failed")})
    src = env["ingest_dir"] / "book.epub"
    src.write_bytes(b"precious")

    assert ingest_processor.main(str(src)) == 0

    assert src.exists() and src.read_bytes() == b"precious"


def test_ignored_temp_file_is_left_alone(ingest_processor, env):
    src = env["ingest_dir"] / "book.epub.part"
    src.write_bytes(b"partial")

    assert ingest_processor.main(str(src)) == 0

    assert src.exists()
    assert _failed_files(env) == []


def test_locked_database_keeps_source_and_reports_busy(ingest_processor, env):
    env["import_result"] = False
    env["db_locked"] = True
    src = env["ingest_dir"] / "book.epub"
    src.write_bytes(b"precious")

    assert ingest_processor.main(str(src)) == ingest_processor.EXIT_BUSY == 2

    assert src.read_bytes() == b"precious"
    assert _failed_files(env) == []


def test_not_ready_file_is_kept_with_distinct_exit_code(ingest_processor, env, capsys):
    env["ready"] = False
    src = env["ingest_dir"] / "book.epub"
    src.write_bytes(b"growing")

    assert ingest_processor.main(str(src)) == ingest_processor.EXIT_NOT_READY == 3

    assert src.exists() and env["import_calls"] == []
    assert _failed_files(env) == []
    assert "kept for retry" in capsys.readouterr().out


def test_truncated_book_is_kept_for_retry_not_imported(ingest_processor, env, monkeypatch, capsys):
    monkeypatch.setattr(ingest_processor.book_integrity, "incomplete_reason", env["real_incomplete_reason"])
    import zipfile
    good = env["ingest_dir"] / "good.epub"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("OEBPS/c.xhtml", "x" * 5000)
    src = env["ingest_dir"] / "cut.epub"
    src.write_bytes(good.read_bytes()[:200])

    assert ingest_processor.main(str(src)) == ingest_processor.EXIT_NOT_READY
    assert src.exists() and env["import_calls"] == [] and _failed_files(env) == []
    assert "not a readable zip yet" in capsys.readouterr().out

    assert ingest_processor.main(str(good)) == 0
    assert env["import_calls"] == [str(good)]


def test_calibredb_database_locked_is_not_a_failed_import(ingest_processor, monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calibredb = bin_dir / "calibredb"
    calibredb.write_text("#!/bin/sh\necho 'apsw.BusyError: BusyError: database is locked' >&2\nexit 1\n")
    calibredb.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    failed_dir = tmp_path / "failed"
    monkeypatch.setattr(ingest_processor, "backup_destinations", {"failed": str(failed_dir)})

    nbp = object.__new__(ingest_processor.NewBookProcessor)
    nbp.cwa_settings = {"auto_ingest_automerge": "ignore", "auto_backup_imports": False}
    nbp.staging_dir = str(tmp_path / "staging")
    os.makedirs(nbp.staging_dir)
    nbp.library_dir = str(tmp_path / "library")
    nbp.metadata_db = str(tmp_path / "library" / "metadata.db")
    nbp.calibre_env = dict(os.environ)
    nbp.db_locked = False
    src = tmp_path / "book.epub"
    src.write_bytes(b"book")

    assert nbp.add_book_to_library(str(src)) is False
    assert nbp.db_locked is True
    assert not failed_dir.exists() or list(failed_dir.iterdir()) == []

    nbp.db_locked = False
    calibredb.write_text("#!/bin/sh\necho 'not an ebook' >&2\nexit 1\n")
    assert nbp.add_book_to_library(str(src)) is False
    assert nbp.db_locked is False
    assert len(list(failed_dir.iterdir())) == 1  # the rejected copy is kept


def test_backup_failed_uses_unique_names(ingest_processor, monkeypatch, tmp_path):
    failed_dir = tmp_path / "failed"
    monkeypatch.setattr(ingest_processor, "backup_destinations", {"failed": str(failed_dir)})
    nbp = object.__new__(ingest_processor.NewBookProcessor)
    src = tmp_path / "book.epub"
    for payload in (b"a", b"b"):
        src.write_bytes(payload)
        nbp.backup(str(src), backup_type="failed")
    assert sorted(p.read_bytes() for p in failed_dir.iterdir()) == [b"a", b"b"]


# ── ProcessLock ────────────────────────────────────────────────────────────


def _lock(ingest_processor, path):
    lock = ingest_processor.ProcessLock()
    lock.lock_path = str(path)
    return lock


def test_two_process_locks_cannot_both_acquire(ingest_processor, tmp_path):
    lock_path = tmp_path / "ingest_processor.lock"
    first = _lock(ingest_processor, lock_path)
    second = _lock(ingest_processor, lock_path)
    try:
        assert first.acquire(timeout=1)
        assert not second.acquire(timeout=0.3)
        # The loser must neither remove the lock file nor clobber the holder's PID
        assert lock_path.exists()
        assert lock_path.read_text() == str(os.getpid())
        # And a third contender still can't get in
        assert not _lock(ingest_processor, lock_path).acquire(timeout=0.2)
    finally:
        first.release()
        second.release()
    # Release never unlinks the lock file; the next process just flocks it again
    assert lock_path.exists()
    third = _lock(ingest_processor, lock_path)
    assert third.acquire(timeout=1)
    third.release()


def test_lock_with_dead_pid_is_acquired_without_unlinking(ingest_processor, tmp_path):
    lock_path = tmp_path / "ingest_processor.lock"
    lock_path.write_text("999999")  # leftover from a crashed run; nobody holds the flock
    inode_before = lock_path.stat().st_ino
    lock = _lock(ingest_processor, lock_path)
    try:
        assert lock.acquire(timeout=1)
        assert lock_path.stat().st_ino == inode_before
        assert lock_path.read_text() == str(os.getpid())
    finally:
        lock.release()


def test_lock_held_by_other_process_blocks_until_it_dies(ingest_processor, tmp_path):
    lock_path = tmp_path / "ingest_processor.lock"
    holder_script = textwrap.dedent(
        f"""
        import sys, time
        sys.path.insert(0, {str(SCRIPTS_DIR)!r})
        import ingest_processor
        lock = ingest_processor.ProcessLock()
        lock.lock_path = {str(lock_path)!r}
        assert lock.acquire(timeout=1)
        print("held", flush=True)
        time.sleep(60)
        """
    )
    holder = subprocess.Popen([sys.executable, "-c", holder_script], stdout=subprocess.DEVNULL)
    try:
        # Wait for the holder's PID to be recorded
        deadline = time.time() + 10
        while time.time() < deadline and (not lock_path.exists() or lock_path.read_text().strip() != str(holder.pid)):
            time.sleep(0.05)
        contender = _lock(ingest_processor, lock_path)
        assert not contender.acquire(timeout=0.3)
        assert lock_path.read_text().strip() == str(holder.pid)
    finally:
        holder.kill()
        holder.wait()
    # flock is released by the kernel when the holder dies
    contender = _lock(ingest_processor, lock_path)
    try:
        assert contender.acquire(timeout=2)
    finally:
        contender.release()
