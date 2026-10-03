# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Metadata enforcer: its lock can't outlive a killed run, and each book's metadata is
exported into a folder of its own."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"

# calibredb export --with-library L --to-dir D <id>: writes D/<id>/metadata.opf
FAKE_CALIBREDB = textwrap.dedent("""\
    #!{python}
    import os, sys
    args = sys.argv[1:]
    if os.environ.get("FAKE_CALIBREDB_FAIL"):
        sys.stderr.write("calibredb: boom\\n")
        sys.exit(1)
    out = os.path.join(args[args.index("--to-dir") + 1], args[-1])
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, "metadata.opf"), "w").write('<package book="%s"/>' % args[-1])
""")

# ebook-polish [-c cover] -o opf -U src dst: records its arguments
FAKE_POLISH = textwrap.dedent("""\
    #!{python}
    import json, os, sys
    with open(os.environ["FAKE_POLISH_LOG"], "a") as log:
        log.write(json.dumps(sys.argv[1:]) + "\\n")
""")


@pytest.fixture
def ce(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.delitem(sys.modules, "cover_enforcer", raising=False)
    import cover_enforcer as module
    # Importing must not take the enforcer lock (deletes and merges check it)
    assert not (tmp_path / "cover_enforcer.lock").exists()
    monkeypatch.setattr(module, "metadata_temp_dir", str(tmp_path / "metadata_temp"))
    dirs_json = tmp_path / "dirs.json"
    dirs_json.write_text(json.dumps({"calibre_library_dir": str(tmp_path / "library")}))
    monkeypatch.setattr(module, "dirs_json", str(dirs_json))
    monkeypatch.setattr(module, "_split_library_cache", None)
    monkeypatch.setattr(module, "_export_settle_done", True)
    registered = []
    monkeypatch.setattr(module.atexit, "register", registered.append)
    module.registered = registered
    yield module
    module.removeLock()


@pytest.fixture
def tools(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, source in (("calibredb", FAKE_CALIBREDB), ("ebook-polish", FAKE_POLISH)):
        script = bin_dir / name
        script.write_text(source.format(python=sys.executable))
        script.chmod(0o755)
    log = tmp_path / "polish.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_POLISH_LOG", str(log))
    return lambda: [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.fixture
def book(tmp_path):
    book_dir = tmp_path / "library" / "Jane Doe" / "A Book (7)"
    book_dir.mkdir(parents=True)
    epub = book_dir / "A Book - Jane Doe.epub"
    epub.write_bytes(b"epub")
    (book_dir / "cover.jpg").write_bytes(b"jpeg")
    return {"dir": book_dir, "epub": epub}


def make_enforcer(ce):
    enforcer = object.__new__(ce.Enforcer)
    enforcer.supported_formats = ["epub", "azw3"]
    return enforcer


# ── Lock ────────────────────────────────────────────────────────────────────


HOLD_LOCK = textwrap.dedent("""\
    import fcntl, sys, time
    fh = open(sys.argv[1], "a+")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    print("locked", flush=True)
    time.sleep(float(sys.argv[2]))
""")


def _hold_lock_in_other_process(path, seconds):
    proc = subprocess.Popen([sys.executable, "-c", HOLD_LOCK, str(path), str(seconds)],
                            stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "locked"
    return proc


def test_lock_is_taken_by_main_and_blocks_a_second_instance(ce, tmp_path):
    lock = tmp_path / "cover_enforcer.lock"
    ce.acquire_lock()
    assert lock.read_text() == str(os.getpid())
    assert ce.registered == [ce.removeLock]
    holder = None
    try:
        # A second process can't take it
        holder = subprocess.run(
            [sys.executable, "-c",
             "import fcntl,sys; fh=open(sys.argv[1],'a+'); "
             "fcntl.flock(fh.fileno(), fcntl.LOCK_EX|fcntl.LOCK_NB)", str(lock)],
            capture_output=True)
        assert holder.returncode != 0
    finally:
        ce.removeLock()
    ce.removeLock()  # already released: no error
    ce.acquire_lock()  # free again


@pytest.mark.parametrize("content", ["", "999999999"])
def test_lock_left_by_a_killed_run_does_not_block(ce, tmp_path, content):
    """The old lock was "the file exists": a killed run blocked every later edit."""
    lock = tmp_path / "cover_enforcer.lock"
    lock.write_text(content)
    ce.acquire_lock()
    assert lock.read_text() == str(os.getpid())


def test_killed_holder_releases_the_lock(ce, tmp_path):
    holder = _hold_lock_in_other_process(tmp_path / "cover_enforcer.lock", 60)
    try:
        with pytest.raises(SystemExit) as exc:
            ce.acquire_lock()
        assert exc.value.code == 2
    finally:
        holder.kill()
        holder.wait()
    ce.acquire_lock()


def test_log_run_waits_for_a_running_enforcer(ce, tmp_path):
    lock = tmp_path / "cover_enforcer.lock"
    holder = _hold_lock_in_other_process(lock, 1.5)
    try:
        ce.acquire_lock(wait_seconds=30)
    finally:
        holder.kill()
        holder.wait()
    assert lock.read_text() == str(os.getpid())


def test_manual_run_processes_logs_queued_while_it_held_the_lock(ce):
    enforcer = make_enforcer(ce)
    calls = []
    enforcer.check_for_other_logs = lambda: calls.append(1)
    enforcer.enforcer_on = False
    enforcer.process_queued_logs()
    assert calls == []
    enforcer.enforcer_on = True
    enforcer.process_queued_logs()
    assert calls == [1]


def test_service_pause_takes_the_same_lock(ce, tmp_path, monkeypatch):
    from cps.tasks import restore
    ce.acquire_lock()
    with pytest.raises(RuntimeError, match="cover enforcer"):
        restore.acquire_service_locks()
    ce.removeLock()
    handles = restore.acquire_service_locks()
    restore.release_service_locks(handles)
    # Releasing leaves the file in place but holds nothing
    assert (tmp_path / "cover_enforcer.lock").exists()
    ce.acquire_lock()


# ── Metadata export ─────────────────────────────────────────────────────────


def test_export_uses_a_fresh_folder_and_ignores_leftover_opfs(ce, tools, book, tmp_path):
    temp = tmp_path / "metadata_temp"
    # A killed run left another book's export behind (it sorts first, too)
    stale = temp / "0" / "metadata.opf"
    stale.parent.mkdir(parents=True)
    stale.write_text('<package book="99"/>')

    result = ce.Book(str(book["dir"]), str(book["epub"]))
    opf = Path(result.new_metadata_path)
    assert opf.read_text() == '<package book="7"/>'
    assert opf.parent.parent.parent == temp and opf.parent.parent.name.startswith("book-7-")


def test_failed_export_removes_its_folder(ce, tools, book, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CALIBREDB_FAIL", "1")
    with pytest.raises(subprocess.CalledProcessError):
        ce.Book(str(book["dir"]), str(book["epub"]))
    assert list((tmp_path / "metadata_temp").iterdir()) == []


def test_enforce_cover_writes_the_books_own_metadata_and_cleans_up(ce, tools, book, tmp_path):
    (tmp_path / "metadata_temp").mkdir()
    (tmp_path / "metadata_temp" / "leftover.opf").write_text('<package book="99"/>')
    (result,) = make_enforcer(ce).enforce_cover(str(book["dir"]))
    assert (book["dir"] / "metadata.opf").read_text() == '<package book="7"/>'
    (args,) = tools()
    assert args[args.index("-o") + 1] == result.new_metadata_path
    assert list((tmp_path / "metadata_temp").iterdir()) == []


def test_empty_metadata_temp_clears_leftovers(ce, tmp_path):
    temp = tmp_path / "metadata_temp"
    (temp / "book-3-x").mkdir(parents=True)
    (temp / "book-3-x" / "metadata.opf").write_text("old")
    (temp / "loose.opf").write_text("old")
    make_enforcer(ce).empty_metadata_temp()
    assert list(temp.iterdir()) == []
