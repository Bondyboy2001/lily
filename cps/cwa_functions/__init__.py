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
    library_ops   Convert Library / EPUB Fixer routes, runners and scheduling
    theme_profile theme switch and profile picture routes

Everything other modules import from ``cps.cwa_functions`` is re-exported here.
Module-level state (e.g. the duplicate-scan debounce timer and lock) lives only in
its owning submodule; patch it there (``cps.cwa_functions.ingest``), not here.
"""

from .common import (switch_theme, library_refresh, convert_library, epub_fixer, cwa_stats,
                     cwa_check_status, cwa_settings, cwa_logs, profile_pictures, cwa_internal,
                     log, LOG_ARCHIVE, DIRS_JSON)
# Import order mirrors the old single module (web, scheduler, worker, tasks) to keep
# circular-import behaviour the same.
from .stats import (cwa_scheduled_cancel, get_cwa_stats, headers, cwa_stats_show, export_stats_csv,
                    debug_stats_data, cwa_scheduled_upcoming, cwa_scheduled_upcoming_ops,
                    show_full_enforcement, show_full_enforcement_path, show_full_imports,
                    show_full_conversions, show_full_epub_fixer, show_full_epub_fixer_with_paths_fixes)
from .ingest import (_duplicate_full_scan_running, get_ingest_dir, get_ingest_status, _coerce_book_ids,
                     get_ingest_queue_size, refresh_library, cwa_library_refresh,
                     get_library_refresh_messages, cwa_internal_schedule_auto_send,
                     cwa_internal_queue_duplicate_scan, cwa_internal_run_duplicate_scan,
                     cwa_internal_duplicate_scan_status, queue_debounced_duplicate_scan,
                     duplicate_scan_debounce_pending, cwa_internal_reconnect_db)
# download_current_log / get_status exist for both services; as in the old single module,
# the package-level names refer to the EPUB Fixer versions.
from .library_ops import (_schedule_library_op, _schedule_library_op_response,
                          cwa_internal_schedule_convert_library, cwa_internal_schedule_epub_fixer,
                          convert_library_start, get_tmp_conversion_dir, empty_tmp_con_dir,
                          is_convert_library_finished, kill_convert_library, show_convert_library_page,
                          schedule_convert_library, show_convert_library_logs, download_current_log,
                          start_convert_library_run, request_convert_library_cancel, start_conversion,
                          cancel_convert_library, get_status, epub_fixer_start, is_epub_fixer_finished,
                          kill_epub_fixer, show_epub_fixer_page, schedule_epub_fixer, show_epub_fixer_logs,
                          start_epub_fixer_run, request_epub_fixer_cancel, start_epub_fixer,
                          run_epub_fixer_for_book, cancel_epub_fixer)
from .logs import (cwa_flash_status, download_log, read_log, extract_progress, archive_run_log,
                   get_logs_from_archive, get_log_dates)
from .settings import (parse_metadata_providers_enabled, validate_and_cleanup_provider_enabled_map,
                       set_cwa_settings, get_next_duplicate_scan_run)
from .theme_profile import cwa_switch_theme, user_profiles_json, set_profile_picture
