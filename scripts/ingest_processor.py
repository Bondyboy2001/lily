# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The ingest process: imports and backs up files dropped in the ingest folder."""

import atexit
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import shutil
import sqlite3
import fcntl
from datetime import datetime, UTC
from pathlib import Path

import title_card  # stdlib only at import time; Wand loads when a card is drawn

# ── Lazy-initialization sentinels ──────────────────────────────────────────
# Heavy modules (metadata fetch, audiobook support) are NOT imported at module level.  All globals below start as
# None / empty and are populated by initialize_runtime().  This allows main()
# to fast-exit on missing/stale ingest targets without importing cps.* (which
# triggers Flask app init) or creating process-lock files.
#
# IMPORTANT:  Any code path that uses these globals MUST be reachable only
# AFTER initialize_runtime() has returned True.  If you add a new function
# that touches cps.*, cwa_db, audiobook, or requests, ensure
# it is only called from add_book_to_library(), add_format_to_book(), or
# another path gated by initialize_runtime().
# ───────────────────────────────────────────────────────────────────────────
_CPS_AVAILABLE = False
_cps_config = None
fetch_and_apply_metadata = None
clear_new_book_details = None
tidy_new_book_authors = None
recentre_new_book_cover = None
CWA_DB = None
audiobook = None
requests = None
backup_destinations = {}
process_lock = None
_runtime_initialized = False
_runtime_init_attempted = False


def _bounded_reason(text: str, limit: int = 800) -> str:
    text = " ".join(str(text or "").split())
    return text[:limit]


# A browser upload is saved as "new_<user id>_<UTC stamp>_<file name>" (editbooks_upload.py)
# A book already in the library is imported again beside it: both copies are kept, and
# Duplicates can resolve them
IMPORT_MERGE = "new_record"

_UPLOAD_PREFIX = re.compile(r"^new_\d+_\d{8}_\d{6}_\d{6}_(?=.)")


def _import_name(name: str) -> str:
    """The name a new book's file is staged under: the uploaded file's own name, since
    calibre titles a file with no title inside it (DjVu, many PDFs) by its name."""
    return _UPLOAD_PREFIX.sub("", name)


def _record_job(job_id, state, error="", book_id=None):
    if not job_id:
        return
    try:
        from automation_jobs import finish_job
        finish_job(job_id, state, error, book_id=book_id)
    except Exception as e:
        print(f"[ingest-processor] WARN: could not record job {job_id}: {e}", flush=True)

class ProcessLock:
    """Process lock backed by flock(2).

    The kernel releases the flock automatically when the holding process exits
    (even on SIGKILL), so there is no such thing as a "stale" lock to clean up.
    The lock file itself is never truncated before the lock is held and never
    unlinked: removing a path another process has flocked would let a third
    process create a fresh inode and lock it too, so two processors could run
    at once. The PID written after acquiring is purely diagnostic.
    """

    def __init__(self, lock_name="ingest_processor"):
        self.lock_name = lock_name
        self.lock_path = os.path.join(tempfile.gettempdir(), f"{lock_name}.lock")
        self.lock_file = None
        self.acquired = False

    def acquire(self, timeout=5):
        """Acquire the lock with timeout. Returns True if successful, False if another process has it."""
        try:
            # 'a+' creates the file if needed without truncating the holder's PID
            self.lock_file = open(self.lock_path, 'a+')

            start_time = time.time()
            while True:
                try:
                    fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    # Lock is held by another live process
                    if time.time() - start_time >= timeout:
                        break
                    time.sleep(0.1)
                    continue

                # We hold the lock now, so it is safe to replace the diagnostic PID
                try:
                    self.lock_file.seek(0)
                    self.lock_file.truncate()
                    self.lock_file.write(str(os.getpid()))
                    self.lock_file.flush()
                except OSError as e:
                    print(f"[ingest-processor] WARN: Could not record PID in lock file: {e}")

                self.acquired = True
                print(f"[ingest-processor] Lock acquired successfully (PID: {os.getpid()})")
                return True

            holding_pid = self._get_holding_pid()
            print(f"[ingest-processor] CANCELLING... ingest-processor initiated but is already running (PID: {holding_pid})")
            self.release()
            return False

        except Exception as e:
            print(f"[ingest-processor] Error acquiring lock: {e}")
            self.release()
            return False

    def _get_holding_pid(self):
        """Get the PID recorded by the process holding the lock (diagnostic only)"""
        try:
            with open(self.lock_path) as f:
                pid_str = f.read().strip()
            return int(pid_str) if pid_str.isdigit() else "unknown"
        except Exception:
            pass
        return "unknown"

    def release(self):
        """Release the lock. The lock file is intentionally left in place."""
        if self.lock_file is None:
            self.acquired = False
            return
        try:
            if self.acquired:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
                print(f"[ingest-processor] Lock released (PID: {os.getpid()})")
        except Exception as e:
            print(f"[ingest-processor] Error releasing lock: {e}")
        finally:
            try:
                self.lock_file.close()
            except Exception:
                pass
            self.lock_file = None
            self.acquired = False

def cleanup_lock():
    """Cleanup function for atexit"""
    if process_lock:
        process_lock.release()

# Register cleanup function
atexit.register(cleanup_lock)


def get_app_db_path() -> str:
    """Resolve app.db path consistently with the main app config."""
    app_db_path = os.environ.get("CWA_APP_DB_PATH")
    if app_db_path:
        return app_db_path
    base_path = os.environ.get("CALIBRE_DBPATH", "/config")
    if base_path.endswith(".db"):
        if os.path.basename(base_path) != "app.db":
            return os.path.join(os.path.dirname(base_path), "app.db")
        return base_path
    return os.path.join(base_path, "app.db")


