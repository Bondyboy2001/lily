CREATE TABLE IF NOT EXISTS cwa_enforcement(
    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, 
    timestamp TEXT NOT NULL,
    book_id INTEGER NOT NULL, 
    book_title TEXT NOT NULL,
    author TEXT NOT NULL, 
    file_path TEXT NOT NULL, 
    trigger_type TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cwa_settings(
    default_settings SMALLINT DEFAULT 1 NOT NULL,
    auto_backup_imports SMALLINT DEFAULT 1 NOT NULL,
    cwa_update_notifications SMALLINT DEFAULT 1 NOT NULL,
    auto_ingest_ignored_formats TEXT DEFAULT "" NOT NULL,
    auto_ingest_automerge TEXT DEFAULT "new_record" NOT NULL,
    ingest_timeout_minutes INTEGER DEFAULT 15 NOT NULL,
    ingest_stale_temp_minutes INTEGER DEFAULT 120 NOT NULL,
    ingest_stale_temp_interval INTEGER DEFAULT 600 NOT NULL,
    auto_metadata_enforcement SMALLINT DEFAULT 1 NOT NULL,
    auto_metadata_fetch_enabled SMALLINT DEFAULT 1 NOT NULL,
    cover_download_max_mb INTEGER DEFAULT 15 NOT NULL,
    duplicate_detection_title SMALLINT DEFAULT 1 NOT NULL,
    duplicate_detection_author SMALLINT DEFAULT 1 NOT NULL,
    duplicate_detection_language SMALLINT DEFAULT 1 NOT NULL,
    duplicate_detection_series SMALLINT DEFAULT 0 NOT NULL,
    duplicate_detection_publisher SMALLINT DEFAULT 0 NOT NULL,
    duplicate_detection_format SMALLINT DEFAULT 0 NOT NULL,
    -- Number of nightly sqlite backups (app.db, cwa.db, metadata.db) kept in /config/backup/db/
    db_backup_keep_count INTEGER DEFAULT 7 NOT NULL,
    -- Where nightly snapshots go. '' = DB_BACKUP_DIR env var, else /config/backup/db/.
    -- Point this at a different volume from /config so a lost /config volume doesn't take the backups with it.
    db_backup_dir TEXT DEFAULT '' NOT NULL,
    -- Days to keep files in /config/processed_books/{imported,failed}, pruned nightly. '0' = keep forever.
    -- Stored as TEXT so the generic settings form doesn't treat it as a checkbox.
    processed_books_retention_days TEXT DEFAULT '30' NOT NULL,
    -- Days to keep deleted-book recovery archives under book_recovery/, pruned nightly.
    -- '0' = keep forever. Ages are read from each entry's manifest, not file mtimes.
    book_recovery_retention_days TEXT DEFAULT '0' NOT NULL,
    -- Optional second folder that nightly receives a copy of new/changed book files. '' = off.
    library_mirror_dir TEXT DEFAULT '' NOT NULL,
    -- Duplicate notification and auto-resolution settings
    duplicate_detection_enabled SMALLINT DEFAULT 1 NOT NULL,
    duplicate_notifications_enabled SMALLINT DEFAULT 1 NOT NULL,
    duplicate_auto_resolve_enabled SMALLINT DEFAULT 0 NOT NULL,
    duplicate_auto_resolve_strategy TEXT DEFAULT 'newest' NOT NULL,
    duplicate_auto_resolve_cooldown_minutes INTEGER DEFAULT 0 NOT NULL,  -- 0 = disabled, >0 = minutes between auto-resolutions
    duplicate_format_priority TEXT DEFAULT '{"EPUB":100,"PDF":60,"DJVU":25}' NOT NULL,
    duplicate_scan_enabled SMALLINT DEFAULT 1 NOT NULL,
    duplicate_scan_frequency TEXT DEFAULT 'after_import' NOT NULL,
    duplicate_scan_cron TEXT DEFAULT '' NOT NULL,
    duplicate_scan_debounce_seconds INTEGER DEFAULT 60 NOT NULL
);

-- Duplicate detection cache table
CREATE TABLE IF NOT EXISTS cwa_duplicate_cache (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- Singleton table, only one row
    scan_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    duplicate_groups_json TEXT,  -- JSON serialized duplicate groups
    total_count INTEGER DEFAULT 0,
    scan_pending INTEGER DEFAULT 1,  -- 1=needs scan, 0=cache valid
    last_scanned_book_id INTEGER DEFAULT 0  -- Track last scanned book for incremental updates
);

-- Insert default row for cache table
INSERT OR IGNORE INTO cwa_duplicate_cache (id, scan_pending) VALUES (1, 1);

-- Persisted duplicate key index for bounded duplicate-cache maintenance.
CREATE TABLE IF NOT EXISTS cwa_duplicate_book_keys (
    book_id INTEGER PRIMARY KEY,
    normalized_title TEXT NOT NULL DEFAULT '',
    normalized_author TEXT NOT NULL DEFAULT '',
    normalized_language TEXT NOT NULL DEFAULT '',
    normalized_series TEXT NOT NULL DEFAULT '',
    normalized_publisher TEXT NOT NULL DEFAULT '',
    format_signature TEXT NOT NULL DEFAULT '',
    duplicate_key TEXT NOT NULL,
    criteria_fingerprint TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cwa_duplicate_book_keys_key
    ON cwa_duplicate_book_keys(criteria_fingerprint, duplicate_key);

-- Files that hold the same document as another book's file: same format, same
-- byte size and nearly all 1 KB blocks equal. Books sharing a match_key are
-- duplicates whatever their metadata. Size and mtime let an unchanged file skip
-- being read again.
CREATE TABLE IF NOT EXISTS cwa_duplicate_file_matches (
    book_id INTEGER NOT NULL,
    format TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    file_mtime_ns INTEGER NOT NULL,
    match_key TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cwa_duplicate_file_matches_file
    ON cwa_duplicate_file_matches(book_id, format);

CREATE INDEX IF NOT EXISTS idx_cwa_duplicate_file_matches_key
    ON cwa_duplicate_file_matches(match_key);

-- Auto-resolution audit log
CREATE TABLE IF NOT EXISTS cwa_duplicate_resolutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    group_hash TEXT NOT NULL,
    group_title TEXT,
    group_author TEXT,
    kept_book_id INTEGER NOT NULL,
    deleted_book_ids TEXT NOT NULL,  -- JSON array of deleted IDs
    strategy TEXT NOT NULL,  -- 'newest', 'highest_quality_format', 'most_metadata', 'largest_file_size'
    trigger_type TEXT NOT NULL,  -- 'manual', 'scheduled', 'automatic'
    user_id INTEGER,  -- NULL for automatic, admin user ID for manual
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_duplicate_resolutions_timestamp ON cwa_duplicate_resolutions(timestamp);
CREATE INDEX IF NOT EXISTS idx_duplicate_resolutions_group_hash ON cwa_duplicate_resolutions(group_hash);

CREATE TABLE IF NOT EXISTS cwa_operation_jobs (
    id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL,
    user_id INTEGER,
    filename TEXT,
    parent_id TEXT,
    state TEXT NOT NULL,
    started_utc TEXT NOT NULL,
    finished_utc TEXT,
    error TEXT DEFAULT '',
    pid INTEGER NOT NULL,
    book_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_cwa_operation_jobs_started
    ON cwa_operation_jobs(started_utc);
CREATE INDEX IF NOT EXISTS idx_cwa_operation_jobs_kind_state
    ON cwa_operation_jobs(kind, state);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cwa_operation_jobs_one_running
    ON cwa_operation_jobs(kind) WHERE state='running' AND kind='refresh';

-- Rebuild metadata, how far a run got (one row while a run is unfinished) so the next can carry on
CREATE TABLE IF NOT EXISTS metadata_rebuild_progress(
    id INTEGER PRIMARY KEY CHECK (id = 1),
    next_book_id INTEGER NOT NULL,  -- every book below this id has been checked
    checked INTEGER NOT NULL,
    updated INTEGER NOT NULL,
    covers INTEGER NOT NULL,
    total INTEGER NOT NULL
);

-- The provider cover last weighed against each book's own, so a later lookup need not download it again to compare
CREATE TABLE IF NOT EXISTS metadata_cover_checks(
    book_id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    cover TEXT NOT NULL  -- the book's cover.jpg once weighed, as "size:mtime"
);

-- Books whose cover was chosen by hand (ticked in Fetch metadata, or uploaded): a PDF's cover
-- is otherwise always its first page (cps/pdf_cover.py)
CREATE TABLE IF NOT EXISTS hand_covers(
    book_id INTEGER PRIMARY KEY
);

-- Books edited by hand (in the editor, by applying a Fetch Metadata result, or by undoing a
-- lookup): automatic lookups keep their title and authors and only fill empty fields
CREATE TABLE IF NOT EXISTS hand_edited(
    book_id INTEGER PRIMARY KEY
);

-- What each automatic lookup changed, kept as the book was before it: Book Details' Undo reads it
CREATE TABLE IF NOT EXISTS metadata_changes(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT '',  -- the provider whose record was applied
    changed_at TEXT NOT NULL,  -- UTC, ISO 8601
    before TEXT NOT NULL  -- JSON: the fields the lookup changed, as they were
);

-- What the last metadata lookup of each book found, for the library's Metadata filter and Retry failed
CREATE TABLE IF NOT EXISTS metadata_lookups(
    book_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,  -- matched, nomatch (every provider answered, none has it) or failed (one didn't answer)
    source TEXT NOT NULL DEFAULT '',  -- the provider that matched, as it names itself
    checked_at TEXT NOT NULL  -- UTC, ISO 8601
);

-- Every automatic metadata lookup, newest last, for the Logs page: the book, what the lookup
-- found and what it changed. Only the newest LOOKUP_LOG_KEEP are kept (cwa_db.py)
CREATE TABLE IF NOT EXISTS metadata_lookup_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',  -- the book's title after the lookup: it may be deleted since
    status TEXT NOT NULL,  -- matched, nomatch or failed, as metadata_lookups
    source TEXT NOT NULL DEFAULT '',  -- the provider that matched
    checked_at TEXT NOT NULL,  -- UTC, ISO 8601
    changes TEXT NOT NULL DEFAULT '{}'  -- JSON: {field: [before, after]} for each field it changed
);

-- A book's edition, set by hand in the editor ("6" for the sixth): calibre has no field for it
CREATE TABLE IF NOT EXISTS book_editions(
    book_id INTEGER PRIMARY KEY,
    edition INTEGER NOT NULL
);

-- A book's volume, set by hand in the editor ("3" for volume 3): calibre has no field for it
CREATE TABLE IF NOT EXISTS book_volumes(
    book_id INTEGER PRIMARY KEY,
    volume INTEGER NOT NULL
);
