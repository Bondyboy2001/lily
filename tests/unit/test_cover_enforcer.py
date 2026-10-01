# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Metadata enforcer: books are only replaced by a checked, fully written copy."""

import json
import os
import sqlite3
import stat
import sys
import tempfile
import textwrap
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"

FAKE_POLISH = textwrap.dedent("""\
    #!{python}
    import json, os, shutil, sys, time, zipfile
    args = sys.argv[1:]
    with open(os.environ["FAKE_POLISH_LOG"], "a") as log:
        log.write(json.dumps(args) + "\\n")
    src, dst = args[-2], args[-1]
    mode = os.environ.get("FAKE_POLISH_MODE", "success")
    if mode == "hang":
        time.sleep(60)
    elif mode == "fail":
        with open(dst, "wb") as fh:
            fh.write(b"half-written")
        sys.stderr.write("Traceback...\\nValueError: boom\\n")
        sys.exit(1)
    elif mode == "truncate":
        data = open(src, "rb").read()
        open(dst, "wb").write(data[: len(data) // 10])
    else:
        with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
            for item in zin.infolist():
                zout.writestr(item, zin.read(item.filename))
            zout.writestr("META-INF/polished.txt", "yes")
""")

# calibredb export --with-library L --to-dir D <id>: writes D/<id>/metadata.opf
FAKE_CALIBREDB = textwrap.dedent("""\
    #!{python}
    import os, sys
    args = sys.argv[1:]
    out = os.path.join(args[args.index("--to-dir") + 1], args[-1])
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, "metadata.opf"), "w").write("<package/>")
""")


