# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Automatic metadata enforcement: writes a book's current metadata and cover back into its EPUB/AZW3 files."""

import argparse
import atexit
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import book_integrity
from cwa_db import CWA_DB
try:
    from cps.utils.filename_sanitizer import get_valid_filename_shared
except ModuleNotFoundError:
    # Add project root (parent of scripts/) to sys.path and retry
    this_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(this_dir, '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    try:
        from cps.utils.filename_sanitizer import get_valid_filename_shared  # type: ignore
    except Exception:
        # Inline fallback: minimal mirror of CW behavior used only if import fails
        import re as _re
        try:
            import unidecode as _unidecode  # type: ignore
        except Exception:
            _unidecode = None

        _ZW_TRIM_RE = _re.compile(r"(^[\s\u200B-\u200D\ufeff]+)|([\s\u200B-\u200D\ufeff]+$)")

        def _strip_ws(text: str) -> str:
            return _ZW_TRIM_RE.sub("", text)

        def get_valid_filename_shared(value: str,
                                       replace_whitespace: bool = True,
                                       chars: int = 128,
                                       unicode_filename: bool = False) -> str:
            if not isinstance(value, str):
                value = str(value) if value is not None else ""
            if value[-1:] == '.':
                value = value[:-1] + '_'
            value = value.replace("/", "_").replace(":", "_").strip('\0')
            if unicode_filename and _unidecode is not None:
                value = _unidecode.unidecode(value)
            if replace_whitespace:
                value = _re.sub(r'[*+:\\\"/<>?]+', '_', value, flags=_re.U)
                value = _re.sub(r'[|]+', ',', value, flags=_re.U)
            value = _strip_ws(value.encode('utf-8')[:chars].decode('utf-8', errors='ignore'))
            if not value:
                raise ValueError("Filename cannot be empty")
            return value
try:
    from unidecode import unidecode  # transliteration used when unicode-filename mode is on
except Exception:
    unidecode = None

# Global Variables
dirs_json = "/app/calibre-web-automated/dirs.json"
change_logs_dir = "/app/calibre-web-automated/metadata_change_logs"
metadata_temp_dir = "/app/calibre-web-automated/metadata_temp"


LOCK_NAME = 'cover_enforcer.lock'


def acquire_lock() -> None:
    """Creates the lock file, or exits with code 2 if one already exists (another instance
    is running). The file's mere existence is the lock: cps/tasks/restore.py relies on that.
    Taken in main() rather than at import so the module can be imported (e.g. by tests)."""
    lock_path = os.path.join(tempfile.gettempdir(), LOCK_NAME)
    try:
        lock = open(lock_path, 'x')
        lock.close()
    except FileExistsError:
        print("[cover-metadata-enforcer]: CANCELLING... cover-metadata-enforcer was initiated but is already running")
        sys.exit(2)
    # Removed again when the script exits
    atexit.register(removeLock)


def removeLock():
    try:
        os.remove(os.path.join(tempfile.gettempdir(), LOCK_NAME))
    except FileNotFoundError:
        pass


# ── Rewriting book files safely ────────────────────────────────────────────
# ebook-polish writes to a temp file next to the book (same filesystem, so the final
# os.replace is atomic); the original is only replaced once the output looks sane.
POLISH_TEMP_MARKER = '.lily-polish-'
POLISH_BASE_TIMEOUT = 120        # seconds for any book...
POLISH_SECONDS_PER_MB = 3        # ...plus this much per MB of book...
POLISH_MAX_TIMEOUT = 1800        # ...capped here
# Output smaller than this fraction of the original is treated as damaged. Replacing a
# very large embedded cover can legitimately shrink a book, so this is deliberately loose.
POLISH_MIN_SIZE_RATIO = 0.5


def polish_timeout(size_bytes: int) -> int:
    """Seconds ebook-polish may take for a book of the given size."""
    mb = max(0, size_bytes) / (1024 * 1024)
    return int(min(POLISH_MAX_TIMEOUT, POLISH_BASE_TIMEOUT + POLISH_SECONDS_PER_MB * mb))


def upgrade_book_enabled() -> bool:
    """ebook-polish -U (e.g. EPUB 2 -> EPUB 3) rewrites a book's internals, so it is opt-in."""
    return os.environ.get('CWA_ENFORCER_UPGRADE_BOOK', '').strip().lower() in ('1', 'true', 'yes', 'on')


def is_polish_temp(path: str) -> bool:
    return POLISH_TEMP_MARKER in os.path.basename(path)


def remove_stale_polish_temps(directory: str) -> None:
    """Deletes temp files left by an enforcer run that was killed mid-polish.
    Only one enforcer runs at a time (lock above), so any that exist are stale."""
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if POLISH_TEMP_MARKER in name:
            try:
                os.remove(os.path.join(directory, name))
                print(f"[cover-metadata-enforcer] Removed leftover temp file {name}", flush=True)
            except OSError:
                pass


def polish_in_place(file: str, metadata_path: str, cover_path: str | None = None,
                    timeout: int | None = None) -> str | None:
    """Embeds the metadata (and cover, if given) into `file` with ebook-polish.

    Returns None once the book has been replaced, or the reason it was not. On any
    failure, timeout or suspicious output the temp file is deleted and the original
    is left exactly as it was."""
    directory, name = os.path.split(file)
    stem, ext = os.path.splitext(name)
    try:
        fd, tmp = tempfile.mkstemp(prefix=f".{stem[:40]}{POLISH_TEMP_MARKER}", suffix=ext, dir=directory)
        os.close(fd)
    except OSError as e:
        return f"could not create a temp file next to the book: {e}"
    replaced = False
    try:
        if timeout is None:
            timeout = polish_timeout(os.path.getsize(file))
        cmd = ['ebook-polish']
        if cover_path:
            cmd += ['-c', cover_path]
        cmd += ['-o', metadata_path]
        if upgrade_book_enabled():
            cmd.append('-U')
        cmd += [file, tmp]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return f"ebook-polish timed out after {timeout}s"
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip()
            last_line = detail.splitlines()[-1] if detail else 'no output'
            return f"ebook-polish exited with code {result.returncode}: {last_line}"
        problem = book_integrity.polished_output_problem(file, tmp, POLISH_MIN_SIZE_RATIO)
        if problem:
            return f"ebook-polish output rejected: {problem}"
        try:
            shutil.copymode(file, tmp)  # mkstemp creates 0600; keep the book's permissions
        except OSError:
            pass
        os.replace(tmp, file)
        replaced = True
        return None
    except OSError as e:
        return f"error while polishing: {e}"
    finally:
        if not replaced:
            try:
                os.remove(tmp)
            except OSError:
                pass


# Split-library settings from app.db, read once per process (see Book.get_split_library)
_SPLIT_LIBRARY_UNSET = object()
_split_library_cache = _SPLIT_LIBRARY_UNSET
# The settle delay before calibredb export only guards the change that triggered this run
# (the web app's DB write), so it is only needed before the first export in the process.
_export_settle_done = False


class Book:
    def __init__(self, book_dir: str, file_path: str, new_metadata_path: str | None = None):
        """new_metadata_path: an OPF already exported for this book (same book_id) to reuse
        instead of running calibredb export again, e.g. for the book's other formats."""
        self.book_dir: str = book_dir
        self.file_path: str = file_path

        self.calibre_library = self.get_calibre_library()

        self.file_format: str = Path(file_path).suffix.replace('.', '')
        self.timestamp: str = self.get_time()
        self.book_id: str = (list(re.findall(r"\(\d*\)", book_dir))[-1])[1:-1]
        self.book_title, self.author_name, self.title_author = self.get_title_and_author()

        self.calibre_env = os.environ.copy()
        # Enables Calibre plugins to be used from /config/plugins
        self.calibre_env["HOME"] = "/config"
        # Gets split library info from app.db and sets library dir to the split dir if split library is enabled
        self.split_library = self.get_split_library()
        if self.split_library:
            self.calibre_library = self.split_library["split_path"]
            self.calibre_env['CALIBRE_OVERRIDE_DATABASE_PATH'] = os.path.join(self.split_library["db_path"], "metadata.db")

        self.cover_path = book_dir + '/cover.jpg'
        self.old_metadata_path = book_dir + '/metadata.opf'
        self.new_metadata_path = new_metadata_path or self.get_new_metadata_path()

        self.log_info = None
        # Why the file could not be rewritten (None once it has been)
        self.enforce_error: str | None = None


    def get_split_library(self) -> dict[str, str] | None:
        """Checks whether or not the user has split library enabled. Returns None if they don't and the path of the Split Library location if True.

        Read from app.db once per process (a Book is built per file, so a full-library run would
        otherwise open app.db thousands of times); a copy is returned so callers can't mutate the cache."""
        global _split_library_cache
        if _split_library_cache is _SPLIT_LIBRARY_UNSET:
            con = sqlite3.connect("/config/app.db", timeout=60)
            try:
                cur = con.cursor()
                split_library = cur.execute('SELECT config_calibre_split FROM settings;').fetchone()[0]
                if split_library:
                    split_path = cur.execute('SELECT config_calibre_split_dir FROM settings;').fetchone()[0]
                    db_path = cur.execute('SELECT config_calibre_dir FROM settings;').fetchone()[0]
                    _split_library_cache = {
                        "split_path": split_path,
                        "db_path": db_path
                    }
                else:
                    _split_library_cache = None
            finally:
                con.close()
        return dict(_split_library_cache) if _split_library_cache else None

    def get_calibre_library(self) -> str:
        """Gets Calibre-Library location from dirs.json"""
        with open(dirs_json, 'r') as f:
            dirs = json.load(f)
        return dirs['calibre_library_dir'] # Returns without / on the end


    def get_time(self) -> str:
        now = datetime.now()
        return now.strftime('%Y-%m-%d %H:%M:%S')


    def get_title_and_author(self) -> tuple[str, str, str]:
        title_author = self.file_path.split('/')[-1].split(f'.{self.file_format}')[0]
        book_title = title_author.split(f" - {title_author.split(' - ')[-1]}")[0]
        author_name = title_author.split(' - ')[-1]

        return book_title, author_name, title_author


    def get_new_metadata_path(self) -> str:
        """Uses the export function of the calibredb utility to export any new metadata for the given book to metadata_temp, and returns the path to the new metadata.opf"""
        # Add retry logic with exponential backoff to handle database locks
        global _export_settle_done
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Add small delay before first attempt to allow other operations to complete
                if attempt > 0:
                    delay = 2 ** attempt  # Exponential backoff: 2s, 4s
                    print(f"[cover-metadata-enforcer] Retrying calibredb export (attempt {attempt + 1}/{max_retries}) after {delay}s delay...", flush=True)
                    time.sleep(delay)
                elif not _export_settle_done:
                    # Small initial delay so the web app's write that triggered this run can settle.
                    # Only needed once per process: later exports (other books in a full-library run)
                    # follow our own already-exited subprocesses, and "database is locked" is retried above.
                    _export_settle_done = True
                    time.sleep(0.5)

                result = subprocess.run(
                    ["calibredb", "export", "--with-library", self.calibre_library, "--to-dir", metadata_temp_dir, self.book_id],
                    env=self.calibre_env, check=False, capture_output=True, text=True, timeout=60
                )

                if result.returncode == 0:
                    temp_files = [os.path.join(dirpath,f) for (dirpath, dirnames, filenames) in os.walk(metadata_temp_dir) for f in filenames]
                    opf_files = [f for f in temp_files if f.endswith('.opf')]
                    if opf_files:
                        return opf_files[0]
                    else:
                        raise FileNotFoundError("No .opf file found after calibredb export")
                else:
                    if attempt < max_retries - 1 and "database is locked" in result.stderr.lower():
                        continue  # Retry on database lock
                    else:
                        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
            except subprocess.TimeoutExpired:
                if attempt < max_retries - 1:
                    continue
                else:
                    raise

        # If all retries failed
        raise RuntimeError(f"Failed to export metadata for book {self.book_id} after {max_retries} attempts")


    def export_as_dict(self) -> dict[str,str | None]:
        return {
            "book_dir":self.book_dir,
            "file_path":self.file_path,
            "calibre_library":self.calibre_library,
            "file_format":self.file_format,
            "timestamp":self.timestamp,
            "book_id":self.book_id,
            "book_title":self.book_title,
            "author_name":self.author_name,
            "title_author":self.title_author,
            "cover_path":self.cover_path,
            "old_metadata_path":self.old_metadata_path,
            "new_metadata_path":self.new_metadata_path,
            "log_info":self.log_info
        }


class Enforcer:
    def __init__(self, args):
        self.db = CWA_DB()
        self.cwa_settings = self.db.cwa_settings
        self.enforcer_on = self.cwa_settings["auto_metadata_enforcement"]
        self.supported_formats = ["epub", "azw3"]

        self.args = args
        self.calibre_library = self.get_calibre_library()

        self.illegal_characters = ["<", ">", ":", '"', "/", "\\", "|", "?", "*"]

        self.calibre_env = os.environ.copy()
        # Enables Calibre plugins to be used from /config/plugins
        self.calibre_env["HOME"] = "/config"
        # Gets split library info from app.db and sets library dir to the split dir if split library is enabled
        self.split_library = self.get_split_library()
        if self.split_library:
            self.calibre_library = self.split_library["split_path"]
            self.calibre_env['CALIBRE_OVERRIDE_DATABASE_PATH'] = os.path.join(self.split_library["db_path"], "metadata.db")

        # Read Calibre-Web setting: config_unicode_filename (True -> transliterate non-English in filenames)
        try:
            with sqlite3.connect("/config/app.db", timeout=60) as con:
                cur = con.cursor()
                self.unicode_filename = bool(cur.execute('SELECT config_unicode_filename FROM settings;').fetchone()[0])
        except Exception:
            self.unicode_filename = False

    def get_split_library(self) -> dict[str, str] | None:
        """Checks whether or not the user has split library enabled. Returns None if they don't and the path of the Split Library location if True."""
        con = sqlite3.connect("/config/app.db", timeout=60)
        cur = con.cursor()
        split_library = cur.execute('SELECT config_calibre_split FROM settings;').fetchone()[0]

        if split_library:
            split_path = cur.execute('SELECT config_calibre_split_dir FROM settings;').fetchone()[0]
            db_path = cur.execute('SELECT config_calibre_dir FROM settings;').fetchone()[0]
            con.close()
            return {
                "split_path": split_path,
                "db_path": db_path
            }
        else:
            con.close()
            return None


    def get_calibre_library(self) -> str:
        with open(dirs_json, 'r') as f:
            dirs = json.load(f)
        return dirs['calibre_library_dir'] # Returns without / on the end


    def read_log(self, auto=True, log_path: str = "None") -> dict:
        """Reads pertinent information from the given log file, adds the book_id from the log name and returns the info as a dict.
        Returns None if the file doesn't exist after retries (handles race conditions)."""
        if auto:
            file_path = f'{change_logs_dir}/{self.args.log}'
            book_id = (self.args.log.split('-')[1]).split('.')[0]
            timestamp_raw = self.args.log.split('-')[0]
        else:
            file_path = log_path
            log_name = os.path.basename(log_path)
            book_id = (log_name.split('-')[1]).split('.')[0]
            timestamp_raw = log_name.split('-')[0]

        try:
            timestamp = datetime.strptime(timestamp_raw, '%Y%m%d%H%M%S')
        except ValueError as e:
            print(f"[cover-metadata-enforcer] ERROR: Invalid timestamp format in log filename: {e}", flush=True)
            return None

        # Retry logic to handle race conditions where file is detected but not yet fully written
        max_retries = 3
        retry_delay = 0.5  # seconds

        for attempt in range(max_retries):
            try:
                # Check if file exists first
                if not os.path.exists(file_path):
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                    else:
                        print(f"[cover-metadata-enforcer] WARNING: Log file '{os.path.basename(file_path)}' not found after {max_retries} attempts. "
                              f"This may be due to a race condition or the file was already processed and deleted.", flush=True)
                        return None

                # Try to read the file
                with open(file_path, 'r', encoding='utf-8') as f:
                    log_info = json.load(f)

                log_info['book_id'] = book_id
                log_info['timestamp'] = timestamp.strftime('%Y-%m-%d %H:%M:%S')
                return log_info

            except FileNotFoundError:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                else:
                    print(f"[cover-metadata-enforcer] WARNING: Log file '{os.path.basename(file_path)}' not found after {max_retries} attempts. "
                          f"This may be due to a race condition or the file was already processed and deleted.", flush=True)
                    return None
            except json.JSONDecodeError as e:
                if attempt < max_retries - 1:
                    # File might still be being written
                    time.sleep(retry_delay)
                    continue
                else:
                    print(f"[cover-metadata-enforcer] ERROR: Failed to parse log file '{os.path.basename(file_path)}': {e}", flush=True)
                    return None
            except Exception as e:
                print(f"[cover-metadata-enforcer] ERROR: Unexpected error reading log file '{os.path.basename(file_path)}': {e}", flush=True)
                return None

        return None


    def get_book_dir_from_log(self, log_info: dict) -> str:
        """Resolve the on-disk book directory prioritizing ones that contain supported files.
        Order of preference: DB path -> any (id)-suffix dirs -> reconstructed ASCII/raw (based on config).
        Within each, prefer the one that actually contains EPUB/AZW3. When config_unicode_filename is True,
        prefer the ASCII path over a diacritic sibling if both exist."""
        book_id = str(log_info['book_id']).strip()

        candidate_dirs: list[str] = []

        # 1) DB-based resolution (split-library aware)
        try:
            metadb_path = os.path.join(
                (self.split_library or {}).get("db_path", self.calibre_library),
                "metadata.db",
            )
            con = sqlite3.connect(metadb_path, timeout=60)
            try:
                cur = con.cursor()
                row = cur.execute('SELECT path FROM books WHERE id = ?', (book_id,)).fetchone()
            finally:
                con.close()
            if row and row[0]:
                resolved = os.path.join(self.calibre_library, row[0])
                resolved = resolved if resolved.endswith(os.sep) else resolved + os.sep
                if os.path.isdir(resolved):
                    candidate_dirs.append(resolved)
                    if self.args and getattr(self.args, 'verbose', False):
                        print(f"[cover-metadata-enforcer] Candidate from DB: {resolved}", flush=True)
        except Exception as e:
            if self.args and getattr(self.args, 'verbose', False):
                print(f"[cover-metadata-enforcer] WARN: DB lookup failed for id={book_id}: {e}", flush=True)

        # 2) All directories that end with (book_id)
        target_suffix = f"({book_id})"
        try:
            for dirpath, dirnames, _ in os.walk(self.calibre_library):
                for d in dirnames:
                    if d.endswith(target_suffix):
                        p = os.path.join(dirpath, d)
                        p = p if p.endswith(os.sep) else p + os.sep
                        if os.path.isdir(p):
                            candidate_dirs.append(p)
            if self.args and getattr(self.args, 'verbose', False):
                if candidate_dirs:
                    print(f"[cover-metadata-enforcer] Found {len(candidate_dirs)} candidate(s) including DB/ID-search", flush=True)
        except Exception:
            pass

        # 3) Reconstruct from log names using EXACT CW sanitization
        raw_title = str(log_info.get('title', '')).strip()
        # CW uses only the first author to build the folder
        raw_author_full = str(log_info.get('authors', '')).strip().replace(' & ', ', ')
        raw_author = raw_author_full.split(', ')[0] if ', ' in raw_author_full else raw_author_full

        # Build both transliterated and non-transliterated variants using shared sanitizer
        # Guard against empty/invalid values to avoid crashing on fresh/partial metadata
        try:
            title_ascii = get_valid_filename_shared(raw_title, chars=96, unicode_filename=True)
        except Exception:
            # Fallback: minimal safe title using book id
            title_ascii = f"book_{book_id}"
        try:
            author_ascii = get_valid_filename_shared(raw_author, chars=96, unicode_filename=True)
        except Exception:
            author_ascii = "Unknown Author"
        try:
            title_raw = get_valid_filename_shared(raw_title, chars=96, unicode_filename=False)
        except Exception:
            title_raw = f"book_{book_id}"
        try:
            author_raw = get_valid_filename_shared(raw_author, chars=96, unicode_filename=False)
        except Exception:
            author_raw = "Unknown Author"

        reconstructed_ascii = os.path.join(self.calibre_library, author_ascii, f"{title_ascii} ({book_id})")
        reconstructed_raw = os.path.join(self.calibre_library, author_raw, f"{title_raw} ({book_id})")
        # Prefer ASCII first when config demands transliteration
        recon_order = [reconstructed_ascii, reconstructed_raw] if self.unicode_filename else [reconstructed_raw, reconstructed_ascii]
        candidate_dirs.extend([(p if p.endswith(os.sep) else p + os.sep) for p in recon_order])

        # Deduplicate while preserving order
        seen = set()
        deduped_candidates = []
        for c in candidate_dirs:
            if c not in seen:
                seen.add(c)
                deduped_candidates.append(c)

        # Split into preferred vs alternate based on config_unicode_filename
        def is_preferred(path: str) -> bool:
            base = author_ascii if self.unicode_filename else author_raw
            return path.startswith(os.path.join(self.calibre_library, base) + os.sep)

        preferred_candidates = [c for c in deduped_candidates if is_preferred(c)]
        alternate_candidates = [c for c in deduped_candidates if not is_preferred(c)]

        # Choose the first candidate that exists and contains supported files (preferred first)
        for group_name, group in (("preferred", preferred_candidates), ("alternate", alternate_candidates)):
            for c in group:
                if os.path.isdir(c):
                    sf = self.get_supported_files_from_dir(c)
                    if sf:
                        if self.args and getattr(self.args, 'verbose', False):
                            print(f"[cover-metadata-enforcer] Selected {group_name} candidate with supported files: {c}", flush=True)
                        log_info['file_path'] = c
                        return c

        # If none have supported files, but some dirs exist, choose best available (prefer ASCII if exists)
        existing_pref = [c for c in preferred_candidates if os.path.isdir(c)]
        existing_alt = [c for c in alternate_candidates if os.path.isdir(c)]
        existing = existing_pref or existing_alt
        if existing:
            # Try to pick ASCII-looking path if config is True
            preferred = None
            for c in existing_pref:
                preferred = c
                break
            if not preferred:
                preferred = existing[0]
            if self.args and getattr(self.args, 'verbose', False):
                print(f"[cover-metadata-enforcer] No supported files in candidates; falling back to existing dir: {preferred}", flush=True)
            log_info['file_path'] = preferred
            return preferred

        # Nothing exists; fall back to reconstructed path that matches config
        fallback = (reconstructed_ascii if self.unicode_filename else reconstructed_raw)
        fallback = fallback if fallback.endswith(os.sep) else fallback + os.sep
        if self.args and getattr(self.args, 'verbose', False):
            print(f"[cover-metadata-enforcer] Resolved via reconstructed path (not found on disk): {fallback}", flush=True)
        log_info['file_path'] = fallback
        return fallback


    def get_supported_files_from_dir(self, dir: str) -> list[str]:
        """ Returns a list if the book dir given contains files of one or more of the supported formats"""
        library_files = [os.path.join(dirpath, f) for (dirpath, dirnames, filenames) in os.walk(dir) for f in filenames]

        supported_files = []
        for format in self.supported_formats:
            supported_files += [f for f in library_files if f.lower().endswith(f'.{format}') and not is_polish_temp(f)]

        return supported_files

    def enforce_cover(self, book_dir: str) -> list:
        """Will force the Cover & Metadata to update for the supported book files in the given directory.
        Returns one Book per file; check book.enforce_error to see whether it was rewritten."""
        remove_stale_polish_temps(book_dir)
        supported_files = self.get_supported_files_from_dir(book_dir)
        if supported_files:
            if len(supported_files) > 1:
                print("[cover-metadata-enforcer] Multiple file formats for current book detected...", flush=True)
            book_objects = []
            # All formats share the book's metadata (same book_id), so export it once and reuse the
            # OPF for every format; metadata_temp is emptied once all formats are done.
            shared_metadata_path = None
            try:
                for file in supported_files:
                    book = self._enforce_file(book_dir, file, shared_metadata_path)
                    shared_metadata_path = book.new_metadata_path
                    book_objects.append(book)
            finally:
                self.empty_metadata_temp()

            return book_objects
        else:
            print(f"[cover-metadata-enforcer]: No supported file formats found in {book_dir}.", flush=True)
            print("[cover-metadata-enforcer]: *** NOTICE **** Only EPUB & AZW3 formats are currently supported.", flush=True)
            return []


    def _enforce_file(self, book_dir: str, file: str, new_metadata_path: str | None = None) -> "Book":
        """Polish one book file with the book's exported metadata and cover (exports it unless given)."""
        book = Book(book_dir, file, new_metadata_path)
        self.replace_old_metadata(book.old_metadata_path, book.new_metadata_path)

        # No settle delay needed here: the calibredb export and the metadata copy above have
        # already finished (subprocess exited, file closed), so nothing still holds the files.
        cover = book.cover_path if Path(book.cover_path).exists() else None
        book.enforce_error = polish_in_place(file, book.new_metadata_path, cover)

        if book.enforce_error is None:
            print(f"[cover-metadata-enforcer]: DONE: '{book.title_author}.{book.file_format}': Cover & Metadata updated", flush=True)
        else:
            print(f"[cover-metadata-enforcer]: FAILED: '{book.title_author}.{book.file_format}' left unchanged: {book.enforce_error}", flush=True)

        return book


    def enforce_all_covers(self) -> tuple[int, float, int] | tuple[bool, bool, bool]:
        """Will force the covers and metadata to be re-generated for all books in the library"""
        t_start = time.time()

        supported_files = self.get_supported_files_from_dir(self.calibre_library)
        if supported_files:
            # One entry per book dir (a multi-format book used to be enforced once per format,
            # and enforce_cover already handles every format in the dir)
            files_per_dir: dict[str, int] = {}
            for file in supported_files:
                book_dir = os.path.dirname(file)
                files_per_dir[book_dir] = files_per_dir.get(book_dir, 0) + 1
            book_dirs = list(files_per_dir)

            print(f"[cover-metadata-enforcer]: {len(book_dirs)} books detected in Library")
            print(f"[cover-metadata-enforcer]: Enforcing covers for {len(supported_files)} supported file(s) in {self.calibre_library} ...")

            successful_enforcements = len(supported_files)

            for book_dir in book_dirs:
                try:
                    book_objects = self.enforce_cover(book_dir)
                    if book_objects:
                        failed = self.record_book_results(book_objects, 'manual -all')
                        successful_enforcements -= failed
                except Exception as e:
                    print(f"[cover-metadata-enforcer]: ERROR: {book_dir}")
                    print(f"[cover-metadata-enforcer]: Skipping book due to following error: {e}")
                    successful_enforcements = successful_enforcements - files_per_dir[book_dir]
                    continue

            t_end = time.time()

            return successful_enforcements, (t_end - t_start), len(supported_files)
        else: # No supported files found
            return False, False, False


    def replace_old_metadata(self, old_metadata: str, new_metadata: str) -> None:
        """Switches the metadata in metadata_temp with the metadata in the Calibre-Library"""
        # Never shell out here: the paths contain book titles/authors, which
        # can carry shell metacharacters such as $() or backticks.
        shutil.copyfile(new_metadata, old_metadata)


    def print_library_list(self) -> None:
        """Uses the calibredb command line utility to list the books in the library"""
        subprocess.run(["calibredb", "list", "--with-library", self.calibre_library], env=self.calibre_env, check=True)


    def delete_log(self, auto=True, log_path="None"):
        """Deletes the log file"""
        try:
            if auto:
                log = os.path.join(change_logs_dir, self.args.log)
                os.remove(log)
            else:
                os.remove(log_path)
        except FileNotFoundError:
            # Log may already be removed by another process or cleanup path
            return


    def _parse_log_filename(self, log_path: str):
        """Return (book_id, timestamp) from a log filename, or (None, None) if invalid."""
        log_name = os.path.basename(log_path)
        try:
            timestamp_raw = log_name.split('-')[0]
            book_id = (log_name.split('-')[1]).split('.')[0]
            timestamp = datetime.strptime(timestamp_raw, '%Y%m%d%H%M%S')
            return book_id, timestamp
        except Exception:
            return None, None


    def select_latest_log_for_book(self, book_id: str, current_log_path: str | None = None) -> str | None:
        """Select the latest log file for a given book_id; optionally prefer current_log_path if newest."""
        log_files = [os.path.join(dirpath, f)
                     for (dirpath, _, filenames) in os.walk(change_logs_dir)
                     for f in filenames if f.endswith('.json')]
        latest_path = None
        latest_ts = None
        for log in log_files:
            parsed_book_id, ts = self._parse_log_filename(log)
            if parsed_book_id != str(book_id) or ts is None:
                continue
            if latest_ts is None or ts > latest_ts:
                latest_ts = ts
                latest_path = log
        if latest_path is None:
            return current_log_path
        if current_log_path and os.path.abspath(current_log_path) == os.path.abspath(latest_path):
            return current_log_path
        return latest_path


    def record_book_results(self, book_objects: list, trigger: str, log_info: dict | None = None) -> int:
        """Records each rewritten file as an enforcement under `trigger` and each file that
        could not be rewritten as '<trigger> (failed)'. Returns the number of failures."""
        succeeded = [b for b in book_objects if b.enforce_error is None]
        failed = [b for b in book_objects if b.enforce_error is not None]
        if log_info is not None:
            for book in succeeded:
                book.log_info = {**log_info, 'file_path': book.file_path}
                self.db.enforce_add_entry_from_log(book.log_info)
            for book in failed:
                book.log_info = {**log_info, 'file_path': book.file_path}
                self.record_failed_enforcement(book.log_info, book.enforce_error)
            return len(failed)
        if succeeded:
            book_dicts = [book.export_as_dict() for book in succeeded]
            if trigger == 'manual -dir':
                self.db.enforce_add_entry_from_dir(book_dicts)
            else:
                self.db.enforce_add_entry_from_all(book_dicts)
        for book in failed:
            info = {'timestamp': book.timestamp, 'book_id': book.book_id, 'title': book.book_title,
                    'authors': book.author_name, 'file_path': book.file_path}
            self.record_failed_enforcement(info, book.enforce_error, trigger_type=f'{trigger} (failed)')
        return len(failed)


    def record_failed_enforcement(self, log_info: dict, error: Exception | str,
                                  trigger_type: str = "auto -log (failed)") -> None:
        """Record a failed enforcement attempt so admins can see it in stats."""
        try:
            # Ensure file_path exists for DB insert
            if not log_info.get('file_path'):
                log_info['file_path'] = "unknown"
            self.db.enforce_add_entry_from_log(log_info, trigger_type=trigger_type)
        except Exception as e:
            print(f"[cover-metadata-enforcer] WARNING: Unable to record failed enforcement: {e}", flush=True)

        # Always surface the failure to logs
        print(f"[cover-metadata-enforcer] ERROR: Failed to enforce metadata for '{log_info.get('title', 'Unknown')}' (book_id={log_info.get('book_id', 'unknown')}): {error}", flush=True)


    def empty_metadata_temp(self):
        """Empties the metadata_temp folder"""
        if not os.path.isdir(metadata_temp_dir):
            return
        for entry in os.listdir(metadata_temp_dir):
            entry_path = os.path.join(metadata_temp_dir, entry)
            try:
                if os.path.isdir(entry_path) and not os.path.islink(entry_path):
                    shutil.rmtree(entry_path)
                else:
                    os.remove(entry_path)
            except FileNotFoundError:
                continue


    def check_for_other_logs(self, processed_book_ids: set | None = None) -> int:
        """Processes the change logs that queued up while this run was busy.
        Returns the number of books/files that could not be enforced."""
        processed_book_ids = processed_book_ids or set()
        failures = 0
        log_files = [os.path.join(dirpath, f)
                     for (dirpath, _, filenames) in os.walk(change_logs_dir)
                     for f in filenames if f.endswith('.json')]

        if len(log_files) > 0:
            print(f"[cover-metadata-enforcer] {len(log_files)} Additional metadata changes detected, processing now..", flush=True)

            # Coalesce logs per book to the newest entry
            latest_by_book: dict[str, tuple[str, datetime]] = {}
            for log in log_files:
                book_id, ts = self._parse_log_filename(log)
                if book_id is None or ts is None:
                    continue
                if book_id not in latest_by_book or ts > latest_by_book[book_id][1]:
                    latest_by_book[book_id] = (log, ts)

            # Delete any older logs for the same book
            latest_paths = {entry[0] for entry in latest_by_book.values()}
            for log in log_files:
                if log not in latest_paths:
                    self.delete_log(auto=False, log_path=log)

            for log_path, _ in latest_by_book.values():
                log_info = self.read_log(auto=False, log_path=log_path)
                # Skip if log_info is None (file was deleted or invalid)
                if log_info is None:
                    continue

                book_id = str(log_info.get('book_id', '')).strip()
                if book_id in processed_book_ids:
                    self.delete_log(auto=False, log_path=log_path)
                    continue

                book_dir = self.get_book_dir_from_log(log_info)
                try:
                    book_objects = self.enforce_cover(book_dir)
                    if book_objects:
                        failures += self.record_book_results(book_objects, 'auto -log', log_info=log_info)
                    else:
                        self.record_failed_enforcement(log_info, "No supported files or enforcement failed")
                        failures += 1
                except Exception as e:
                    self.record_failed_enforcement(log_info, e)
                    failures += 1
                finally:
                    processed_book_ids.add(book_id)
                    self.delete_log(auto=False, log_path=log_path)
        return failures


def main():
    acquire_lock()
    parser = argparse.ArgumentParser(
        prog='cover-enforcer',
        description='Upon receiving a log, valid directory or an "-all" flag, this \
        script will enforce the covers and metadata of the corresponding books, making \
        sure that each are correctly stored in both the ebook files themselves as well as in the \
        user\'s Calibre Library. A file is only replaced once the rewritten copy has been \
        checked; on any failure the original is left untouched. Set \
        CWA_ENFORCER_UPGRADE_BOOK=true to also upgrade books\' internals (e.g. EPUB 2 to EPUB 3).'
    )

    parser.add_argument('--log', action='store', dest='log', required=False, help='Will enforce the covers and metadata of the books in the given log file.', default=None)
    parser.add_argument('--dir', action='store', dest='dir', required=False, help='Will enforce the covers and metadata of the books in the given directory.', default=None)
    parser.add_argument('-all', action='store_true', dest='all', help='Will enforce covers & metadata for ALL books currently in your calibre-library-dir', default=False)
    parser.add_argument('-list', '-l', action='store_true', dest='list', help='List all books in your calibre-library-dir', default=False)
    parser.add_argument('-history', action='store_true', dest='history', help='Display a history of all enforcements ever carried out on your machine (not yet implemented)', default=False)
    parser.add_argument('-paths', '-p', action='store_true', dest='paths', help="Use with '-history' flag to display stored paths of all files in enforcement database", default=False)
    parser.add_argument('-v', '--verbose', action='store_true', dest='verbose', help="Use with history to display entire enforcement history instead of only the most recent 10 entries", default=False)
    args = parser.parse_args()

    enforcer = Enforcer(args)

    if len(sys.argv) == 1:
        parser.print_help()
    #########################     QUERY ARGS     ###########################
    elif args.log is not None and args.dir is not None:
        ### log and dir provided together
        parser.print_usage()
    elif args.list and args.log is None and args.dir is None and args.all is False and args.history is False:
        ### only list flag passed
        enforcer.print_library_list()
    elif args.history and args.log is None and args.dir is None and args.all is False and args.list is False:
        ### only history flag passed
        enforcer.db.enforce_show(args.paths, args.verbose)
    #########################  ENFORCEMENT ARGS  ###########################
    elif args.all and args.log is None and args.dir is None and args.list is False and args.history is False:
        ### only all flag passed
        print('[cover-metadata-enforcer]: Enforcing metadata and covers for all books in library...')
        n_enforced, completion_time, n_supported_files = enforcer.enforce_all_covers()
        if n_enforced == False:
            print("\n[cover-metadata-enforcer]: No supported ebook files found in library (only EPUB & AZW3 formats are currently supported)")
        elif n_enforced == n_supported_files:
            print(f"\n[cover-metadata-enforcer]: SUCCESS: All covers & metadata successfully updated for all {n_enforced} supported ebooks in the library in {completion_time:.2f} seconds!")
        elif n_enforced == 0:
            print("\n[cover-metadata-enforcer]: FAILURE: Supported files found but none we're successfully enforced. See the log above for details.")
        elif n_enforced < n_supported_files:
            print(f"\n[cover-metadata-enforcer]: PARTIAL SUCCESS: Out of {n_supported_files} supported files detected, {n_enforced} were successfully enforced. See log above for details")
    elif args.log is None and args.dir is not None and args.all is False and args.list is False and args.history is False:
        ### dir passed, no log, not all, no flags
        if args.dir[-1] == '/':
            args.dir = args.dir[:-1]
        if os.path.isdir(args.dir):
            book_objects = enforcer.enforce_cover(args.dir)
            if book_objects and enforcer.record_book_results(book_objects, 'manual -dir'):
                sys.exit(1)
        else:
            print(f"[cover-metadata-enforcer]: ERROR: '{args.dir}' is not a valid directory")
    elif args.log is not None and args.dir is None and args.all is False and args.list is False and args.history is False:
        ### log passed: (args.log), no dir
        log_info = enforcer.read_log()

        # Handle case where log file doesn't exist (race condition)
        if log_info is None:
            print("[cover-metadata-enforcer] Skipping processing due to missing or invalid log file. This is normal if the file was already processed.")
            sys.exit(0)

        # If multiple logs exist for the same book, prefer the newest one
        current_log_path = os.path.join(change_logs_dir, args.log)
        latest_log_path = enforcer.select_latest_log_for_book(log_info.get('book_id'), current_log_path=current_log_path)
        if latest_log_path and os.path.abspath(latest_log_path) != os.path.abspath(current_log_path):
            # Switch to the latest log and delete the older one
            enforcer.delete_log(auto=False, log_path=current_log_path)
            log_info = enforcer.read_log(auto=False, log_path=latest_log_path)
            if log_info is None:
                print("[cover-metadata-enforcer] Skipping processing due to missing or invalid log file. This is normal if the file was already processed.")
                sys.exit(0)

        book_dir = enforcer.get_book_dir_from_log(log_info)
        if enforcer.enforcer_on:
            try:
                book_objects = enforcer.enforce_cover(book_dir)
                if not book_objects:
                    print(f"[cover-metadata-enforcer] Metadata for '{log_info['title']}' not successfully enforced")
                    enforcer.record_failed_enforcement(log_info, "No supported files or enforcement failed")
                    enforcer.delete_log()
                    sys.exit(1)
                failures = enforcer.record_book_results(book_objects, 'auto -log', log_info=log_info)
                enforcer.delete_log()
                failures += enforcer.check_for_other_logs(processed_book_ids={str(log_info.get('book_id', '')).strip()})
                if failures:
                    sys.exit(1)
            except Exception as e:
                enforcer.record_failed_enforcement(log_info, e)
                enforcer.delete_log()
                sys.exit(1)
        else: # Enforcer has been disabled in the CWA Settings
            print(f"[cover-metadata-enforcer] The CWA Automatic Metadata enforcement service is currently disabled in the settings. Therefore the metadata changes for {log_info['title'].replace(':', '_')} won't be enforced.\n\nThis means that the changes made will appear in the Web UI, but not be stored in the ebook files themselves.")
            enforcer.delete_log()
    else:
        parser.print_usage()

    sys.exit(0)

if __name__ == "__main__":
    main()
