# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""cwa.db: Lily's settings and statistics database, its schema sync and migrations."""

import sqlite3
import os
import threading
from sqlite3 import Error as sqlError
import re
from datetime import datetime, timezone

from tabulate import tabulate


class CWADBConnectionError(sqlError):
    """Raised when cwa.db cannot be opened.

    Subclasses sqlite3.Error so existing ``except sqlite3.Error`` handlers keep
    working. Previously connect_to_db() called sys.exit(0), which raised
    SystemExit inside web requests and worker threads.
    """


# Schema creation + settings/column migrations only need to run once per process
# per database file. Every CWA_DB() construction used to re-run them (12 CREATE
# TABLEs with a commit each, several ALTER/UPDATE passes), and there are ~85 call
# sites, several per page render.
_SCHEMA_INIT_LOCK = threading.Lock()
_SCHEMA_INITIALIZED: set[str] = set()
_SCHEMA_FILE_CACHE: dict[str, tuple[list[str], list[str]]] = {}
# Default cwa_settings values parsed from the schema file, per schema path. Parsing
# used to run on every CWA_DB() construction.
_DEFAULT_SETTINGS_CACHE: dict[str, dict] = {}


def invalidate_schema_cache(db_file: str | None = None) -> None:
    """Forget that schema setup ran, so the next CWA_DB() re-runs migrations.

    Call after cwa.db was replaced on disk (e.g. a restore). With no argument,
    all database paths are invalidated.
    """
    with _SCHEMA_INIT_LOCK:
        if db_file is None:
            _SCHEMA_INITIALIZED.clear()
        else:
            _SCHEMA_INITIALIZED.discard(os.path.abspath(db_file))


def _network_share_mode() -> bool:
    """Same NETWORK_SHARE_MODE parsing as cps/db.py (WAL is unsafe on network shares)."""
    return os.getenv('NETWORK_SHARE_MODE', 'False').lower() in ('1', 'true', 'yes', 'on')


def _read_schema_file(schema_path: str) -> tuple[list[str], list[str]]:
    """Returns (statements, raw non-blank lines) for the schema file, cached per process."""
    cached = _SCHEMA_FILE_CACHE.get(schema_path)
    if cached is not None:
        return list(cached[0]), list(cached[1])
    schema = []
    with open(schema_path, 'r') as f:
        for line in f:
            if line != "\n":
                schema.append(line)
    tables = "".join(schema)
    tables = tables.split(';')
    tables.pop(-1)
    for x in range(len(tables)):
        tables[x] = tables[x] + ";"
    _SCHEMA_FILE_CACHE[schema_path] = (tables, schema)
    return list(tables), list(schema)