def _load_cps_settings_from_app_db() -> None:
    """Load the CPS settings this process uses: the internal HTTPS certificates, and the library
    paths, file naming and provider keys that metadata lookups need (config.get_book_path() reads
    the split library settings; moving a renamed book's folder reads config_unicode_filename;
    without the Google Books key a new book's lookup uses the shared quota, which is soon spent)."""
    if not _cps_config:
        return
    # The web app loads these from app.db at start; this process only has a bare ConfigSQL
    _cps_config.config_calibre_split = False
    _cps_config.config_calibre_split_dir = None
    _cps_config.config_unicode_filename = False
    try:
        app_db_path = get_app_db_path()
        with sqlite3.connect(app_db_path, timeout=30) as con:
            cur = con.cursor()
            row = cur.execute(
                "SELECT config_calibre_dir, config_certfile, config_keyfile "
                "FROM settings LIMIT 1"
            ).fetchone()
            if not row:
                return

            if row[0]:
                _cps_config.config_calibre_dir = row[0]
            if row[1]:
                _cps_config.config_certfile = row[1]
            if row[2]:
                _cps_config.config_keyfile = row[2]
            columns = {r[1] for r in cur.execute("PRAGMA table_info(settings)")}
            for name in ("config_calibre_split", "config_calibre_split_dir", "config_unicode_filename",
                         "config_google_books_api_key", "config_hardcover_token"):
                if name in columns:
                    value = cur.execute(f"SELECT {name} FROM settings LIMIT 1").fetchone()[0]
                    if value is not None:
                        setattr(_cps_config, name, value)
    except Exception as e:
        print(f"[ingest-processor] WARN: Could not read CPS settings from app.db ({app_db_path}): {e}", flush=True)

def _ensure_project_root_on_path() -> None:
    cps_path = os.path.dirname(os.path.dirname(__file__))
    if cps_path not in sys.path:
        sys.path.append(cps_path)


def _load_runtime_dependencies() -> None:
    global CWA_DB, audiobook, requests
    if CWA_DB and audiobook and requests:
        return

    from cwa_db import CWA_DB as _CWA_DB
    import audiobook as _audiobook
    import requests as _requests

    CWA_DB = _CWA_DB
    audiobook = _audiobook
    requests = _requests


def _load_optional_cps_modules() -> None:
    global _CPS_AVAILABLE, _cps_config, fetch_and_apply_metadata, clear_new_book_details, tidy_new_book_authors, \
        recentre_new_book_cover

    if _CPS_AVAILABLE:
        return

    try:
        _ensure_project_root_on_path()

        # CPS settings (certificate paths for the internal HTTPS API)
        try:
            from cps import config as loaded_cps_config
            _cps_config = loaded_cps_config
            _load_cps_settings_from_app_db()
        except (ImportError, TypeError, AttributeError) as e:
            print(f"[ingest-processor] CPS settings not available: {e}", flush=True)
            _cps_config = None

        # Import metadata functionality
        try:
            from cps.metadata_helper import fetch_and_apply_metadata as loaded_fetch_and_apply_metadata
            from cps.tag_cleanup import clear_new_book_details as loaded_clear_new_book_details
            from cps.author_cleanup import tidy_new_book_authors as loaded_tidy_new_book_authors
            from cps.pdf_cover import recentre_new_book_cover as loaded_recentre_new_book_cover
            from cps import ub as loaded_ub
            from cps.calibre_init import init_calibre_db_from_app_db
            init_calibre_db_from_app_db(get_app_db_path())
            # Filing a paper on the arXiv shelf opens app.db by this path
            loaded_ub.app_DB_path = loaded_ub.app_DB_path or get_app_db_path()
            fetch_and_apply_metadata = loaded_fetch_and_apply_metadata
            clear_new_book_details = loaded_clear_new_book_details
            tidy_new_book_authors = loaded_tidy_new_book_authors
            recentre_new_book_cover = loaded_recentre_new_book_cover
            _CPS_AVAILABLE = True
            print("[ingest-processor] Metadata functionality available", flush=True)
        except ImportError as e:
            print(f"[ingest-processor] Metadata functionality not available: {e}", flush=True)
            fetch_and_apply_metadata = None
            clear_new_book_details = None
            tidy_new_book_authors = None
            recentre_new_book_cover = None
            _CPS_AVAILABLE = False

    except Exception as e:
        print(f"[ingest-processor] WARN: Unexpected error during CPS path setup: {e}", flush=True)
        _CPS_AVAILABLE = False


def _ensure_processed_books_dirs() -> None:
    """Ensure processed backups directory structure exists so backups never crash on missing folders."""
    try:
        processed_root = "/config/processed_books"
        os.makedirs(processed_root, exist_ok=True)
        os.makedirs(os.path.join(processed_root, "failed"), exist_ok=True)
    except Exception as e:
        print(f"[ingest-processor] WARN: Could not ensure processed_books directories: {e}", flush=True)


def _load_backup_destinations() -> None:
    global backup_destinations
    try:
        backup_destinations = {
            entry.name: entry.path
            for entry in os.scandir("/config/processed_books")
            if entry.is_dir()
        }
    except FileNotFoundError:
        # Fallback for test environments where /config might not exist
        backup_destinations = {}
    except Exception as e:
        print(f"[ingest-processor] WARN: Could not scan processed_books: {e}", flush=True)
        backup_destinations = {}


def initialize_runtime() -> bool:
    """Initialize heavy ingest runtime after the target path has passed cheap validation."""
    global process_lock, _runtime_initialized, _runtime_init_attempted

    if _runtime_initialized:
        return True
    if _runtime_init_attempted:
        return False
    _runtime_init_attempted = True

    _ensure_project_root_on_path()
    _load_runtime_dependencies()
    _load_optional_cps_modules()

    process_lock = ProcessLock()
    if not process_lock.acquire(timeout=10):
        return False

    _ensure_processed_books_dirs()
    _load_backup_destinations()
    _runtime_initialized = True
    return True


DEFAULT_FAILED_DIR = "/config/processed_books/failed"


