# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Lily/CWA-specific routes and helpers, split by responsibility.

    common        blueprints, logger, shared paths
    settings      /cwa-settings page + metadata-provider settings helpers
    stats         stats pages, CSV export, scheduled job list/cancel
    logs          service status check, log archive routes + helpers
    ingest        library refresh, ingest helpers, internal endpoints used by the
                  ingest process (auto-send, debounced duplicate scans, DB reconnect)

Everything other modules import from ``cps.cwa_functions`` is re-exported here.
Module-level state (e.g. the duplicate-scan debounce timer and lock) lives only in
its owning submodule; patch it there (``cps.cwa_functions.ingest``), not here.
"""

from .common import (library_refresh, cwa_stats,
                     cwa_check_status, cwa_settings, cwa_logs, cwa_internal,
                     log, LOG_ARCHIVE, DIRS_JSON)
# Import order mirrors the old single module (web, scheduler, worker, tasks) to keep
# circular-import behaviour the same.
from .stats import (cwa_scheduled_cancel, get_cwa_stats, headers, cwa_stats_show, export_stats_csv,
                    cwa_scheduled_upcoming,
                    show_full_enforcement, show_full_enforcement_path, show_full_imports)
from .ingest import (_duplicate_full_scan_running, get_ingest_dir, get_ingest_status, _coerce_book_ids,
                     get_ingest_queue_size, refresh_library, cwa_library_refresh,
                     get_library_refresh_messages, cwa_internal_schedule_auto_send,
                     cwa_internal_queue_duplicate_scan, cwa_internal_run_duplicate_scan,
                     cwa_internal_duplicate_scan_status, queue_debounced_duplicate_scan,
                     duplicate_scan_debounce_pending, cwa_internal_reconnect_db)
from .logs import cwa_flash_status, download_log, read_log
from .settings import (parse_metadata_providers_enabled, validate_and_cleanup_provider_enabled_map,
                       set_cwa_settings, get_next_duplicate_scan_run)

# The imports above are the package's public facade, not local use. Keep them
# listed so linters treat them as intentional re-exports.
__all__ = [
    # common
    "library_refresh", "cwa_stats", "cwa_check_status", "cwa_settings", "cwa_logs",
    "cwa_internal", "log", "LOG_ARCHIVE", "DIRS_JSON",
    # stats
    "cwa_scheduled_cancel", "get_cwa_stats", "headers", "cwa_stats_show",
    "export_stats_csv", "cwa_scheduled_upcoming", "show_full_enforcement",
    "show_full_enforcement_path", "show_full_imports",
    # ingest
    "_duplicate_full_scan_running", "get_ingest_dir", "get_ingest_status",
    "_coerce_book_ids", "get_ingest_queue_size", "refresh_library",
    "cwa_library_refresh", "get_library_refresh_messages",
    "cwa_internal_schedule_auto_send", "cwa_internal_queue_duplicate_scan",
    "cwa_internal_run_duplicate_scan", "cwa_internal_duplicate_scan_status",
    "queue_debounced_duplicate_scan", "duplicate_scan_debounce_pending",
    "cwa_internal_reconnect_db",
    # logs
    "cwa_flash_status", "download_log", "read_log",
    # settings
    "parse_metadata_providers_enabled", "validate_and_cleanup_provider_enabled_map",
    "set_cwa_settings", "get_next_duplicate_scan_run",
]