def _strip_sql_comment(line: str) -> str:
    """Removes a trailing `-- comment` that is not inside a quoted string."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == '-' and line[i:i + 2] == '--':
            return line[:i]
    return line


def parse_schema_columns(tables: list[str]) -> dict[str, dict[str, str]]:
    """{table: {column: column definition}} for each CREATE TABLE statement.

    Column names are matched as whole identifiers (the first token of the line),
    never as substrings, so e.g. `timestamp` never matches `scan_timestamp`.
    Definition order is preserved.
    """
    columns: dict[str, dict[str, str]] = {}
    for statement in tables:
        table_name = None
        table_columns: dict[str, str] = {}
        for line in statement.split('\n'):
            if line.startswith("CREATE TABLE IF NOT EXISTS "):
                table_name = line[len("CREATE TABLE IF NOT EXISTS "):].replace('(', '').strip()
            elif table_name is not None and line[:4] == "    ":
                definition = _strip_sql_comment(line).strip().rstrip(',').strip()
                if not definition or definition.startswith(')'):
                    continue
                name = definition.split()[0]
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                    table_columns[name] = definition
        if table_name is not None:
            columns[table_name] = table_columns
    return columns


# Explicit, ordered, run-once migrations for cwa.db, applied after the additive
# schema sync. Each entry is (version, name, fn(cursor)). Versions must be unique
# and increasing; never renumber or edit an entry once released. Use these for
# anything the additive "add missing column" pass cannot express (renames, data
# rewrites, dropping a column after its data was moved). A migration runs inside
# a transaction together with its bookkeeping row, so it is applied exactly once.
#
# Example:
#   def _m2_rename_foo(cur):
#       cur.execute("ALTER TABLE cwa_import RENAME COLUMN foo TO bar")
#   MIGRATIONS = [(2, "rename cwa_import.foo to bar", _m2_rename_foo)]
def _m1_settings_page_defaults(cur):
    # The settings page no longer offers these: duplicate detection is always on and
    # Hardcover auto-fetch is gone, so pin them for libraries that changed them before.
    cur.execute("UPDATE cwa_settings SET duplicate_detection_enabled=1, duplicate_scan_enabled=1, "
                "hardcover_auto_fetch_enabled=0")


def _m2_drop_duplicate_file_keys(cur) -> None:
    # Exact-hash file matches, replaced by cwa_duplicate_file_matches (block-level)
    cur.execute("DROP TABLE IF EXISTS cwa_duplicate_file_keys")


MIGRATIONS: list = [(1, "always detect duplicates, no Hardcover auto-fetch", _m1_settings_page_defaults),
                    (2, "drop exact-hash duplicate file keys", _m2_drop_duplicate_file_keys)]
SCHEMA_MIGRATIONS_TABLE = "cwa_schema_migrations"


class CWA_DB:
    def __init__(self, verbose=False):
        self.verbose = verbose

        self.db_file = "cwa.db"
        # CWA_DB_PATH lets tests point at an isolated directory; production always uses /config/
        self.db_path = os.path.join(os.environ.get("CWA_DB_PATH", "/config"), "")
        full_path = os.path.abspath(self.db_path + self.db_file)
        if not os.path.exists(full_path):
            # A missing/replaced file must get its schema created again
            invalidate_schema_cache(full_path)
        self.con, self.cur = self.connect_to_db() # type: ignore

        # Support both Docker and CI environments for schema path
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.schema_path = os.path.join(script_dir, "cwa_schema.sql")
        self.stats_tables = [
            "cwa_enforcement",
            "cwa_import",
            "cwa_user_activity",
            "cwa_duplicate_cache",
            "cwa_duplicate_book_keys",
            "cwa_duplicate_file_matches",
            "cwa_duplicate_resolutions",
            "cwa_operation_jobs",
        ]
        self.tables, self.schema = _read_schema_file(self.schema_path)
        self.cwa_default_settings = self.get_cwa_default_settings()

        with _SCHEMA_INIT_LOCK:
            if full_path not in _SCHEMA_INITIALIZED:
                self.run_schema_setup()
                _SCHEMA_INITIALIZED.add(full_path)

        self.cwa_settings = self.get_cwa_settings()


    def run_schema_setup(self) -> None:
        """Creates tables and applies settings/column migrations. Runs once per process per db file."""
        self.make_tables()
        self.ensure_settings_schema_match()
        self.match_stat_table_columns_with_schema()
        self.run_migrations()
        self.set_default_settings()


    def run_migrations(self, migrations=None) -> list[int]:
        """Applies each pending migration in MIGRATIONS once, in version order.

        Applied versions are recorded in cwa_schema_migrations. A failing migration
        is rolled back and stops the run (later ones depend on it); it is retried
        on the next start. Returns the versions applied by this call.
        """
        migrations = MIGRATIONS if migrations is None else migrations
        self.cur.execute(
            f"CREATE TABLE IF NOT EXISTS {SCHEMA_MIGRATIONS_TABLE}("
            "version INTEGER PRIMARY KEY NOT NULL, "
            "name TEXT NOT NULL, "
            "applied_at TEXT NOT NULL)"
        )
        self.con.commit()
        applied = {row[0] for row in self.cur.execute(f"SELECT version FROM {SCHEMA_MIGRATIONS_TABLE}")}
        newly_applied = []
        for version, name, fn in sorted(migrations, key=lambda m: m[0]):
            if version in applied:
                continue
            try:
                # Explicit BEGIN: the sqlite3 module does not open a transaction
                # before DDL, so without it an ALTER could commit on its own.
                self.cur.execute("BEGIN")
                fn(self.cur)
                self.cur.execute(
                    f"INSERT INTO {SCHEMA_MIGRATIONS_TABLE}(version, name, applied_at) VALUES (?, ?, ?)",
                    (version, name, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                self.con.commit()
                newly_applied.append(version)
                print(f"[cwa-db] Applied migration {version}: {name}", flush=True)
            except Exception as e:
                self.con.rollback()
                print(f"[cwa-db] Migration {version} ({name}) failed and was rolled back; "
                      f"later migrations were not applied: {e}", flush=True)
                break
        return newly_applied


    def close(self) -> None:
        """Closes the underlying connection. Safe to call more than once."""
        con = getattr(self, "con", None)
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


    def __enter__(self):
        return self


    def __exit__(self, _exc_type, exc, tb):
        self.close()
        return False


    def connect_to_db(self) -> tuple[sqlite3.Connection, sqlite3.Cursor]:
        """Establishes connection with the db or makes one if one doesn't already exist"""
        try:
            os.makedirs(self.db_path, exist_ok=True)
            con = sqlite3.connect(self.db_path + self.db_file, timeout=30)
        except (sqlError, OSError) as e:
            print(f"[cwa-db]: The following error occurred while trying to connect to the CWA Enforcement DB: {e}")
            raise CWADBConnectionError(f"Could not open {self.db_path + self.db_file}: {e}") from e
        try:
            con.execute("PRAGMA busy_timeout=30000")
            if not _network_share_mode():
                # journal_mode is persistent in the file; this is a cheap no-op once set
                con.execute("PRAGMA journal_mode=WAL")
                # Durable across app crashes in WAL mode; avoids an fsync on every commit
                con.execute("PRAGMA synchronous=NORMAL")
        except sqlError as e:
            print(f"[cwa-db] Warning: could not configure cwa.db pragmas: {e}")
        cur = con.cursor()
        if self.verbose:
            print("[cwa-db]: Connection with the CWA Enforcement DB Successful!")
        return con, cur


    def make_tables(self) -> tuple[list[str], list[str]]:
        """Creates the tables for the CWA DB if they don't already exist"""
        tables, schema = _read_schema_file(self.schema_path)
        for table in tables:
            self.cur.execute(table)
        self.con.commit()

        return tables, schema


    def get_cwa_default_settings(self):
        schema_path = getattr(self, "schema_path", None)
        cached = _DEFAULT_SETTINGS_CACHE.get(schema_path) if schema_path else None
        if cached is not None:
            return dict(cached)
        for table in self.tables:
            if "cwa_settings" in table:
                settings_table = table.strip()
                break

        settings_lines = []
        for line in settings_table.split('\n'):
            stripped = line.strip()
            # Skip comment lines and empty lines
            if line[:4] == "    " and not stripped.startswith('--') and stripped:
                settings_lines.append(stripped)

        default_settings = {}
        for line in settings_lines:
            # Extract setting name and DEFAULT value more carefully
            # Format: setting_name TYPE DEFAULT value [NOT NULL]
            if ' DEFAULT ' not in line:
                continue

            setting_name = line.split()[0]

            # Extract everything after DEFAULT
            default_start = line.index(' DEFAULT ') + len(' DEFAULT ')
            remainder = line[default_start:].strip()

            # Handle different value types
            if remainder.startswith("'") or remainder.startswith('"'):
                # String value with quotes - could be '', "value", or JSON
                quote_char = remainder[0]
                # Find the matching closing quote
                end_quote = remainder.index(quote_char, 1)
                setting_value = remainder[1:end_quote]  # Extract content between quotes
            elif ' ' in remainder:
                # Value followed by other keywords (like NOT NULL)
                setting_value = remainder.split()[0]
                try:
                    setting_value = int(setting_value)
                except ValueError:
                    pass
            else:
                # Simple value at end of line
                setting_value = remainder
                try:
                    setting_value = int(setting_value)
                except ValueError:
                    pass

            default_settings |= {setting_name:setting_value}

        if schema_path:
            _DEFAULT_SETTINGS_CACHE[schema_path] = dict(default_settings)
        return default_settings


    def ensure_settings_schema_match(self) -> None:
        self.cur.execute("SELECT * FROM cwa_settings")
        cwa_setting_names = [header[0] for header in self.cur.description]

        # Add any settings present in the schema file but not in the db
        newly_added_settings = []
        for setting in self.cwa_default_settings.keys():
            if setting not in cwa_setting_names:
                success = self.add_missing_setting(setting)
                if success:
                    print(f"[cwa-db] Setting '{setting}' successfully added to cwa.db!")
                    newly_added_settings.append(setting)

        # Sync newly added settings with schema defaults
        # This handles cases where schema default was updated after column was added
        if newly_added_settings:
            self.sync_new_settings_with_defaults(newly_added_settings)

        # Fix for issue #903: Repair incorrectly parsed default values from older versions
        self.fix_malformed_setting_values()

        # Settings in the db but not in the schema file are left in place. They may
        # belong to a newer version (downgrade) or a renamed setting whose value a
        # migration still needs; dropping them silently deleted user config.
        unknown_settings = [s for s in cwa_setting_names if s not in self.cwa_default_settings]
        if unknown_settings:
            print(f"[cwa-db] Keeping {len(unknown_settings)} cwa_settings column(s) not in the current schema "
                  f"(from another version?): {', '.join(unknown_settings)}", flush=True)


    def sync_new_settings_with_defaults(self, newly_added_settings) -> None:
        """Sync newly added settings to match schema defaults

        This ensures that if a column was added with one default value, then the schema
        was updated with a different default, existing databases get the new default.
        """
        try:
            # Get current values
            self.cur.execute("SELECT * FROM cwa_settings")
            headers = [header[0] for header in self.cur.description]
            current_values = dict(zip(headers, self.cur.fetchone()))

            # Update any newly added settings that don't match schema defaults
            updates_made = []
            for setting in newly_added_settings:
                current_val = current_values.get(setting)
                expected_val = self.cwa_default_settings.get(setting)

                # Compare with type handling (int vs string)
                if str(current_val) != str(expected_val):
                    self.cur.execute(f"UPDATE cwa_settings SET {setting}=?", (expected_val,))
                    updates_made.append(f"{setting}: {current_val} -> {expected_val}")

            if updates_made:
                self.con.commit()
                print(f"[cwa-db] Synced {len(updates_made)} new setting(s) with schema defaults:")
                for update in updates_made:
                    print(f"[cwa-db]   - {update}")
        except Exception as e:
            print(f"[cwa-db] Warning: Failed to sync new settings with defaults: {e}")


    def fix_malformed_setting_values(self) -> None:
        """Fix settings that may have been incorrectly parsed in older versions.

        Issue #903: Old parser would save '' as literal two-quote string and truncate JSON.
        This migration fixes existing databases with malformed values.
        """
        try:
            self.cur.execute(
                "SELECT duplicate_scan_cron, duplicate_format_priority, "
                "auto_ingest_ignored_formats, auto_ingest_automerge "
                "FROM cwa_settings"
            )
            row = self.cur.fetchone()
            if not row:
                return

            cron_value, format_priority, _, _ = row
            fixes_made = []

            def _strip_quotes(value: str | None) -> str | None:
                if value is None:
                    return None
                value = str(value).strip()
                if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                return value.strip()

            def _normalize_format_list(value: str | None) -> str | None:
                if value is None:
                    return None
                parts = [p for p in str(value).split(',')]
                cleaned = [(_strip_quotes(p) or "").strip().lower() for p in parts]
                cleaned = [p for p in cleaned if p]
                return ",".join(cleaned)

            # Fix duplicate_scan_cron if it's the literal string "''"
            if cron_value == "''":
                self.cur.execute("UPDATE cwa_settings SET duplicate_scan_cron = ''")
                fixes_made.append("duplicate_scan_cron: removed literal quotes")

            # Fix duplicate_format_priority if it's malformed (not valid JSON or missing formats)
            if format_priority:
                try:
                    import json
                    parsed = json.loads(format_priority)
                    # Check if it has at least the basic formats
                    if not isinstance(parsed, dict) or 'EPUB' not in parsed:
                        raise ValueError("Missing expected format data")
                except (json.JSONDecodeError, ValueError):
                    # Reset to default if malformed
                    default_json = self.cwa_default_settings.get('duplicate_format_priority', '{}')
                    self.cur.execute("UPDATE cwa_settings SET duplicate_format_priority = ?", (default_json,))
                    fixes_made.append("duplicate_format_priority: reset to default due to malformed JSON")

            # Generic cleanup: strip wrapped quotes for TEXT settings and normalize
            # format/automerge-like values without hardcoding each column.
            try:
                self.cur.execute("SELECT * FROM cwa_settings")
                headers = [header[0] for header in self.cur.description]
                values = self.cur.fetchone()
                if values:
                    current_settings = dict(zip(headers, values))

                    self.cur.execute("PRAGMA table_info(cwa_settings)")
                    columns = self.cur.fetchall()
                    text_columns = {
                        col[1] for col in columns
                        if isinstance(col[2], str) and col[2].upper().startswith("TEXT")
                    }

                    json_settings = {
                        'metadata_provider_hierarchy',
                        'metadata_providers_enabled',
                        'duplicate_format_priority',
                    }

                    for column_name in text_columns:
                        if column_name not in current_settings:
                            continue
                        raw_value = current_settings[column_name]
                        if raw_value is None or not isinstance(raw_value, str):
                            continue

                        cleaned_value = _strip_quotes(raw_value)
                        if cleaned_value is None:
                            continue

                        normalized_value = cleaned_value

                        # Normalize format/automerge-like values for consistency
                        if column_name not in json_settings:
                            column_lower = column_name.lower()
                            if ',' in cleaned_value and 'format' in column_lower:
                                parts = [p.strip() for p in cleaned_value.split(',')]
                                parts = [(_strip_quotes(p) or '').strip() for p in parts]
                                parts = [p for p in parts if p]
                                parts = [p.lower() for p in parts]
                                normalized_value = ','.join(parts)
                            elif 'format' in column_lower or 'automerge' in column_lower:
                                normalized_value = cleaned_value.strip().lower()

                        if normalized_value != raw_value:
                            self.cur.execute(
                                f"UPDATE cwa_settings SET {column_name} = ?",
                                (normalized_value,)
                            )
                            fixes_made.append(f"{column_name}: stripped quotes/normalized")
            except Exception as e:
                print(f"[cwa-db] Warning: Generic settings normalization failed: {e}")

            if fixes_made:
                self.con.commit()
                print(f"[cwa-db] Fixed {len(fixes_made)} malformed setting value(s) from previous version:")
                for fix in fixes_made:
                    print(f"[cwa-db]   - {fix}")
        except Exception as e:
            print(f"[cwa-db] Warning: Failed to fix malformed setting values: {e}")


    def add_missing_setting(self, setting) -> bool:
        # Exact identifier match within the cwa_settings table definition
        definition = parse_schema_columns(self.tables).get("cwa_settings", {}).get(setting)
        if definition is None:
            print(f"[cwa-db] Error adding new setting to cwa.db: {setting}: Matching setting could not be found in schema file")
            return False
        try:
            self.cur.execute(f"ALTER TABLE cwa_settings ADD COLUMN {definition}")
            self.con.commit()
            return True
        except Exception as e:
            print(f"[cwa-db] The following error occurred when trying to add {setting} to cwa.db:\n{e}")
            return False

    def match_stat_table_columns_with_schema(self) -> None:
        """Adds columns that exist in the schema file but not yet in the db (additive only).

        Columns are never renamed or dropped here: the old heuristic renamed columns
        by position whenever the column counts matched, which could relabel data
        under the wrong name. Renames belong in MIGRATIONS. Extra columns (e.g. from
        a newer version) are preserved and logged.
        """
        schema_columns = parse_schema_columns(self.tables)
        for table in self.stats_tables:
            try:
                existing = [row[1] for row in self.cur.execute(f"PRAGMA table_info('{table}')").fetchall()]
            except sqlite3.OperationalError:
                existing = []
            # Table missing: make_tables() creates it from the schema
            if not existing:
                continue
            if table not in schema_columns:
                print(f"[cwa-db] Warning: Table '{table}' in stats_tables but not found in schema")
                continue

            for column, definition in schema_columns[table].items():
                if column in existing:
                    continue
                try:
                    try:
                        self.cur.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
                    except sqlite3.OperationalError as e:
                        # SQLite refuses NOT NULL without a default on a non-empty
                        # table; existing rows can only get NULL, so add it nullable.
                        if "NOT NULL" not in str(e):
                            raise
                        relaxed = re.sub(r"\s+NOT\s+NULL", "", definition, flags=re.IGNORECASE)
                        self.cur.execute(f"ALTER TABLE {table} ADD COLUMN {relaxed}")
                    self.con.commit()
                    print(f'[cwa-db] Missing Column detected in cwa.db. Added new column "{column}" to table "{table}" in cwa.db')
                except Exception as e:
                    print(f'[cwa-db] Could not add column "{column}" to table "{table}": {e}', flush=True)

            extra = [c for c in existing if c not in schema_columns[table]]
            if extra:
                print(f'[cwa-db] Keeping column(s) in "{table}" not in the current schema: {", ".join(extra)}', flush=True)


    def set_default_settings(self, force=False) -> None:
        """Sets default settings for new tables and keeps track if the user is using the default settings or not.\n\n
        If the argument 'force' is set to True, the function instead sets all settings to their default values"""
        if force:
            for setting in self.cwa_default_settings:
                self.cur.execute(f"UPDATE cwa_settings SET {setting}=?;", (self.cwa_default_settings[setting],))
                self.con.commit()
            print("[cwa-db] CWA Default Settings successfully applied!")
            return
        try:
            self.cur.execute("SELECT * FROM cwa_settings")
            setting_names = [header[0] for header in self.cur.description]
            current_settings = [dict(zip(setting_names,row)) for row in self.cur.fetchall()][0]

        except IndexError:
            print("[cwa-db]: No existing CWA settings detected, applying default CWA settings...")
            for setting in self.cwa_default_settings:
                self.cur.execute(f"UPDATE cwa_settings SET {setting}=?;", (self.cwa_default_settings[setting],))
                self.con.commit()
            print("[cwa-db] CWA Default Settings successfully applied!")
            return

        default_check = True
        for setting in setting_names:
            if setting == "default_settings" or setting not in self.cwa_default_settings:
                # Columns kept from another version don't count towards "defaults"
                continue
            elif current_settings[setting] != self.cwa_default_settings[setting]:
                default_check = False
                self.cur.execute("UPDATE cwa_settings SET default_settings=0 WHERE default_settings=1;")
                self.con.commit()
                break
        if default_check:
            self.cur.execute("UPDATE cwa_settings SET default_settings=1 WHERE default_settings=0;")
            self.con.commit()

        if self.verbose:
            print("[cwa-db] CWA Settings loaded successfully")


    def get_cwa_settings(self) -> dict:
        """Gets the current cwa_settings values from the table of the same name in cwa.db and returns them as a dict"""
        self.cur.execute("SELECT * FROM cwa_settings")
        rows = self.cur.fetchall()
        headers = [header[0] for header in self.cur.description]
        if rows == []: # If settings table is empty, populates it with default values
            self.cur.execute("INSERT INTO cwa_settings DEFAULT VALUES;")
            self.con.commit()
            self.cur.execute("SELECT * FROM cwa_settings")
            rows = self.cur.fetchall()
            headers = [header[0] for header in self.cur.description]
        cwa_settings = dict(zip(headers, rows[0]))

        # Define default values for new columns (in case db doesn't have them yet)
        schema_defaults = {
            'hardcover_auto_fetch_enabled': 0,
            'hardcover_auto_fetch_schedule': 'weekly',
            'hardcover_auto_fetch_schedule_day': 'sunday',
            'hardcover_auto_fetch_schedule_hour': 2,
            'hardcover_auto_fetch_min_confidence': 0.85,
            'hardcover_auto_fetch_batch_size': 50,
            'hardcover_auto_fetch_rate_limit': 5.0,
            'archived_cleanup_enabled': 1,
            'archived_cleanup_schedule': 'daily',
            'archived_cleanup_schedule_day': 'sunday',
            'archived_cleanup_schedule_hour': 3,
            'ingest_stale_temp_minutes': 120,
            'ingest_stale_temp_interval': 600,
            'cover_download_max_mb': 15,
            'db_backup_keep_count': 7
        }

        # Apply defaults for missing keys
        for key, default_value in schema_defaults.items():
            if key not in cwa_settings:
                cwa_settings[key] = default_value

        # Define which settings should remain as integers (not converted to boolean)
        integer_settings = ['ingest_timeout_minutes', 'ingest_stale_temp_minutes', 'ingest_stale_temp_interval', 'auto_send_delay_minutes', 'hardcover_auto_fetch_batch_size', 'hardcover_auto_fetch_schedule_hour', 'duplicate_scan_hour', 'duplicate_scan_chunk_size', 'duplicate_scan_debounce_seconds', 'duplicate_auto_resolve_cooldown_minutes', 'archived_cleanup_schedule_hour', 'cover_download_max_mb', 'db_backup_keep_count']

        # Define which settings should remain as floats (not converted to boolean)
        float_settings = ['hardcover_auto_fetch_min_confidence', 'hardcover_auto_fetch_rate_limit']

        # Define which settings should remain as JSON strings (not split by comma)
        json_settings = ['metadata_provider_hierarchy', 'metadata_providers_enabled', 'duplicate_format_priority']

        for header in headers:
            if isinstance(cwa_settings[header], int) and header not in integer_settings and header not in float_settings:
                cwa_settings[header] = bool(cwa_settings[header])
            elif isinstance(cwa_settings[header], str) and ',' in cwa_settings[header] and header not in json_settings:
                cwa_settings[header] = cwa_settings[header].split(',')

        return cwa_settings


    def update_cwa_settings(self, result) -> None:
        """Sets settings using POST request from set_cwa_settings()"""
        for setting in result.keys():
            if setting == "auto_ingest_ignored_formats":
                result[setting] = ','.join(result[setting])

            # Skip updates for unset values to avoid NOT NULL constraint failures
            if result[setting] is None:
                continue

            try:
                # Use parameterized queries to safely handle non-English characters and quotes
                self.cur.execute(f"UPDATE cwa_settings SET {setting}=?;", (result[setting],))
                self.con.commit()
            except Exception as e:
                print(f"[CWA_DB] Error updating setting '{setting}' with value '{result[setting]}': {e}")
                # Continue to next setting instead of failing completely
                continue
        self.set_default_settings()
        self.cwa_settings = self.get_cwa_settings()


    def enforce_add_entry_from_log(self, log_info: dict, trigger_type: str = "auto -log"):
        """Adds an entry to the db from a change log file"""
        self.cur.execute(
            "INSERT INTO cwa_enforcement(timestamp, book_id, book_title, author, file_path, trigger_type) VALUES (?, ?, ?, ?, ?, ?);",
            (log_info['timestamp'], log_info['book_id'], log_info['title'], log_info['authors'], log_info['file_path'], trigger_type)
        )
        self.con.commit()


    def enforce_add_entry_from_dir(self, book_dicts: list[dict[str,str]]):
        """Adds an entry to the db when cover_enforcer is ran with a directory"""
        for book in book_dicts:
            self.cur.execute("INSERT INTO cwa_enforcement(timestamp, book_id, book_title, author, file_path, trigger_type) VALUES (?, ?, ?, ?, ?, ?);", (book['timestamp'], book['book_id'], book['book_title'], book['author_name'], book['file_path'], 'manual -dir'))
            self.con.commit()


    def enforce_add_entry_from_all(self, book_dicts: list[dict[str,str]]):
        """Adds an entry to the db when cover_enforcer is ran with the -all flag"""
        for book in book_dicts:
            self.cur.execute("INSERT INTO cwa_enforcement(timestamp, book_id, book_title, author, file_path, trigger_type) VALUES (?, ?, ?, ?, ?, ?);", (book['timestamp'], book['book_id'], book['book_title'], book['author_name'], book['file_path'], 'manual -all'))
            self.con.commit()


    def enforce_show(self, paths: bool, verbose: bool, web_ui=False):
        results_no_path = self.cur.execute("SELECT timestamp, book_id, book_title, author, trigger_type FROM cwa_enforcement ORDER BY timestamp DESC;").fetchall()
        results_with_path = self.cur.execute("SELECT timestamp, book_id, file_path FROM cwa_enforcement ORDER BY timestamp DESC;").fetchall()
        if paths:
            results = results_with_path
            headers = ["Timestamp", "Book ID", "Book Title", "Book Author", "Trigger Type"]
        else:
            results = results_no_path
            headers = ["Timestamp","Book ID", "Filepath"]

        if verbose:
            results.reverse()
            if web_ui:
                return results
            else:
                print(f"\n{tabulate(results, headers=headers, tablefmt='rounded_grid')}\n")
        else:
            newest_ten = []
            x = 0
            for result in results:
                newest_ten.insert(0, result)
                x += 1
                if x == 10:
                    break
            if web_ui:
                return newest_ten
            else:
                print(f"\n{tabulate(newest_ten, headers=headers, tablefmt='rounded_grid')}\n")


    def import_add_entry(self, filename, original_backed_up):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.cur.execute("INSERT INTO cwa_import(timestamp, filename, original_backed_up) VALUES (?, ?, ?);", (timestamp, filename, original_backed_up))
        self.con.commit()


    # ==============================
    # Scheduled Jobs (Auto-Send)
    # ==============================


    def get_rebuild_progress(self) -> dict | None:
        """How far an unfinished Rebuild metadata run got, or None when the last one finished."""
        row = self.cur.execute("SELECT next_book_id, checked, updated, covers, total "
                               "FROM metadata_rebuild_progress WHERE id = 1").fetchone()
        return dict(zip(("next_book_id", "checked", "updated", "covers", "total"), row)) if row else None

    def save_rebuild_progress(self, next_book_id: int, checked: int, updated: int, covers: int, total: int) -> None:
        self.cur.execute("INSERT OR REPLACE INTO metadata_rebuild_progress "
                         "(id, next_book_id, checked, updated, covers, total) VALUES (1, ?, ?, ?, ?, ?)",
                         (next_book_id, checked, updated, covers, total))
        self.con.commit()

    def clear_rebuild_progress(self) -> None:
        self.cur.execute("DELETE FROM metadata_rebuild_progress")
        self.con.commit()

    def get_cover_check(self, book_id: int) -> tuple[str, str] | None:
        """(provider cover URL, the book's cover as it was) from the last time one was weighed."""
        return self.cur.execute("SELECT url, cover FROM metadata_cover_checks WHERE book_id = ?",
                                (book_id,)).fetchone()

    def save_cover_check(self, book_id: int, url: str, cover: str) -> None:
        self.cur.execute("INSERT OR REPLACE INTO metadata_cover_checks (book_id, url, cover) VALUES (?, ?, ?)",
                         (book_id, url, cover))
        self.con.commit()

    def save_metadata_lookup(self, book_id: int, status: str, source: str = '') -> None:
        """Note what a metadata lookup of the book found: matched, nomatch or failed."""
        self.cur.execute("INSERT OR REPLACE INTO metadata_lookups (book_id, status, source, checked_at) "
                         "VALUES (?, ?, ?, ?)",
                         (book_id, status, source or '', datetime.now(timezone.utc).isoformat(timespec='seconds')))
        self.con.commit()

    def get_metadata_lookup(self, book_id: int) -> dict | None:
        """{status, source, checked_at} from the book's last lookup, or None when it has had none."""
        row = self.cur.execute("SELECT status, source, checked_at FROM metadata_lookups WHERE book_id = ?",
                               (book_id,)).fetchone()
        return dict(zip(("status", "source", "checked_at"), row)) if row else None

    def metadata_lookup_ids(self, status: str | None = None) -> list[int]:
        """The books whose last lookup ended in `status`; every book looked up when it is None."""
        if status is None:
            rows = self.cur.execute("SELECT book_id FROM metadata_lookups")
        else:
            rows = self.cur.execute("SELECT book_id FROM metadata_lookups WHERE status = ?", (status,))
        return [row[0] for row in rows]

    def invalidate_duplicate_cache(self):
        """Mark duplicate cache as needing refresh"""
        try:
            self.cur.execute("""
                UPDATE cwa_duplicate_cache
                SET scan_pending = 1
                WHERE id = 1
            """)
            self.con.commit()
            return True
        except Exception as e:
            print(f"[cwa-db] Error invalidating duplicate cache: {e}")
            return False

    def get_duplicate_cache(self):
        """Get cached duplicate scan results"""
        import json
        try:
            self.cur.execute("""
                SELECT scan_timestamp, duplicate_groups_json, total_count, scan_pending, last_scanned_book_id
                FROM cwa_duplicate_cache
                WHERE id = 1
            """)
            row = self.cur.fetchone()
            if row and row[1]:  # Has cached data
                return {
                    'scan_timestamp': row[0],
                    'duplicate_groups': json.loads(row[1]),
                    'total_count': row[2],
                    'scan_pending': bool(row[3]),
                    'last_scanned_book_id': row[4]
                }
            return None
        except Exception as e:
            print(f"[cwa-db] Error getting duplicate cache: {e}")
            return None

    def update_duplicate_cache(self, duplicate_groups, total_count, max_book_id=None):
        """Update duplicate cache with fresh scan results

        Args:
            duplicate_groups: List of duplicate group dictionaries
            total_count: Total number of duplicate groups found
            max_book_id: Maximum book ID in metadata.db (optional, for incremental scanning)
        """
        import json
        from datetime import datetime
        try:
            # Serialize duplicate groups to JSON (extract only serializable data)
            serializable_groups = []
            for group in duplicate_groups:
                serializable_group = {
                    'title': group.get('title', ''),
                    'author': group.get('author', ''),
                    'count': group.get('count', 0),
                    'group_hash': group.get('group_hash', ''),
                    'legacy_group_hash': group.get('legacy_group_hash', ''),
                    'same_file': bool(group.get('same_file')),
                    'book_ids': [book.id for book in group.get('books', [])]
                }
                serializable_groups.append(serializable_group)

            groups_json = json.dumps(serializable_groups)

            # Update cache with optional max_book_id for incremental scanning
            if max_book_id is not None:
                self.cur.execute("""
                    UPDATE cwa_duplicate_cache
                    SET scan_timestamp = ?,
                        duplicate_groups_json = ?,
                        total_count = ?,
                        scan_pending = 0,
                        last_scanned_book_id = ?
                    WHERE id = 1
                """, (datetime.now().isoformat(), groups_json, total_count, max_book_id))
            else:
                self.cur.execute("""
                    UPDATE cwa_duplicate_cache
                    SET scan_timestamp = ?,
                        duplicate_groups_json = ?,
                        total_count = ?,
                        scan_pending = 0
                    WHERE id = 1
                """, (datetime.now().isoformat(), groups_json, total_count))
            self.con.commit()
            return True
        except Exception as e:
            print(f"[cwa-db] Error updating duplicate cache: {e}")
            return False

    def log_duplicate_resolution(self, group_hash, group_title, group_author, kept_book_id,
                                 deleted_book_ids, strategy, trigger_type, user_id=None, notes=None):
        """Log a duplicate resolution to audit table"""
        import json
        try:
            # Explicit local timestamp: the column's schema DEFAULT CURRENT_TIMESTAMP is
            # UTC, but duplicate_scan.py's cooldown check compares against datetime.now()
            # (local). Passing it explicitly keeps this table consistent with every other
            # stats table (cwa_import, cwa_enforcement, etc.), which all stamp local time.
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            self.cur.execute("""
                INSERT INTO cwa_duplicate_resolutions
                (timestamp, group_hash, group_title, group_author, kept_book_id, deleted_book_ids,
                 strategy, trigger_type, user_id, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, group_hash, group_title, group_author, kept_book_id,
                  json.dumps(deleted_book_ids), strategy, trigger_type, user_id, notes))
            self.con.commit()
            return True
        except Exception as e:
            print(f"[cwa-db] Error logging duplicate resolution: {e}")
            return False


def main():
    """Create the CWA database if missing and apply any pending schema migration."""
    db = CWA_DB()
    print(f"[cwa-db] Schema ready: {os.path.abspath(db.db_path + db.db_file)}")


if __name__ == "__main__":
    main()