def unique_failed_path(failed_dir: str, filename: str) -> str:
    """Return a path in failed_dir that doesn't exist yet: '<timestamp>_<name>', plus a counter on collision."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem, ext = os.path.splitext(filename)
    candidate = os.path.join(failed_dir, f"{timestamp}_{filename}")
    counter = 1
    while os.path.lexists(candidate):
        candidate = os.path.join(failed_dir, f"{timestamp}_{stem}_{counter}{ext}")
        counter += 1
    return candidate


def _is_missing_ingest_target(filepath: str) -> bool:
    return not os.path.isfile(filepath) and not os.path.isdir(filepath)

def get_internal_api_url(path):
    """Construct internal API URL, respecting SSL configuration"""
    port = os.getenv('CWA_PORT_OVERRIDE', '8083').strip()
    if not port.isdigit():
        port = '8083'

    protocol = "http"
    certfile = None
    keyfile = None
    if _cps_config:
        certfile = getattr(_cps_config, "config_certfile", None)
        keyfile = getattr(_cps_config, "config_keyfile", None)
    if not certfile and not keyfile:
        try:
            app_db_path = get_app_db_path()
            with sqlite3.connect(app_db_path, timeout=30) as con:
                cur = con.cursor()
                row = cur.execute(
                    "SELECT config_certfile, config_keyfile FROM settings LIMIT 1"
                ).fetchone()
                if row:
                    certfile, keyfile = row[0], row[1]
        except Exception as e:
            print(f"[ingest-processor] WARN: Could not read TLS settings from app.db ({app_db_path}): {e}", flush=True)

    if certfile and keyfile and os.path.isfile(certfile) and os.path.isfile(keyfile):
        protocol = "https"

    if not path.startswith("/"):
        path = "/" + path

    return f"{protocol}://127.0.0.1:{port}{path}"


def get_internal_api_headers():
    """Headers that authenticate this process to the web app's internal endpoints."""
    from cwa_internal_auth import internal_headers
    return internal_headers()


def get_ingest_batch_dirty_file() -> str:
    return os.environ.get("CWA_INGEST_BATCH_DIRTY_FILE", "/config/cwa_ingest_batch_dirty")


def mark_ingest_batch_dirty() -> None:
    dirty_file = get_ingest_batch_dirty_file()
    try:
        dirty_dir = os.path.dirname(dirty_file)
        if dirty_dir:
            os.makedirs(dirty_dir, exist_ok=True)
        with open(dirty_file, "w", encoding="utf-8") as marker:
            marker.write(f"dirty_at={int(time.time())}\n")
        print(f"[ingest-processor] Marked ingest batch follow-up dirty: {dirty_file}", flush=True)
    except Exception as e:
        print(f"[ingest-processor] WARN: Failed to mark ingest batch follow-up dirty: {e}", flush=True)


def _post_internal_endpoint(path: str, payload: dict | None = None) -> bool:
    global requests
    if requests is None:
        import requests as loaded_requests
        requests = loaded_requests

    retryable_statuses = (500, 503)
    retryable_exceptions = (requests.exceptions.Timeout, requests.exceptions.ConnectionError)
    max_attempts = 2 if path == "/cwa-internal/reconnect-db" else 1

    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(
                get_internal_api_url(path),
                json=payload,
                headers=get_internal_api_headers(),
                timeout=5,
                verify=False,
            )
            if resp.status_code == 200:
                return True
            if resp.status_code in retryable_statuses and attempt < max_attempts:
                print(
                    f"[ingest-processor] WARN: Batch follow-up endpoint {path} returned "
                    f"{resp.status_code}; retrying once",
                    flush=True,
                )
                time.sleep(1)
                continue
            print(
                f"[ingest-processor] WARN: Batch follow-up endpoint {path} returned {resp.status_code}",
                flush=True,
            )
            return False
        except retryable_exceptions as e:
            if attempt < max_attempts:
                print(
                    f"[ingest-processor] WARN: Batch follow-up endpoint {path} failed transiently: "
                    f"{e}; retrying once",
                    flush=True,
                )
                time.sleep(1)
                continue
            print(f"[ingest-processor] WARN: Batch follow-up endpoint {path} failed: {e}", flush=True)
            return False
        except Exception as e:
            print(f"[ingest-processor] WARN: Batch follow-up endpoint {path} failed: {e}", flush=True)
            return False
    return False


def run_post_batch_follow_up() -> int:
    """Run follow-up work once the ingest service observes a quiet dirty batch."""
    print("[ingest-processor] Running post-batch follow-up", flush=True)
    checks = [
        _post_internal_endpoint("/cwa-internal/reconnect-db"),
        _post_internal_endpoint("/duplicates/invalidate-cache"),
        _post_internal_endpoint("/cwa-internal/queue-duplicate-scan"),
    ]
    if all(checks):
        print("[ingest-processor] Post-batch follow-up completed", flush=True)
        return 0
    print("[ingest-processor] WARN: Post-batch follow-up incomplete", flush=True)
    return 1