@pytest.fixture
def ce(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    lock_path = Path(tempfile.gettempdir()) / "cover_enforcer.lock"
    existed = lock_path.exists()
    import cover_enforcer as module
    # Importing must not take the enforcer lock (restore checks it to see if we're running)
    assert lock_path.exists() == existed
    monkeypatch.setattr(module, "metadata_temp_dir", str(tmp_path / "metadata_temp"))
    dirs_json = tmp_path / "dirs.json"
    dirs_json.write_text(json.dumps({"calibre_library_dir": str(tmp_path / "library")}))
    monkeypatch.setattr(module, "dirs_json", str(dirs_json))
    monkeypatch.setattr(module, "_split_library_cache", None)
    monkeypatch.delenv("CWA_ENFORCER_UPGRADE_BOOK", raising=False)
    return module


@pytest.fixture
def fake_polish(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "ebook-polish"
    script.write_text(FAKE_POLISH.format(python=sys.executable))
    script.chmod(0o755)
    calibredb = bin_dir / "calibredb"
    calibredb.write_text(FAKE_CALIBREDB.format(python=sys.executable))
    calibredb.chmod(0o755)
    log = tmp_path / "polish.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_POLISH_LOG", str(log))

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def mode(value):
        monkeypatch.setenv("FAKE_POLISH_MODE", value)

    return {"calls": calls, "mode": mode}


def make_epub(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("OEBPS/chapter.xhtml", "<html>" + "text " * 2000 + "</html>")
    return path


@pytest.fixture
def book(tmp_path):
    book_dir = tmp_path / "library" / "Jane Doe" / "A Book (7)"
    book_dir.mkdir(parents=True)
    epub = make_epub(book_dir / "A Book - Jane Doe.epub")
    epub.chmod(0o644)
    (book_dir / "cover.jpg").write_bytes(b"jpeg")
    opf = tmp_path / "metadata_temp" / "export.opf"
    opf.parent.mkdir()
    opf.write_text("<package/>")
    return {"dir": book_dir, "epub": epub, "opf": opf, "original": epub.read_bytes()}


def leftovers(book_dir: Path) -> list[str]:
    return sorted(p.name for p in book_dir.iterdir() if ".lily-polish-" in p.name)


# ── polish_in_place ─────────────────────────────────────────────────────────


def test_success_replaces_book_via_temp_file(ce, fake_polish, book):
    fake_polish["mode"]("success")
    assert ce.polish_in_place(str(book["epub"]), str(book["opf"]), str(book["dir"] / "cover.jpg")) is None

    with zipfile.ZipFile(book["epub"]) as zf:
        assert zf.read("META-INF/polished.txt") == b"yes"
    assert leftovers(book["dir"]) == []
    assert stat.S_IMODE(book["epub"].stat().st_mode) == 0o644  # not mkstemp's 0600

    (args,) = fake_polish["calls"]()
    assert args[:4] == ["-c", str(book["dir"] / "cover.jpg"), "-o", str(book["opf"])]
    assert "-U" not in args
    assert args[-2] == str(book["epub"])
    out = Path(args[-1])
    assert out != book["epub"] and out.parent == book["dir"] and out.suffix == ".epub"


def test_upgrade_flag_is_opt_in(ce, fake_polish, book, monkeypatch):
    fake_polish["mode"]("success")
    monkeypatch.setenv("CWA_ENFORCER_UPGRADE_BOOK", "true")
    assert ce.polish_in_place(str(book["epub"]), str(book["opf"])) is None
    (args,) = fake_polish["calls"]()
    assert "-U" in args and "-c" not in args


@pytest.mark.parametrize("mode,expected", [
    ("fail", "exited with code 1: ValueError: boom"),
    ("truncate", "output rejected"),
])
def test_failed_or_damaged_output_leaves_original_untouched(ce, fake_polish, book, mode, expected):
    fake_polish["mode"](mode)
    error = ce.polish_in_place(str(book["epub"]), str(book["opf"]))
    assert error is not None and expected in error
    assert book["epub"].read_bytes() == book["original"]
    assert leftovers(book["dir"]) == []


def test_hanging_polish_times_out_and_leaves_original(ce, fake_polish, book):
    fake_polish["mode"]("hang")
    error = ce.polish_in_place(str(book["epub"]), str(book["opf"]), timeout=1)
    assert error == "ebook-polish timed out after 1s"
    assert book["epub"].read_bytes() == book["original"]
    assert leftovers(book["dir"]) == []


def test_missing_ebook_polish_is_a_failure(ce, book, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    error = ce.polish_in_place(str(book["epub"]), str(book["opf"]))
    assert error and error.startswith("error while polishing")
    assert book["epub"].read_bytes() == book["original"]
    assert leftovers(book["dir"]) == []


def test_timeout_scales_with_size_and_is_capped(ce):
    mb = 1024 * 1024
    assert ce.polish_timeout(0) == 120
    assert ce.polish_timeout(100 * mb) == 420
    assert ce.polish_timeout(10_000 * mb) == ce.POLISH_MAX_TIMEOUT


# ── Enforcer: reporting and recording ───────────────────────────────────────


class FakeDB:
    def __init__(self):
        self.rows = []

    def enforce_add_entry_from_log(self, log_info, trigger_type="auto -log"):
        self.rows.append((trigger_type, log_info["book_id"], log_info["file_path"]))

    def enforce_add_entry_from_dir(self, book_dicts):
        self.rows += [("manual -dir", b["book_id"], b["file_path"]) for b in book_dicts]

    def enforce_add_entry_from_all(self, book_dicts):
        self.rows += [("manual -all", b["book_id"], b["file_path"]) for b in book_dicts]


def make_enforcer(ce, library):
    enforcer = object.__new__(ce.Enforcer)
    enforcer.db = FakeDB()
    enforcer.args = None
    enforcer.supported_formats = ["epub", "azw3"]
    enforcer.calibre_library = str(library)
    enforcer.split_library = None
    enforcer.unicode_filename = False
    return enforcer


@pytest.mark.parametrize("mode", ["success", "fail"])
def test_enforce_cover_prints_done_only_on_success(ce, fake_polish, book, tmp_path, capsys, mode):
    fake_polish["mode"](mode)
    enforcer = make_enforcer(ce, tmp_path / "library")
    (result,) = enforcer.enforce_cover(str(book["dir"]))
    out = capsys.readouterr().out
    if mode == "success":
        assert result.enforce_error is None
        assert "DONE: 'A Book - Jane Doe.epub'" in out and "FAILED" not in out
    else:
        assert "boom" in result.enforce_error
        assert "DONE" not in out and "FAILED: 'A Book - Jane Doe.epub' left unchanged" in out
        assert book["epub"].read_bytes() == book["original"]


def test_results_are_recorded_as_success_or_failure(ce, tmp_path):
    enforcer = make_enforcer(ce, tmp_path)

    class B:
        def __init__(self, path, error):
            self.file_path, self.enforce_error = path, error
            self.timestamp, self.book_id, self.book_title, self.author_name = "t", "7", "T", "A"
            self.log_info = None

        def export_as_dict(self):
            return {"book_id": self.book_id, "file_path": self.file_path}

    books = [B("/l/ok.epub", None), B("/l/bad.azw3", "boom")]
    assert enforcer.record_book_results(books, "manual -all") == 1
    assert enforcer.record_book_results(books, "manual -dir") == 1
    log_info = {"timestamp": "t", "book_id": "7", "title": "T", "authors": "A"}
    assert enforcer.record_book_results(books, "auto -log", log_info=log_info) == 1
    assert "file_path" not in log_info  # the caller's dict is not mutated
    assert enforcer.db.rows == [
        ("manual -all", "7", "/l/ok.epub"), ("manual -all (failed)", "7", "/l/bad.azw3"),
        ("manual -dir", "7", "/l/ok.epub"), ("manual -dir (failed)", "7", "/l/bad.azw3"),
        ("auto -log", "7", "/l/ok.epub"), ("auto -log (failed)", "7", "/l/bad.azw3"),
    ]


def test_enforce_all_counts_failed_files(ce, fake_polish, book, tmp_path):
    fake_polish["mode"]("fail")
    enforcer = make_enforcer(ce, tmp_path / "library")
    n_ok, _elapsed, n_files = enforcer.enforce_all_covers()
    assert (n_ok, n_files) == (0, 1)
    assert enforcer.db.rows == [("manual -all (failed)", "7", str(book["epub"]))]


def test_temp_files_are_ignored_and_stale_ones_removed(ce, fake_polish, book, tmp_path):
    fake_polish["mode"]("success")
    stale = book["dir"] / ".A Book - Jane Doe.lily-polish-abc123.epub"
    stale.write_bytes(b"junk")
    enforcer = make_enforcer(ce, tmp_path / "library")
    assert enforcer.get_supported_files_from_dir(str(book["dir"])) == [str(book["epub"])]
    (result,) = enforcer.enforce_cover(str(book["dir"]))
    assert result.enforce_error is None
    assert not stale.exists()
    assert len(fake_polish["calls"]()) == 1


# ── Lock ────────────────────────────────────────────────────────────────────


def test_lock_is_taken_by_main_and_blocks_a_second_instance(ce, monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    registered = []
    monkeypatch.setattr(ce.atexit, "register", registered.append)
    ce.acquire_lock()
    assert (tmp_path / "cover_enforcer.lock").exists() and registered == [ce.removeLock]
    with pytest.raises(SystemExit) as exc:
        ce.acquire_lock()
    assert exc.value.code == 2
    ce.removeLock()
    assert not (tmp_path / "cover_enforcer.lock").exists()
    ce.removeLock()  # already gone: no error


# ── Book folder resolution ──────────────────────────────────────────────────


def _library(tmp_path, db_path=None):
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    with sqlite3.connect(library / "metadata.db") as con:
        con.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, path TEXT)")
        if db_path:
            con.execute("INSERT INTO books VALUES (7, ?)", (db_path,))
    return library


def _book_dir(library, rel, with_epub=True):
    d = library / rel
    d.mkdir(parents=True)
    if with_epub:
        (d / "x.epub").write_bytes(b"x")
    return d


LOG = {"book_id": "7", "title": "A Book", "authors": "Jane Doe"}


def test_book_dir_prefers_db_path(ce, tmp_path):
    library = _library(tmp_path, "Renamed Author/A Book (7)")
    _book_dir(library, "Another/A Book (7)")  # stale sibling, also found by the (id) search
    db_dir = _book_dir(library, "Renamed Author/A Book (7)")
    enforcer = make_enforcer(ce, library)
    log = dict(LOG)
    assert enforcer.get_book_dir_from_log(log) == str(db_dir) + os.sep
    assert log["file_path"] == str(db_dir) + os.sep


def test_book_dir_found_by_id_suffix_when_db_path_missing(ce, tmp_path):
    library = _library(tmp_path)
    found = _book_dir(library, "Someone Else/Old Title (7)")
    _book_dir(library, "Someone Else/Other (17)")
    enforcer = make_enforcer(ce, library)
    assert enforcer.get_book_dir_from_log(dict(LOG)) == str(found) + os.sep


def test_book_dir_prefers_one_with_supported_files(ce, tmp_path):
    library = _library(tmp_path, "Jane Doe/A Book (7)")
    _book_dir(library, "Jane Doe/A Book (7)", with_epub=False)
    with_files = _book_dir(library, "J Doe/A Book (7)")
    enforcer = make_enforcer(ce, library)
    assert enforcer.get_book_dir_from_log(dict(LOG)) == str(with_files) + os.sep


def test_book_dir_falls_back_to_reconstructed_path(ce, tmp_path):
    library = _library(tmp_path)
    enforcer = make_enforcer(ce, library)
    expected = os.path.join(str(library), "Jane Doe", "A Book (7)") + os.sep
    assert enforcer.get_book_dir_from_log(dict(LOG)) == expected
