# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import sqlite3
import os
import threading
from sqlite3 import Error as sqlError
import re
from datetime import datetime

from tabulate import tabulate

try:
    from cwa_stats_queries import CWAStatsQueries
except ModuleNotFoundError:  # imported as scripts.cwa_db without scripts/ on sys.path
    from scripts.cwa_stats_queries import CWAStatsQueries


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


class CWA_DB(CWAStatsQueries):
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
            "cwa_duplicate_resolutions",
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
        self.ensure_scheduled_jobs_schema()
        self.set_default_settings()


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


    def __exit__(self, exc_type, exc, tb):
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


    def _normalize_user_ids(self, user_id) -> list[int]:
        """Normalize user filters to a list of integer IDs."""
        if user_id is None:
            return []
        if isinstance(user_id, (list, tuple, set)):
            return [int(uid) for uid in user_id if uid is not None]
        try:
            return [int(user_id)]
        except (TypeError, ValueError):
            return []


    def _has_user_filter(self, user_id) -> bool:
        """Return True when a valid user filter is provided."""
        return len(self._normalize_user_ids(user_id)) > 0


    def _build_user_filter(self, user_id) -> str:
        """Builds SQL filter for a single user ID or list of user IDs."""
        user_ids = self._normalize_user_ids(user_id)
        if not user_ids:
            return ""
        if len(user_ids) == 1:
            return f" AND user_id = {user_ids[0]}"
        user_ids_csv = ",".join(str(uid) for uid in user_ids)
        return f" AND user_id IN ({user_ids_csv})"


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
        
        # Delete any settings in the db but not in the schema file
        for setting in cwa_setting_names:
            if setting not in self.cwa_default_settings.keys():
                try:
                    print(f"[cwa-db] Deprecated setting found from previous version of CWA, removing setting '{setting}' from cwa.db...")
                    self.cur.execute(f"ALTER TABLE cwa_settings DROP COLUMN {setting}")  
                    self.con.commit()
                    print(f"[cwa-db] Deprecated setting '{setting}' successfully removed from cwa.db!")
                except Exception as e:
                    print(f"[cwa-db] The following error occurred when trying to remove {setting} from cwa.db:\n{e}")
    
    
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

            cron_value, format_priority, ingest_ignored, automerge_value = row
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
        for line in self.schema:
            match = re.findall(setting, line)
            if match:
                try:
                    command = line.replace('\n', '').strip()
                    # Skip SQL comments
                    if command.startswith('--') or not command:
                        continue
                    command = command.replace(',', ';')
                    with open(os.path.join(self.db_path, '.cwa_db_debug'), 'a') as f:
                        f.write(command)
                    self.cur.execute(f"ALTER TABLE cwa_settings ADD {command}")  
                    self.con.commit()
                    return True
                except Exception as e:
                    print(f"[cwa-db] The following error occurred when trying to add {setting} to cwa.db:\n{e}")
                    return False
        print(f"[cwa-db] Error adding new setting to cwa.db: {setting}: Matching setting could not be found in schema file")
        return False

    def match_stat_table_columns_with_schema(self) -> None:
        """ Used to rename columns whose names have been changed in later versions and add columns added in later versions """
        # Produces a dict with all of the column names for each table, from the existing DB
        current_column_names = {}
        for table in self.stats_tables:
            try:
                self.cur.execute(f"SELECT * FROM {table}")
                setting_names = [header[0] for header in self.cur.description]
                current_column_names |= {table:setting_names}
            except sqlite3.OperationalError:
                # Table might not exist yet if it's new, skip it for now
                # It will be created by make_tables() if it doesn't exist
                current_column_names |= {table: []}

        # Produces a dict with all of the column names for each table, from the schema
        column_names_in_schema = {}
        for table in self.tables:
            column_names = []
            table_name = None  # Reset for each table
            table = table.split('\n')
            for line in table:
                if line[:27] == "CREATE TABLE IF NOT EXISTS ":
                    table_name = line[27:].replace('(', '').strip()
                elif line[:4] == "    ":
                    column_names.append(line.strip().split(' ')[0])
            if table_name is not None:  # Only add if table_name was actually found
                column_names_in_schema[table_name] = column_names

        for table in self.stats_tables:
            # Skip if table wasn't found in current DB (it was just created empty)
            if not current_column_names[table]:
                continue
            
            # Skip if table not found in schema (shouldn't happen but safety check)
            if table not in column_names_in_schema:
                print(f"[cwa-db] Warning: Table '{table}' in stats_tables but not found in schema")
                continue
            
            columns_added = False  # Track if we added any columns this iteration
                
            if len(current_column_names[table]) < len(column_names_in_schema[table]): # Adds new columns not yet in existing db
                num_new_columns = len(column_names_in_schema[table]) - len(current_column_names[table])
                for x in range(1, num_new_columns + 1):
                    if column_names_in_schema[table][-x] not in current_column_names[table]:
                        for line in self.schema:
                            matches = re.findall(column_names_in_schema[table][-x], line)
                            if matches:
                                # Extract column definition, remove trailing comma and SQL comments
                                new_column = line.strip()
                                if '--' in new_column:
                                    new_column = new_column[:new_column.index('--')].strip()
                                new_column = new_column.rstrip(',')
                                self.cur.execute(f"ALTER TABLE {table} ADD COLUMN {new_column}")
                                self.con.commit()
                                print(f'[cwa-db] Missing Column detected in cwa.db. Added new column "{column_names_in_schema[table][-x]}" to table "{table}" in cwa.db')
                                columns_added = True
                                break  # Found and added the column, move to next missing column
            
            # Only check for column renames if we didn't just add columns
            # (newly added columns are correct, don't try to rename them)
            if not columns_added and len(current_column_names[table]) == len(column_names_in_schema[table]):
                # Check if all columns exist but just in different order (SQLite ADD COLUMN always appends)
                current_set = set(current_column_names[table])
                schema_set = set(column_names_in_schema[table])
                
                if current_set == schema_set:
                    # All columns exist, just in different order - this is fine, SQLite can't reorder
                    continue
                
                # Columns differ, check for actual renames needed
                for x in range(len(column_names_in_schema[table])):
                    if current_column_names[table][x] != column_names_in_schema[table][x]:
                        self.cur.execute(f"ALTER TABLE {table} RENAME COLUMN {current_column_names[table][x]} TO {column_names_in_schema[table][x]}")
                        self.con.commit()
                        print(f'[cwa-db] Fixed column mismatch between versions. Column "{current_column_names[table][x]}" in table "{table}" renamed to "{column_names_in_schema[table][x]}"', flush=True)


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
            if setting == "default_settings":
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


    def get_import_history(self, verbose: bool):
        results = self.cur.execute("SELECT timestamp, filename, original_backed_up FROM cwa_import ORDER BY timestamp DESC;").fetchall()
        if verbose:
            results.reverse()
            return results
        else:
            newest_ten = []
            x = 0
            for result in results:
                newest_ten.insert(0, result)
                x += 1
                if x == 10:
                    break
            return newest_ten


    def import_add_entry(self, filename, original_backed_up):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.cur.execute("INSERT INTO cwa_import(timestamp, filename, original_backed_up) VALUES (?, ?, ?);", (timestamp, filename, original_backed_up))
        self.con.commit()


    def get_stat_totals(self) -> dict[str,int]:
        totals = {"cwa_enforcement":0}
        
        for table in totals:
            try:
                totals[table] = self.cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            except Exception as e:
                print(f"[cwa-db] ERROR - The following error occurred when fetching stat totals:\n{e}")

        return totals

    # ==============================
    # Scheduled Jobs (Auto-Send)
    # ==============================

    def ensure_scheduled_jobs_schema(self) -> None:
        """Add missing columns to cwa_scheduled_jobs if older table exists."""
        try:
            cols = [r[1] for r in self.cur.execute("PRAGMA table_info('cwa_scheduled_jobs')").fetchall()]
            if cols:
                if 'scheduler_job_id' not in cols:
                    self.cur.execute("ALTER TABLE cwa_scheduled_jobs ADD COLUMN scheduler_job_id TEXT DEFAULT ''")
                    self.con.commit()
        except Exception:
            # If table doesn't exist yet, it will be created from schema
            pass

    def scheduled_add_autosend(self, book_id: int, user_id: int, run_at_utc_iso: str, username: str, title: str) -> int | None:
        """Insert a scheduled auto-send job and return its row id."""
        try:
            created_at = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
            self.cur.execute(
                """
                INSERT INTO cwa_scheduled_jobs(job_type, book_id, user_id, username, title, run_at_utc, created_at_utc, state)
                VALUES(?,?,?,?,?,?,?, 'scheduled')
                """,
                ('auto_send', int(book_id), int(user_id), username, title, run_at_utc_iso, created_at)
            )
            self.con.commit()
            return self.cur.lastrowid
        except Exception as e:
            print(f"[cwa-db] ERROR adding scheduled auto-send: {e}")
            return None

    def scheduled_mark_dispatched(self, row_id: int) -> bool:
        try:
            # Only transition scheduled -> dispatched; ignore if already cancelled/dispatched
            self.cur.execute("UPDATE cwa_scheduled_jobs SET state='dispatched' WHERE id=? AND state='scheduled'", (int(row_id),))
            self.con.commit()
            return self.cur.rowcount > 0
        except Exception as e:
            print(f"[cwa-db] ERROR marking scheduled job dispatched: {e}")
            return False

    def scheduled_mark_cancelled(self, row_id: int) -> None:
        try:
            self.cur.execute("UPDATE cwa_scheduled_jobs SET state='cancelled' WHERE id=?", (int(row_id),))
            self.con.commit()
        except Exception as e:
            print(f"[cwa-db] ERROR marking scheduled job cancelled: {e}")

    def scheduled_cancel_for_book(self, book_id: int) -> int:
        """Cancel all scheduled jobs (auto-send, etc.) for a specific book
        
        Args:
            book_id: The book ID whose scheduled jobs should be cancelled
            
        Returns:
            int: Number of jobs cancelled
        """
        try:
            self.cur.execute(
                "UPDATE cwa_scheduled_jobs SET state='cancelled' WHERE book_id=? AND state='scheduled'",
                (int(book_id),)
            )
            self.con.commit()
            cancelled_count = self.cur.rowcount
            if cancelled_count > 0:
                print(f"[cwa-db] Cancelled {cancelled_count} scheduled job(s) for book {book_id}", flush=True)
            return cancelled_count
        except Exception as e:
            print(f"[cwa-db] ERROR cancelling scheduled jobs for book {book_id}: {e}", flush=True)
            return 0

    def scheduled_update_job_id(self, row_id: int, scheduler_job_id: str) -> None:
        try:
            self.cur.execute("UPDATE cwa_scheduled_jobs SET scheduler_job_id=? WHERE id=?", (scheduler_job_id, int(row_id)))
            self.con.commit()
        except Exception as e:
            print(f"[cwa-db] ERROR updating scheduler_job_id: {e}")

    def scheduled_get_by_id(self, row_id: int):
        try:
            row = self.cur.execute("SELECT * FROM cwa_scheduled_jobs WHERE id=?", (int(row_id),)).fetchone()
            if not row:
                return None
            cols = [d[0] for d in self.cur.description]
            return dict(zip(cols, row))
        except Exception as e:
            print(f"[cwa-db] ERROR fetching scheduled job by id: {e}")
            return None

    def scheduled_get_upcoming_autosend(self, limit: int = 50):
        try:
            now_utc = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
            rows = self.cur.execute(
                """
                SELECT id, book_id, user_id, username, title, run_at_utc, state
                FROM cwa_scheduled_jobs
                WHERE job_type='auto_send' AND state='scheduled' AND run_at_utc >= ?
                ORDER BY run_at_utc ASC
                LIMIT ?
                """,
                (now_utc, int(limit))
            ).fetchall()
            cols = [d[0] for d in self.cur.description]
            return [dict(zip(cols, r)) for r in rows]
        except Exception as e:
            print(f"[cwa-db] ERROR fetching upcoming scheduled auto-sends: {e}")
            return []

    def scheduled_get_pending_autosend(self):
        """Return all not-yet-dispatched auto-sends due in the future (for rehydration)."""
        try:
            now_utc = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
            rows = self.cur.execute(
                """
                SELECT id, book_id, user_id, username, title, run_at_utc
                FROM cwa_scheduled_jobs
                WHERE job_type='auto_send' AND state='scheduled' AND run_at_utc >= ?
                ORDER BY run_at_utc ASC
                """,
                (now_utc,)
            ).fetchall()
            cols = [d[0] for d in self.cur.description]
            return [dict(zip(cols, r)) for r in rows]
        except Exception as e:
            print(f"[cwa-db] ERROR fetching pending scheduled auto-sends: {e}")
            return []

    def log_activity(self, user_id, user_name, event_type, item_id=None, item_title=None, extra_data=None):
        """Logs a user activity event to the database with device detection."""
        try:
            import json
            
            # Parse extra_data if it's a string
            if isinstance(extra_data, str):
                try:
                    extra_data_dict = json.loads(extra_data)
                except:
                    # If not JSON, treat as simple string (legacy format compatibility)
                    extra_data_dict = {'format': extra_data}
            elif isinstance(extra_data, dict):
                extra_data_dict = extra_data
            else:
                extra_data_dict = {}
            
            # Add device type detection using User-Agent
            try:
                from flask import request
                user_agent = request.headers.get('User-Agent', '').lower()
                
                # Simple device type detection
                if 'mobile' in user_agent or 'android' in user_agent or 'iphone' in user_agent:
                    device_type = 'mobile'
                elif 'tablet' in user_agent or 'ipad' in user_agent:
                    device_type = 'tablet'
                else:
                    device_type = 'desktop'
                
                extra_data_dict['device_type'] = device_type
            except:
                # If flask context not available, skip device detection
                pass
            
            # Convert back to JSON string
            extra_data_json = json.dumps(extra_data_dict) if extra_data_dict else None
            
            self.cur.execute("""
                INSERT INTO cwa_user_activity (user_id, user_name, event_type, item_id, item_title, extra_data)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, user_name, event_type, item_id, item_title, extra_data_json))
            self.con.commit()
        except Exception as e:
            print(f"[cwa-db] Error logging activity: {e}")

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

    def get_resolution_history(self, limit=100):
        """Get recent resolution history"""
        import json
        try:
            self.cur.execute("""
                SELECT id, timestamp, group_hash, group_title, group_author, 
                       kept_book_id, deleted_book_ids, strategy, trigger_type, user_id, notes
                FROM cwa_duplicate_resolutions 
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (limit,))
            
            results = []
            for row in self.cur.fetchall():
                results.append({
                    'id': row[0],
                    'timestamp': row[1],
                    'group_hash': row[2],
                    'group_title': row[3],
                    'group_author': row[4],
                    'kept_book_id': row[5],
                    'deleted_book_ids': json.loads(row[6]),
                    'strategy': row[7],
                    'trigger_type': row[8],
                    'user_id': row[9],
                    'notes': row[10]
                })
            return results
        except Exception as e:
            print(f"[cwa-db] Error getting resolution history: {e}")
            return []


def main():
    """Create the CWA database if missing and apply any pending schema migration."""
    db = CWA_DB()
    print(f"[cwa-db] Schema ready: {os.path.abspath(db.db_path + db.db_file)}")


if __name__ == "__main__":
    main()