class NewBookProcessor:
    def __init__(self, filepath: str):
        def _normalize_format(value: str) -> str:
            if value is None:
                return ""
            value = str(value).strip()
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            return value.strip().lower()

        def _normalize_format_list(values):
            if values is None:
                return []
            if isinstance(values, str):
                values = values.split(',') if values else []
            return [
                _normalize_format(v) for v in values
                if v is not None and str(v).strip() != ""
            ]

        # Settings / DB
        self.db = CWA_DB()
        self.cwa_settings = self.db.cwa_settings

        # Core ingest settings
        self.ingest_ignored_formats = _normalize_format_list(self.cwa_settings['auto_ingest_ignored_formats'])

        # Add known temporary / partial extensions
        for tmp_ext in ("crdownload", "download", "part", "uploading", "temp"):
            if tmp_ext not in self.ingest_ignored_formats:
                self.ingest_ignored_formats.append(tmp_ext)


        # Formats
        self.supported_book_formats = {'epub', 'pdf', 'djvu', 'djv'}
        self.supported_audiobook_formats = {'m4b', 'm4a', 'mp4'}

        # Directories
        self.ingest_folder, self.library_dir, self.tmp_conversion_dir = self.get_dirs("/app/calibre-web-automated/dirs.json")
        self.ingest_folder = os.path.normpath(self.ingest_folder)
        # Ensure library_dir is consistent with the main app's config
        app_db_path = get_app_db_path()
        with sqlite3.connect(app_db_path, timeout=30) as con:
            cur = con.cursor()
            try:
                db_path = cur.execute('SELECT config_calibre_dir FROM settings;').fetchone()[0]
                if db_path:
                    self.library_dir = db_path
            except Exception as e:
                print(f"[ingest-processor] WARN: Could not read config_calibre_dir from app.db ({app_db_path}), using default. Error: {e}", flush=True)

        Path(self.tmp_conversion_dir).mkdir(exist_ok=True)
        self.staging_dir = os.path.join(self.tmp_conversion_dir, "staging")
        Path(self.staging_dir).mkdir(exist_ok=True)

        # Current file
        self.filepath = filepath
        self.filename = os.path.basename(filepath)
        self.input_format = Path(self.filepath).suffix[1:].lower()

        # Calibre environment
        self.calibre_env = os.environ.copy()
        self.calibre_env["HOME"] = "/config"  # Enable plugins under /config

        self.metadata_db = os.path.join(self.library_dir, "metadata.db")
        # Split library support
        self.split_library = self.get_split_library()
        if self.split_library:
            self.calibre_env['CALIBRE_OVERRIDE_DATABASE_PATH'] = self.metadata_db
            self.library_dir = self.split_library["split_path"]

        # Track the last added Calibre book id(s) from calibredb output
        self.last_added_book_id: int | None = None
        # Short reason recorded on the ingest job when import fails
        self.failure_reason = ""
        self.last_added_book_ids: list[int] = []
        self._title_sort_regex = self._get_title_sort_regex()

    @staticmethod
    def _get_title_sort_regex() -> str:
        default_regex = (
            r'^(A|The|An|Der|Die|Das|Den|Ein|Eine|Einen|Dem|Des|Einem|Eines|Le|La|Les|L\'|Un|Une)\s+'
        )
        try:
            app_db_path = get_app_db_path()
            with sqlite3.connect(app_db_path, timeout=30) as con:
                cur = con.cursor()
                row = cur.execute(
                    "SELECT config_title_regex FROM settings LIMIT 1"
                ).fetchone()
                if row and row[0]:
                    return row[0]
        except Exception as e:
            print(f"[ingest-processor] WARN: Could not read config_title_regex from app.db ({app_db_path}): {e}", flush=True)
        return default_regex

    @staticmethod
    def _parse_added_book_ids(output: str) -> list[int]:
        """Parse calibredb stdout for the 'Added/Merged/Updated book ids: X[, Y, ...]' line and return IDs.

        Handles variations like 'Added book id: 4' or 'Merged book ids: 4, 5'.
        """
        try:
            import re
            ids: list[int] = []
            # calibredb prints separate "Added book ids:" and "Merged book ids:" lines
            for nums in re.findall(r"(?:Added|Merged|Updated) book id[s]?:[ \t]*([0-9, \t]+)", output, flags=re.IGNORECASE):
                for part in nums.split(','):
                    if part.strip().isdigit() and int(part) not in ids:
                        ids.append(int(part))
            return ids
        except Exception:
            return []

    def _fallback_last_added_book_id(self) -> None:
        """Fallback to the most recently modified book when calibredb output lacks IDs."""
        if self.last_added_book_id is not None:
            return
        try:
            with sqlite3.connect(self.metadata_db, timeout=30) as con:
                cur = con.cursor()
                row = cur.execute(
                    "SELECT id FROM books ORDER BY last_modified DESC LIMIT 1"
                ).fetchone()
                if row:
                    self.last_added_book_id = int(row[0])
                    self.last_added_book_ids = [self.last_added_book_id]
                    print(
                        "[ingest-processor] WARN: Could not parse calibredb output; using most recently modified book ID.",
                        flush=True,
                    )
        except Exception as e:
            print(f"[ingest-processor] WARN: Failed to infer book ID after import: {e}", flush=True)

    def _register_title_sort_function(self, connection: sqlite3.Connection) -> bool:
        """Register title_sort SQL function on a raw SQLite connection."""
        try:
            import re
            title_pat = re.compile(self._title_sort_regex, re.IGNORECASE)

            def _title_sort(title):
                if title is None:
                    title = ""
                match = title_pat.search(title)
                if match:
                    prep = match.group(1)
                    title = title[len(prep):] + ', ' + prep
                return " ".join(str(title).split())

            connection.create_function("title_sort", 1, _title_sort)
            return True
        except Exception as e:
            print(f"[ingest-processor] WARN: Could not register title_sort function: {e}", flush=True)
            return False
    def get_split_library(self) -> dict[str, str] | None:
        """Checks whether or not the user has split library enabled. Returns None if they don't and the path of the Split Library location if True."""
        app_db_path = get_app_db_path()
        with sqlite3.connect(app_db_path, timeout=30) as con:
            cur = con.cursor()
            split_library = cur.execute('SELECT config_calibre_split FROM settings;').fetchone()[0]

            if split_library:
                split_path = cur.execute('SELECT config_calibre_split_dir FROM settings;').fetchone()[0]
                db_path = cur.execute('SELECT config_calibre_dir FROM settings;').fetchone()[0]
                return {
                    "split_path": split_path,
                    "db_path": db_path,
                }
            return None


    def get_dirs(self, dirs_json_path: str) -> tuple[str, str, str]:
        dirs = {}
        with open(dirs_json_path) as f:
            dirs: dict[str, str] = json.load(f)

        ingest_folder = f"{dirs['ingest_folder']}/"
        library_dir = f"{dirs['calibre_library_dir']}/"
        tmp_conversion_dir = f"{dirs['tmp_conversion_dir']}/"

        return ingest_folder, library_dir, tmp_conversion_dir


    def is_supported_audiobook(self) -> bool:
        input_format = Path(self.filepath).suffix[1:].lower()
        return input_format in self.supported_audiobook_formats

    def backup(self, input_file, backup_type):
        output_path = None
        try:
            output_path = backup_destinations.get(backup_type)
            if not output_path:
                raise KeyError(f"No backup destination for type '{backup_type}'")
            # Ensure destination directory exists
            os.makedirs(output_path, exist_ok=True)
            if backup_type == "failed":
                # Never overwrite an earlier failed copy that happens to share a name
                destination = shutil.copy(input_file, unique_failed_path(output_path, os.path.basename(input_file)))
            else:
                destination = shutil.copy(input_file, output_path)
            os.utime(destination, None)
        except Exception as e:
            # Never let backups crash ingest; just log the problem
            print(f"[ingest-processor]: ERROR - Failed to backup '{input_file}' to '{output_path}': {e}")

    def move_to_failed(self) -> bool:
        """Move the ingest source into processed_books/failed under a unique name.

        Returns True once the source is safely out of the ingest folder. If the move
        fails the source is left where it is (never deleted) and the error is logged.
        """
        if not os.path.exists(self.filepath):
            print(f"[ingest-processor] Source already gone, nothing to move to failed: {self.filepath}", flush=True)
            return True
        failed_dir = backup_destinations.get("failed") or DEFAULT_FAILED_DIR
        try:
            os.makedirs(failed_dir, exist_ok=True)
            destination = unique_failed_path(failed_dir, self.filename)
            shutil.move(self.filepath, destination)
            print(f"[ingest-processor] Moved {self.filename} to failed backups: {destination}", flush=True)
            return True
        except Exception as e:
            print(
                f"[ingest-processor] ERROR: Could not move {self.filepath} to {failed_dir} ({e}). "
                "LEAVING THE ORIGINAL IN THE INGEST FOLDER so it is not lost.",
                flush=True,
            )
            return False


    def delete_current_file(self) -> None:
        """Deletes file just processed from ingest folder"""
        try:
            ext = Path(self.filename).suffix.replace('.', '')
            if ext in self.ingest_ignored_formats or self.filename.endswith(".cwa.json") or self.filename.endswith(".cwa.failed.json"):
                print(f"[ingest-processor] Skipping delete for ignored/temporary file: {self.filename}", flush=True)
                return
            if os.path.exists(self.filepath):
                os.remove(self.filepath) # Removes processed file
            else:
                # Likely a transient/temporary file (.uploading) that was renamed before we processed cleanup
                print(f"[ingest-processor] Skipping delete; file already gone: {self.filepath}", flush=True)
                return

            parent_dir = os.path.dirname(self.filepath)
            # Only attempt folder cleanup if parent still exists and isn't the ingest root
            if os.path.isdir(parent_dir) and os.path.exists(parent_dir):
                try:
                    if os.path.exists(self.ingest_folder) and os.path.normpath(parent_dir) != self.ingest_folder:
                        subprocess.run(["find", parent_dir, "-type", "d", "-empty", "-delete"], check=False)
                except Exception as e:
                    print(f"[ingest-processor] WARN: Failed pruning empty folders for {parent_dir}: {e}", flush=True)
        except Exception as e:
            print(f"[ingest-processor] WARN: Failed to delete processed file {self.filepath}: {e}", flush=True)

    def is_file_in_use(self, timeout: float = None) -> bool:
        """Wait until the file is no longer in use (write handle is closed) or timeout is reached.
        Returns True if file is ready, False if timed out or file vanished."""

        # Use configured timeout from CWA settings (default 15 minutes if not configured)
        if timeout is None:
            timeout_minutes = self.cwa_settings.get('ingest_timeout_minutes', 15)
            timeout = timeout_minutes * 60  # Convert to seconds

        start = time.time()
        while time.time() - start < timeout:
            if not os.path.exists(self.filepath):
                return False
            try:
                # lsof '-F f' gets file access mode; we check for 'w' (write).
                # Add timeout to prevent hanging (issue #654)
                result = subprocess.run(['lsof', '-F', 'f', '--', self.filepath],
                                      capture_output=True, text=True, timeout=10)
                if 'w' not in result.stdout:
                    return True # Not in use for writing
            except subprocess.TimeoutExpired:
                print("[ingest-processor] WARN: lsof command timed out. Assuming file is not in use.", flush=True)
                return True  # If lsof hangs, assume file is ready to avoid indefinite wait
            except FileNotFoundError:
                print("[ingest-processor] WARN: 'lsof' command not found. Cannot reliably check if file is in use. Proceeding with caution.", flush=True)
                return True # Fallback for systems without lsof
            except Exception as e:
                print(f"[ingest-processor] WARN: Error checking file usage with lsof: {e}", flush=True)
                # On error, wait and retry to be safe
            time.sleep(1)
        return False # Timeout reached



    def add_book_to_library(self, book_path:str, text: bool=True, format: str="text" ) -> bool:
        """Import book_path into the library. Returns True only once calibredb has accepted it;
        on False the caller is responsible for preserving the ingest source in failed/."""
        print("[ingest-processor]: Importing new book to CWA...")
        source_path = Path(book_path)
        if not source_path.exists() or source_path.stat().st_size == 0:
            print(f"[ingest-processor] ERROR: Import file is missing or empty, skipping: {book_path}", flush=True)
            return False

        # Stage file for import
        staged_path = Path(self.staging_dir) / _import_name(source_path.name)
        try:
            shutil.copy2(source_path, staged_path)
        except Exception as e:
            print(f"[ingest-processor] ERROR: Failed to stage file for import: {e}", flush=True)
            return False

        imported = False
        try:
            if text:
                add_command = [
                    "calibredb", "add", str(staged_path), "--automerge", IMPORT_MERGE, f"--library-path={self.library_dir}"
                ]
                # An EPUB with no cover image gets a title card rather than calibre's edge-to-edge
                # render of its first page.
                card_path = staged_path.with_name(staged_path.stem + ".title-card.jpg")
                if staged_path.suffix.lower() == ".epub":
                    try:
                        if title_card.card_for_epub(staged_path, card_path):
                            add_command.extend(["--cover", str(card_path)])
                    except Exception as e:
                        print(f"[ingest-processor] WARN: Could not make a title card, calibre will render the first page: {e}", flush=True)
                try:
                    result = subprocess.run(add_command, env=self.calibre_env, check=True, capture_output=True, text=True)
                finally:
                    card_path.unlink(missing_ok=True)
                added_ids = self._parse_added_book_ids((result.stdout or '') + '\n' + (result.stderr or ''))
                if added_ids:
                    self.last_added_book_ids = added_ids
                    self.last_added_book_id = added_ids[-1]
                else:
                    self._fallback_last_added_book_id()
            else:  # audiobook path
                meta = audiobook.get_audio_file_info(str(staged_path), format, os.path.basename(str(staged_path)), False)

                # Coalesce metadata to safe strings
                _title = str(meta[2]) if meta[2] else Path(staged_path).stem
                _authors = str(meta[3]) if meta[3] else ""
                _cover = meta[4] if meta[4] and isinstance(meta[4], str) else None

                add_command = [
                    "calibredb", "add", str(staged_path), "--automerge", IMPORT_MERGE,
                    f"--library-path={self.library_dir}",
                ]
                if _title:
                    add_command.extend(["--title", _title])
                if _authors:
                    add_command.extend(["--authors", _authors])
                if _cover and os.path.exists(_cover):
                    add_command.extend(["--cover", _cover])

                # Add identifiers if present; expect entries like "isbn:12345"
                try:
                    identifiers_list = meta[12] if isinstance(meta[12], (list, tuple)) else []
                except Exception:
                    identifiers_list = []
                for ident in identifiers_list:
                    if isinstance(ident, str) and ":" in ident and ident.strip():
                        add_command.extend(["--identifier", ident.strip()])

                result = subprocess.run(add_command, env=self.calibre_env, check=True, capture_output=True, text=True)
                added_ids = self._parse_added_book_ids((result.stdout or '') + '\n' + (result.stderr or ''))
                if added_ids:
                    self.last_added_book_ids = added_ids
                    self.last_added_book_id = added_ids[-1]
                else:
                    self._fallback_last_added_book_id()
            # calibredb accepted the file; everything below is best-effort follow-up
            imported = True
            print(f"[ingest-processor] Added {staged_path.stem} to Calibre database", flush=True)
            # No copy of the imported file is kept: the library holds it

            mark_ingest_batch_dirty()

            # calibre takes a PDF's author from the file: make it the people it names
            self.tidy_authors(self.last_added_book_ids or [])
            # calibre turns a PDF's Keywords into tags: keep only the subjects
            self.clear_details(self.last_added_book_ids or [])
            # calibre's cover for a PDF is page 1 as printed, often off-centre: centre it on the print
            self.centre_covers(self.last_added_book_ids or [])

            # Fetch metadata if enabled, prefer exact book id from calibredb
            if self.last_added_book_id is not None:
                self.fetch_metadata_if_enabled(book_id=self.last_added_book_id)
            else:
                self.fetch_metadata_if_enabled(staged_path.stem)

            # Ensure newly imported books have their timestamp set to the current time
            # so they appear at the top of "Recently Added" views.
            # calibredb sets timestamp from EPUB metadata (publication date), which can be
            # years in the past, making new imports invisible in recently-added sorting.
            if self.last_added_book_id is not None:
                try:
                    with sqlite3.connect(self.metadata_db, timeout=30) as con:
                        if not self._register_title_sort_function(con):
                            print("[ingest-processor] INFO: Skipping timestamp adjust (title_sort SQL function unavailable).", flush=True)
                        else:
                            cur = con.cursor()
                            now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S+00:00")
                            cur.execute('UPDATE books SET timestamp = ? WHERE id = ?', (now, self.last_added_book_id))
                            print(f"[ingest-processor] INFO: Set timestamp to {now} for newly imported book id={self.last_added_book_id}.", flush=True)
                except Exception as e:
                    print(f"[ingest-processor] WARN: Failed to set timestamp for new book: {e}", flush=True)

        except subprocess.CalledProcessError as e:
            print(f"[ingest-processor] {staged_path.stem} was not able to be added to the Calibre Library due to the following error:\nCALIBREDB EXIT/ERROR CODE: {e.returncode}\n{e.stderr}", flush=True)
            self.failure_reason = _bounded_reason(
                "calibredb exited with %s: %s" % (e.returncode, (e.stderr or "").strip()[-500:]))
            # Keep the exact file calibredb rejected;
            # the original ingest source is moved to failed/ separately by main()
            self.backup(str(staged_path), backup_type="failed")
        except Exception as e:
            print(f"[ingest-processor] ingest-processor ran into the following error:\n{e}", flush=True)
            self.failure_reason = _bounded_reason(str(e))
        finally:
            if staged_path.exists():
                os.remove(staged_path)
        return imported

    def _validate_book_exists(self, book_id: int) -> bool:
        """Check if a book with the given ID exists in the Calibre library"""
        try:
            with sqlite3.connect(self.metadata_db, timeout=30) as con:
                cur = con.cursor()
                row = cur.execute("SELECT id FROM books WHERE id = ?", (book_id,)).fetchone()
                return row is not None
        except Exception as e:
            print(f"[ingest-processor] ERROR: Failed to validate book_id {book_id}: {e}", flush=True)
            return False

    def add_format_to_book(self, book_id:int, book_path:str) -> bool:
        """Attach a new format file to an existing Calibre book using calibredb add_format.
        Returns True only if calibredb accepted the format."""
        source_path = Path(book_path)
        if not source_path.exists() or source_path.stat().st_size == 0:
            print(f"[ingest-processor] ERROR: Source file for add_format is missing or empty, skipping: {book_path}", flush=True)
            return False

        # Validate that the book exists before attempting to add format
        if not self._validate_book_exists(book_id):
            print(f"[ingest-processor] ERROR: Book ID {book_id} not found in library, cannot add format: {os.path.basename(book_path)}", flush=True)
            return False

        # Stage file for import
        staged_path = Path(self.staging_dir) / source_path.name
        try:
            shutil.copy2(source_path, staged_path)
        except Exception as e:
            print(f"[ingest-processor] ERROR: Failed to stage file for add_format: {e}", flush=True)
            return False

        added = False
        try:
            subprocess.run([
                "calibredb", "add_format", str(book_id), str(staged_path), f"--library-path={self.library_dir}"
            ], env=self.calibre_env, check=True, capture_output=True, text=True)
            added = True
            print(f"[ingest-processor] Added new format for book id {book_id}: {os.path.basename(str(staged_path))}", flush=True)
            mark_ingest_batch_dirty()
        except subprocess.CalledProcessError as e:
            stderr_output = e.stderr if e.stderr else "No error details available"
            print(f"[ingest-processor] Failed to add format for book id {book_id}: {os.path.basename(str(staged_path))}\nCALIBREDB EXIT/ERROR CODE: {e.returncode}\nError details: {stderr_output}", flush=True)
            self.failure_reason = _bounded_reason(
                "add_format exited with %s: %s" % (e.returncode, stderr_output.strip()[-500:]))
        except Exception as e:
            print(f"[ingest-processor] Unexpected error while adding format for book id {book_id}: {e}", flush=True)
            self.failure_reason = _bounded_reason(str(e))
        finally:
            if staged_path.exists():
                os.remove(staged_path)
        return added


    def tidy_authors(self, book_ids) -> None:
        if not _CPS_AVAILABLE or tidy_new_book_authors is None:
            return
        for book_id in book_ids:
            try:
                if tidy_new_book_authors(int(book_id), self.library_dir):
                    print(f"[ingest-processor] Cleaned up the authors of book id={book_id}", flush=True)
            except Exception as e:
                print(f"[ingest-processor] WARN: Could not tidy the authors of book id={book_id}: {e}", flush=True)

    def clear_details(self, book_ids) -> None:
        if not _CPS_AVAILABLE or clear_new_book_details is None:
            return
        for book_id in book_ids:
            try:
                if clear_new_book_details(int(book_id)):
                    print(f"[ingest-processor] Cleared the tags, publisher, languages and rating the file gave book id={book_id}", flush=True)
            except Exception as e:
                print(f"[ingest-processor] WARN: Could not clear the details the file gave book id={book_id}: {e}", flush=True)

    def centre_covers(self, book_ids) -> None:
        if not _CPS_AVAILABLE or recentre_new_book_cover is None:
            return
        for book_id in book_ids:
            try:
                if recentre_new_book_cover(int(book_id), self.library_dir):
                    print(f"[ingest-processor] Made or centred the cover of book id={book_id}", flush=True)
            except Exception as e:
                print(f"[ingest-processor] WARN: Could not make or centre the cover of book id={book_id}: {e}", flush=True)

    def fetch_metadata_if_enabled(self, book_title: str | None = None, book_id: int | None = None) -> None:
        """Fetch and apply metadata for newly ingested books if enabled"""
        if not _CPS_AVAILABLE:
            print("[ingest-processor] CPS modules not available, skipping metadata fetch", flush=True)
            return

        if fetch_and_apply_metadata is None:
            print("[ingest-processor] Metadata helper not available, skipping metadata fetch", flush=True)
            return

        try:
            with sqlite3.connect(self.metadata_db, timeout=30) as con:
                cur = con.cursor()
                if book_id is not None:
                    cur.execute("SELECT id, title FROM books WHERE id = ?", (int(book_id),))
                else:
                    # Fallback: most recently added book
                    cur.execute("SELECT id, title FROM books ORDER BY timestamp DESC LIMIT 1")
                result = cur.fetchone()

            if not result:
                print(f"[ingest-processor] Could not find book ID for metadata fetch: {book_title}", flush=True)
                return

            book_id = int(result[0])
            actual_title = result[1]

            print(f"[ingest-processor] Attempting to fetch metadata for: {actual_title}", flush=True)

            # Fetch and apply metadata (now admin-controlled only)
            if fetch_and_apply_metadata(book_id):
                print(f"[ingest-processor] Successfully fetched and applied metadata for: {actual_title}", flush=True)
            else:
                print(f"[ingest-processor] No metadata improvements found for: {actual_title}", flush=True)

        except Exception as e:
            print(f"[ingest-processor] Error fetching metadata: {e}", flush=True)


    def set_library_permissions(self):
        try:
            nsm = os.getenv("NETWORK_SHARE_MODE", "false").strip().lower() in ("1", "true", "yes", "on")
            if not nsm:
                subprocess.run(["chown", "-R", "abc:abc", self.library_dir], check=True)
            else:
                print(f"[ingest-processor] NETWORK_SHARE_MODE=true detected; skipping chown of {self.library_dir}", flush=True)
        except subprocess.CalledProcessError as e:
            print(f"[ingest-processor] An error occurred while attempting to recursively set ownership of {self.library_dir} to abc:abc. See the following error:\n{e}", flush=True)


def main(filepath=None):
    """Checks if filepath is a directory. If it is, main will be ran on every file in the given directory
    Inotifywait won't detect files inside folders if the folder was moved rather than copied"""

    if filepath is None:
        if len(sys.argv) < 2:
            print("[ingest-processor] ERROR: No file path provided", flush=True)
            print("[ingest-processor] Usage: python ingest_processor.py <filepath>", flush=True)
            sys.exit(1)
        filepath = sys.argv[1]

    if filepath == "--post-batch-follow-up":
        return run_post_batch_follow_up()

    nbp = None
    job_id = None
    parent_job_id = os.environ.get("LILY_REFRESH_JOB_ID") or None
    # What happens to the ingest source once we're done with it:
    #   "delete" - only after a confirmed successful import
    #   "keep"   - leave it in place (temp/ignored files, not ready yet)
    #   "failed" - move it into processed_books/failed (default for every other outcome,
    #              including unexpected exceptions)
    source_outcome = "failed"
    try:
        ##############################################################################################
        # Truncates the filename if it is too long
        MAX_LENGTH = 150
        filename = os.path.basename(filepath)
        name, ext = os.path.splitext(filename)
        allowed_len = MAX_LENGTH - len(ext)

        # Ignore sidecar manifests entirely (handled when the real file is processed)
        if filename.endswith((".cwa.json", ".cwa.failed.json")):
            print(f"[ingest-processor] Skipping sidecar manifest file: {filename}", flush=True)
            return 0

        if _is_missing_ingest_target(filepath):
            print(f"[ingest-processor] Skipping missing ingest target: {filepath}", flush=True)
            return 0

        if len(name) > allowed_len:
            new_name = name[:allowed_len] + ext
            new_path = os.path.join(os.path.dirname(filepath), new_name)
            os.rename(filepath, new_path)
            filepath = new_path
        ###############################################################################################
        if os.path.isdir(filepath) and Path(filepath).exists():
            exit_code = 0
            for filename in os.listdir(filepath):
                f = os.path.join(filepath, filename)
                if Path(f).exists():
                    child_exit = main(f)
                    if child_exit:
                        exit_code = int(child_exit)
            return exit_code

        if not initialize_runtime():
            return 2

        nbp = NewBookProcessor(filepath)

        try:
            from automation_jobs import create_job
            job_id = create_job("ingest", filename=nbp.filename,
                                parent_id=parent_job_id)
        except Exception as e:
            print(f"[ingest-processor] WARN: could not record ingest job: {e}", flush=True)
            job_id = None

        # If this file is not an ignored temporary, wait briefly for stability to avoid importing a still-growing file
        ext_tmp_check = Path(nbp.filename).suffix.replace('.', '')
        if ext_tmp_check not in nbp.ingest_ignored_formats:
            timeout_minutes = nbp.cwa_settings.get('ingest_timeout_minutes', 15)
            print(f"[ingest-processor] Checking if file is ready (timeout: {timeout_minutes} minutes): {nbp.filename}", flush=True)
            ready = nbp.is_file_in_use()
            if not ready:
                print(f"[ingest-processor] WARN: File did not become ready in time or vanished (after {timeout_minutes} minutes): {nbp.filename}", flush=True)
                source_outcome = "keep"
                return 0

        # Sidecar manifest handling for explicit actions (e.g., add_format)
        manifest_path = filepath + ".cwa.json"
        try:
            if Path(manifest_path).exists():
                with open(manifest_path, encoding='utf-8') as mf:
                    manifest = json.load(mf)
                action = manifest.get("action")
                if action == "add_format":
                    success = False
                    try:
                        book_id = int(manifest.get("book_id", -1))
                    except Exception:
                        book_id = -1

                    if book_id > -1:
                        # Validate book exists before attempting add_format
                        if nbp._validate_book_exists(book_id):
                            success = nbp.add_format_to_book(book_id, filepath)
                        else:
                            nbp.failure_reason = "target book %s is no longer in the library" % book_id
                            print(f"[ingest-processor] ERROR: Book ID {book_id} not found in library for {os.path.basename(filepath)}", flush=True)
                    else:
                        nbp.failure_reason = "manifest has no valid book_id"
                        print(f"[ingest-processor] ERROR: Invalid book_id in manifest for {os.path.basename(filepath)}", flush=True)
                    if not success and not getattr(nbp, "failure_reason", ""):
                        nbp.failure_reason = "add_format did not complete; check logs"

                    # Cleanup manifest: delete on success, preserve on failure for debugging
                    try:
                        if success:
                            os.remove(manifest_path)
                        else:
                            failed_manifest_path = manifest_path.replace(".cwa.json", ".cwa.failed.json")
                            os.rename(manifest_path, failed_manifest_path)
                            print(f"[ingest-processor] Preserved failed manifest: {os.path.basename(failed_manifest_path)}", flush=True)
                    except Exception as e:
                        print(f"[ingest-processor] WARN: Failed to handle manifest cleanup: {e}", flush=True)

                    # The finally block deletes the source on success or moves it to failed/
                    source_outcome = "delete" if success else "failed"
                    return 0
        except Exception as e:
            print(f"[ingest-processor] Error processing manifest file: {e}", flush=True)
            # Continue with normal processing if manifest handling fails

        # Check if the user has chosen to exclude files of this type from the ingest process
        # Remove . (dot), check is against exclude whitout dot
        ext = Path(nbp.filename).suffix.replace('.', '')
        if ext in nbp.ingest_ignored_formats:
            # Do NOT delete ignored temporary files; they may be renamed shortly (e.g. .uploading -> .epub)
            print(f"[ingest-processor] Skipping ignored/temporary file (no action taken): {nbp.filename}", flush=True)
            source_outcome = "keep"
            return 0

        imported = False
        if nbp.is_supported_audiobook():
            print(f"\n[ingest-processor]: {nbp.filename} is an audiobook, importing now...", flush=True)
            imported = nbp.add_book_to_library(filepath, False, Path(nbp.filename).suffix)
        elif nbp.input_format in nbp.supported_book_formats:
            print(f"\n[ingest-processor]: Importing {nbp.filename}...", flush=True)
            imported = nbp.add_book_to_library(filepath)
        else:
            print(f"[ingest-processor]: Cannot import {nbp.filepath}. {nbp.input_format} is not a known ebook format.", flush=True)
            nbp.failure_reason = "%s is not a known ebook format" % (nbp.input_format or "file")

        source_outcome = "delete" if imported else "failed"
        if not imported:
            print(f"[ingest-processor] {nbp.filename} was not imported; preserving it in failed backups", flush=True)
        return 0

    except Exception as e:
        print(f"[ingest-processor] Unexpected error during processing: {e}", flush=True)
        if nbp is not None and not getattr(nbp, "failure_reason", ""):
            detail = getattr(e, "stderr", None) or str(e)
            nbp.failure_reason = _bounded_reason(detail)
        raise
    finally:
        # Ensure cleanup always happens, even if an exception occurred
        if nbp:
            try:
                nbp.set_library_permissions()
            except Exception as e:
                print(f"[ingest-processor] Error setting library permissions during cleanup: {e}", flush=True)

            try:
                if source_outcome == "keep":
                    print(f"[ingest-processor] Skipping delete for ignored/temporary file: {nbp.filename}", flush=True)
                    _record_job(job_id, "skipped", "file kept in place (ignored or not ready)")
                elif source_outcome == "delete":
                    _record_job(job_id, "succeeded", book_id=getattr(nbp, "last_added_book_id", None))
                    nbp.delete_current_file()
                else:
                    _record_job(job_id, "failed",
                                getattr(nbp, "failure_reason", "") or "Import failed; check logs")
                    nbp.move_to_failed()
            except Exception as e:
                print(f"[ingest-processor] Error handling source file during cleanup (left in place): {e}", flush=True)

            try:
                # Cleanup the temp conversion folder, which now contains the staging dir
                shutil.rmtree(nbp.tmp_conversion_dir, ignore_errors=True)
            except Exception as e:
                print(f"[ingest-processor] Error cleaning up temp conversion directory: {e}", flush=True)

            try:
                del nbp # New in Version 2.0.0, should drastically reduce memory usage with large ingests
            except Exception:
                pass  # Ignore errors in cleanup

if __name__ == "__main__":
    sys.exit(main())
