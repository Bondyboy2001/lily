# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Lily settings page (/cwa-settings) and metadata-provider settings helpers."""

from flask import redirect, flash, url_for, request
from flask_babel import gettext as _

from .. import config, ub, calibre_db
from ..usermanagement import login_required_if_no_ano
from ..admin import admin_required
from ..render_template import render_title_template

from datetime import datetime

# common puts the scripts dir on sys.path, so it must be imported before cwa_db
from .common import cwa_settings, log
from cwa_db import CWA_DB


def _monthly_schedule_day(submitted_values, current_value):
    """Day of month (1-28, as a string like the column) for a monthly schedule.

    The weekday <select> and the day-of-month <input> share one field name, so take the
    first numeric submitted value, then the stored value, then 1.
    """
    for value in list(submitted_values or []) + [current_value]:
        try:
            return str(max(1, min(28, int(str(value).strip()))))
        except (TypeError, ValueError):
            continue
    return "1"


def parse_metadata_providers_enabled(raw_value):
    """
    Parse the metadata_providers_enabled setting from various formats into a dict.
    
    Args:
        raw_value: The raw value from database/settings (str, dict, bytes, or None)
        
    Returns:
        dict: Provider ID to enabled status mapping. Empty dict on error.
    """
    import json
    
    try:
        # Handle None/null values
        if raw_value is None:
            return {}
            
        # Handle bytes (from some database drivers)
        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode('utf-8', errors='ignore')
        
        # Handle string (most common case)
        if isinstance(raw_value, str):
            s = raw_value.strip()
            # Handle empty strings
            if not s:
                return {}
            # Strip surrounding single quotes if present from schema default
            if s.startswith("'") and s.endswith("'"):
                s = s[1:-1]
            # Handle empty string after quote stripping
            if not s:
                return {}
            data = json.loads(s)
            return data if isinstance(data, dict) else {}
        
        # Handle dict (already parsed)
        elif isinstance(raw_value, dict):
            return raw_value
        
        # Unknown type, return empty dict
        else:
            return {}
            
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        return {}

def validate_and_cleanup_provider_enabled_map(enabled_map, available_provider_ids):
    """
    Validate and cleanup the provider enabled map.
    
    Args:
        enabled_map (dict): Current provider enabled map
        available_provider_ids (list): List of valid provider IDs
        
    Returns:
        dict: Cleaned up enabled map with only valid providers
    """
    if not isinstance(enabled_map, dict):
        return {}
    
    if not isinstance(available_provider_ids, (list, tuple, set)):
        return {}
    
    # Keep only valid provider IDs and boolean values
    cleaned_map = {}
    for provider_id, enabled in enabled_map.items():
        if (isinstance(provider_id, str) and 
            provider_id.strip() and  # Non-empty string
            provider_id in available_provider_ids):
            # Convert to boolean, handling various truthy/falsy values
            cleaned_map[provider_id] = bool(enabled)
    
    return cleaned_map

##————————————————————————————————————————————————————————————————————————————##
##                                                                            ##
##                              CWA SETTINGS PAGE                             ##
##                                                                            ##
##————————————————————————————————————————————————————————————————————————————##

@cwa_settings.route("/cwa-settings", methods=["GET", "POST"])
@login_required_if_no_ano
@admin_required
def set_cwa_settings():
    cwa_db = CWA_DB()
    cwa_default_settings = cwa_db.cwa_default_settings
    cwa_settings = cwa_db.cwa_settings
    previous_koreader_enabled = bool(cwa_settings.get('koreader_sync_enabled', 0))

    ignorable_formats = ['acsm', 'azw', 'azw3', 'azw4', 'cbz',
                        'cbr', 'cb7', 'cbc', 'chm',
                        'djvu', 'docx', 'epub', 'fb2',
                        'fbz', 'html', 'htmlz', 'kepub', 'lit',
                        'lrf', 'mobi', 'odt', 'pdf',
                        'prc', 'pdb', 'pml', 'rb',
                        'rtf', 'snb', 'tcr', 'txt', 'txtz',
                        'kfx', 'kfx-zip']
    target_formats = ['epub', 'azw3', 'kepub', 'mobi', 'pdf']
    automerge_options = ['ignore', 'overwrite', 'new_record']
    autoingest_options = ['ignore', 'overwrite', 'new_record']

    boolean_settings = []
    string_settings = []
    list_settings = []
    integer_settings = ['ingest_timeout_minutes', 'ingest_stale_temp_minutes', 'ingest_stale_temp_interval', 'auto_send_delay_minutes', 'hardcover_auto_fetch_batch_size', 'hardcover_auto_fetch_schedule_hour', 'duplicate_scan_hour', 'duplicate_scan_chunk_size', 'duplicate_scan_debounce_seconds', 'duplicate_auto_resolve_cooldown_minutes', 'archived_cleanup_schedule_hour', 'cover_download_max_mb', 'db_backup_keep_count']  # Special handling for integer settings
    float_settings = ['hardcover_auto_fetch_min_confidence', 'hardcover_auto_fetch_rate_limit']  # Special handling for float settings
    json_settings = ['metadata_provider_hierarchy', 'metadata_providers_enabled', 'duplicate_format_priority']  # Special handling for JSON settings
    skip_settings = ['auto_convert_ignored_formats', 'auto_ingest_ignored_formats', 'auto_convert_retained_formats']  # Handled through individual format checkboxes
    
    for setting in cwa_default_settings:
        if setting in integer_settings or setting in float_settings or setting in json_settings or setting in skip_settings:
            continue  # Handle separately
        elif isinstance(cwa_default_settings[setting], int):
            boolean_settings.append(setting)
        elif isinstance(cwa_default_settings[setting], str) and cwa_default_settings[setting] != "":
            string_settings.append(setting)
        else:
            list_settings.append(setting)

    # Ensure cron expression is treated as a string even if default is empty
    if 'duplicate_scan_cron' not in string_settings:
        string_settings.append('duplicate_scan_cron')

    # Ensure archived cleanup schedule fields are treated as strings
    if 'archived_cleanup_schedule' not in string_settings:
        string_settings.append('archived_cleanup_schedule')
    if 'archived_cleanup_schedule_day' not in string_settings:
        string_settings.append('archived_cleanup_schedule_day')

    for format in ignorable_formats:
        string_settings.append(f"ignore_ingest_{format}")
        string_settings.append(f"ignore_convert_{format}")
        string_settings.append(f"convert_retained_{format}")

    if request.method == 'POST':
        saved_message = None
        # Anything other than the reset button (including a missing value) is a normal save
        if request.form.get('submit_button') != "Apply Default Settings":
            result = {"auto_convert_ignored_formats":[], "auto_ingest_ignored_formats":[], "auto_convert_retained_formats":[]}
            # set boolean_settings
            for setting in boolean_settings:
                value = request.form.get(setting)
                if value is None:
                    value = 0
                else:
                    value = 1
                result |= {setting:value}
            # set string settings
            for setting in string_settings:
                value = request.form.get(setting)
                if setting[:14] == "ignore_convert":
                    if value is not None:
                        result["auto_convert_ignored_formats"].append(value)
                    continue
                elif setting[:13] == "ignore_ingest":
                    if value is not None:
                        result["auto_ingest_ignored_formats"].append(value)
                    continue
                elif setting.startswith("convert_retained"):
                    if value is not None:
                        result["auto_convert_retained_formats"].append(value)
                    continue
                elif setting == "auto_convert_target_format":
                    if value is None:
                        value = cwa_db.cwa_settings['auto_convert_target_format']

                result |= {setting:value}

            # Monthly schedules submit a day-of-month number under the weekday field name
            for schedule_setting in ('hardcover_auto_fetch_schedule', 'archived_cleanup_schedule'):
                if result.get(schedule_setting) == 'monthly':
                    day_setting = f"{schedule_setting}_day"
                    result[day_setting] = _monthly_schedule_day(request.form.getlist(day_setting),
                                                                cwa_settings.get(day_setting))
            
            # Prevent ignoring of target format
            if result['auto_convert_target_format'] in result['auto_convert_ignored_formats']:
                result['auto_convert_ignored_formats'].remove(result['auto_convert_target_format'])
            if result['auto_convert_target_format'] in result['auto_ingest_ignored_formats']:
                result['auto_ingest_ignored_formats'].remove(result['auto_convert_target_format'])

            # Prevent retaining of ignored ingest formats (create a copy to avoid modification during iteration)
            for ignored_format in result['auto_ingest_ignored_formats'][:]:
                if ignored_format in result['auto_convert_retained_formats']:
                    result['auto_convert_retained_formats'].remove(ignored_format)

            # Force target format to be retained (ensure it's not already there to avoid duplicates)
            if result['auto_convert_target_format'] not in result['auto_convert_retained_formats']:
                result['auto_convert_retained_formats'].append(result['auto_convert_target_format'])

            # Handle integer settings
            for setting in integer_settings:
                value = request.form.get(setting)
                if value is not None:
                    try:
                        int_value = int(value)
                        # Validate range
                        if setting == 'ingest_timeout_minutes':
                            int_value = max(5, min(120, int_value))  # Clamp between 5 and 120 minutes
                        elif setting == 'ingest_stale_temp_minutes':
                            int_value = max(0, min(10080, int_value))  # Clamp between 0 and 10080 minutes (7 days)
                        elif setting == 'ingest_stale_temp_interval':
                            int_value = max(0, min(86400, int_value))  # Clamp between 0 and 86400 seconds (24 hours)
                        elif setting == 'auto_send_delay_minutes':
                            int_value = max(1, min(60, int_value))  # Clamp between 1 and 60 minutes
                        elif setting == 'hardcover_auto_fetch_batch_size':
                            int_value = max(10, min(200, int_value))  # Clamp between 10 and 200
                        elif setting == 'hardcover_auto_fetch_schedule_hour':
                            int_value = max(0, min(23, int_value))  # Clamp between 0 and 23 hours
                        elif setting == 'archived_cleanup_schedule_hour':
                            int_value = max(0, min(23, int_value))  # Clamp between 0 and 23 hours
                        elif setting == 'db_backup_keep_count':
                            int_value = max(1, min(365, int_value))  # Clamp between 1 and 365 snapshots
                        elif setting == 'duplicate_scan_hour':
                            int_value = max(0, min(23, int_value))
                        elif setting == 'duplicate_scan_chunk_size':
                            int_value = max(500, min(50000, int_value))
                        elif setting == 'duplicate_scan_debounce_seconds':
                            int_value = max(5, min(600, int_value))
                        elif setting == 'cover_download_max_mb':
                            int_value = max(1, min(200, int_value))
                        result[setting] = int_value
                    except (ValueError, TypeError):
                        # Use current value if conversion fails
                        if setting == 'ingest_timeout_minutes':
                            result[setting] = cwa_db.cwa_settings.get(setting, 15)  # Default to 15 minutes
                        elif setting == 'ingest_stale_temp_minutes':
                            result[setting] = cwa_db.cwa_settings.get(setting, 120)  # Default to 120 minutes
                        elif setting == 'ingest_stale_temp_interval':
                            result[setting] = cwa_db.cwa_settings.get(setting, 600)  # Default to 600 seconds
                        elif setting == 'auto_send_delay_minutes':
                            result[setting] = cwa_db.cwa_settings.get(setting, 5)  # Default to 5 minutes
                        elif setting == 'hardcover_auto_fetch_batch_size':
                            result[setting] = cwa_db.cwa_settings.get(setting, 50)  # Default to 50
                        elif setting == 'hardcover_auto_fetch_schedule_hour':
                            result[setting] = cwa_db.cwa_settings.get(setting, 2)  # Default to 2 AM
                        elif setting == 'archived_cleanup_schedule_hour':
                            result[setting] = cwa_db.cwa_settings.get(setting, 3)  # Default to 3 AM
                        elif setting == 'duplicate_scan_debounce_seconds':
                            result[setting] = cwa_db.cwa_settings.get(setting, 60)
                        elif setting == 'cover_download_max_mb':
                            result[setting] = cwa_db.cwa_settings.get(setting, 15)  # Default to 15 MB
                else:
                    if setting == 'ingest_timeout_minutes':
                        result[setting] = cwa_db.cwa_settings.get(setting, 15)  # Default to 15 minutes
                    elif setting == 'ingest_stale_temp_minutes':
                        result[setting] = cwa_db.cwa_settings.get(setting, 120)  # Default to 120 minutes
                    elif setting == 'ingest_stale_temp_interval':
                        result[setting] = cwa_db.cwa_settings.get(setting, 600)  # Default to 600 seconds
                    elif setting == 'auto_send_delay_minutes':
                        result[setting] = cwa_db.cwa_settings.get(setting, 5)  # Default to 5 minutes
                    elif setting == 'hardcover_auto_fetch_batch_size':
                        result[setting] = cwa_db.cwa_settings.get(setting, 50)  # Default to 50
                    elif setting == 'hardcover_auto_fetch_schedule_hour':
                        result[setting] = cwa_db.cwa_settings.get(setting, 2)  # Default to 2 AM
                    elif setting == 'archived_cleanup_schedule_hour':
                        result[setting] = cwa_db.cwa_settings.get(setting, 3)  # Default to 3 AM
                    elif setting == 'duplicate_scan_debounce_seconds':
                        result[setting] = cwa_db.cwa_settings.get(setting, 60)
                    elif setting == 'cover_download_max_mb':
                        result[setting] = cwa_db.cwa_settings.get(setting, 15)  # Default to 15 MB

            # Handle float settings
            for setting in float_settings:
                value = request.form.get(setting)
                if value is not None:
                    try:
                        float_value = float(value)
                        # Validate range
                        if setting == 'hardcover_auto_fetch_min_confidence':
                            float_value = max(0.5, min(1.0, float_value))  # Clamp between 0.5 and 1.0
                        elif setting == 'hardcover_auto_fetch_rate_limit':
                            float_value = max(0.0, min(60.0, float_value))  # Clamp between 0 and 60 seconds
                        result[setting] = float_value
                    except (ValueError, TypeError):
                        # Use current value if conversion fails
                        if setting == 'hardcover_auto_fetch_min_confidence':
                            result[setting] = cwa_db.cwa_settings.get(setting, 0.85)  # Default to 0.85
                        elif setting == 'hardcover_auto_fetch_rate_limit':
                            result[setting] = cwa_db.cwa_settings.get(setting, 5.0)  # Default to 5.0 seconds
                else:
                    if setting == 'hardcover_auto_fetch_min_confidence':
                        result[setting] = cwa_db.cwa_settings.get(setting, 0.85)  # Default to 0.85
                    elif setting == 'hardcover_auto_fetch_rate_limit':
                        result[setting] = cwa_db.cwa_settings.get(setting, 5.0)  # Default to 5.0 seconds


            # Handle JSON settings
            for setting in json_settings:
                value = request.form.get(setting)
                if value is not None:
                    try:
                        # Try to parse as JSON
                        import json
                        json_value = json.loads(value)
                        if setting == 'metadata_provider_hierarchy':
                            # Validate that it's a list of strings (provider IDs)
                            if isinstance(json_value, list) and all(isinstance(provider, str) for provider in json_value):
                                result[setting] = json.dumps(json_value)  # Store as JSON string
                            else:
                                # Use current value if validation fails
                                result[setting] = cwa_db.cwa_settings.get(setting, '["ibdb","google","dnb"]')
                        elif setting == 'metadata_providers_enabled':
                            # Validate dict mapping provider_id -> bool
                            if isinstance(json_value, dict):
                                # Just validate the basic structure - provider validation happens at runtime
                                cleaned_map = {}
                                for k, v in json_value.items():
                                    if isinstance(k, str) and isinstance(v, bool):
                                        cleaned_map[k] = v
                                result[setting] = json.dumps(cleaned_map)
                            else:
                                result[setting] = cwa_db.cwa_settings.get(setting, '{}')
                        else:
                            result[setting] = json.dumps(json_value)
                    except (json.JSONDecodeError, ValueError, TypeError):
                        # Use current value if JSON parsing fails
                        if setting == 'metadata_provider_hierarchy':
                            result[setting] = cwa_db.cwa_settings.get(setting, '["ibdb","google","dnb"]')
                        elif setting == 'metadata_providers_enabled':
                            result[setting] = cwa_db.cwa_settings.get(setting, '{}')
                        else:
                            result[setting] = cwa_db.cwa_settings.get(setting, '[]')
                else:
                    # Use current value if not provided
                    if setting == 'metadata_provider_hierarchy':
                        result[setting] = cwa_db.cwa_settings.get(setting, '["ibdb","google","dnb"]')
                    elif setting == 'metadata_providers_enabled':
                        result[setting] = cwa_db.cwa_settings.get(setting, '{}')
                    else:
                        result[setting] = cwa_db.cwa_settings.get(setting, '[]')

            # Validate cron expression if provided
            cron_invalid = False
            cron_expr = result.get('duplicate_scan_cron', '')
            if cron_expr:
                try:
                    from apscheduler.triggers.cron import CronTrigger
                    CronTrigger.from_crontab(cron_expr)
                except Exception:
                    # Revert to previous value and notify user
                    result['duplicate_scan_cron'] = cwa_db.cwa_settings.get('duplicate_scan_cron', '')
                    cron_invalid = True
                    flash(_("Invalid cron expression for duplicate scans. Changes were not saved."), category="error")

            # DEBUGGING
            # with open("/config/post_request" ,"w") as f:
            #     for key in result.keys():
            #         if key == "auto_convert_ignored_formats" or key == "auto_ingest_ignored_formats":
            #             f.write(f"{key} - {', '.join(result[key])}\n")
            #         else:
            #             f.write(f"{key} - {result[key]}\n")

            # Save Kobo Sync Magic Shelves setting (stored in app.db, not cwa.db)
            config.config_kobo_sync_magic_shelves = 'config_kobo_sync_magic_shelves' in request.form
            config.save()

            duplicate_criteria_changed = False
            try:
                from ..duplicate_index import get_criteria_fingerprint
                submitted_settings = dict(cwa_settings)
                submitted_settings.update(result)
                duplicate_criteria_changed = (
                    get_criteria_fingerprint(cwa_settings) != get_criteria_fingerprint(submitted_settings)
                )
            except Exception as e:
                log.warning("[cwa-duplicates] Could not compare duplicate criteria settings: %s", str(e))

            cwa_db.update_cwa_settings(result)
            cwa_settings = cwa_db.get_cwa_settings()

            if duplicate_criteria_changed:
                try:
                    from ..duplicate_index import mark_duplicate_index_pending
                    mark_duplicate_index_pending("duplicate criteria settings changed")
                except Exception as e:
                    log.warning("[cwa-duplicates] Could not mark duplicate index pending: %s", str(e))

            # If KOReader sync was just enabled, ensure required tables exist
            if not previous_koreader_enabled and bool(cwa_settings.get('koreader_sync_enabled', 0)):
                log.warning(
                    "KOReader sync enabled: checksum backfill runs at startup and may temporarily lock metadata.db. "
                    "Disable and restart the container to stop a running backfill."
                )
                try:
                    from ..progress_syncing.models import ensure_calibre_db_tables, ensure_app_db_tables
                    from ..progress_syncing.settings import is_koreader_sync_enabled
                    if is_koreader_sync_enabled():
                        try:
                            with calibre_db.engine.connect() as conn:
                                ensure_calibre_db_tables(conn)
                        except Exception as e:
                            log.error(f"Failed to initialize KOReader checksum tables: {e}")

                        try:
                            if ub.session and ub.session.bind is not None:
                                ensure_app_db_tables(ub.session.bind.raw_connection())
                        except Exception as e:
                            log.error(f"Failed to initialize KOReader progress tables: {e}")
                except Exception as e:
                    log.error(f"Failed to enable KOReader sync tables: {e}")
            elif previous_koreader_enabled and not bool(cwa_settings.get('koreader_sync_enabled', 0)):
                log.warning(
                    "KOReader sync disabled: checksum backfill will stop after container restart."
                )

            if not cron_invalid:
                saved_message = _("Settings saved")

        else:
            cwa_db = CWA_DB()
            duplicate_criteria_changed = False
            try:
                from ..duplicate_index import get_criteria_fingerprint
                previous_fingerprint = get_criteria_fingerprint(cwa_settings)
                default_settings = dict(cwa_db.cwa_default_settings)
                duplicate_criteria_changed = previous_fingerprint != get_criteria_fingerprint(default_settings)
            except Exception as e:
                log.warning("[cwa-duplicates] Could not compare duplicate criteria defaults: %s", str(e))
            cwa_db.set_default_settings(force=True)
            cwa_settings = cwa_db.get_cwa_settings()
            if duplicate_criteria_changed:
                try:
                    from ..duplicate_index import mark_duplicate_index_pending
                    mark_duplicate_index_pending("duplicate criteria settings changed")
                except Exception as e:
                    log.warning("[cwa-duplicates] Could not mark duplicate index pending: %s", str(e))
            saved_message = _("Default settings restored")

        if saved_message:
            flash(saved_message, category="success")
            # POST/Redirect/GET. The page JS posts to "/cwa-settings#<tab>"; a Location without a
            # fragment inherits the request's fragment (RFC 7231 7.1.2), so the open tab is kept.
            return redirect(url_for('cwa_settings.set_cwa_settings'))

    elif request.method == 'GET':
        cwa_db = CWA_DB()
        cwa_settings = cwa_db.get_cwa_settings()

    # Check if Hardcover token is available
    from os import getenv
    hardcover_token_available = bool(
        getattr(config, "config_hardcover_token", None) or 
        getenv("HARDCOVER_TOKEN")
    )


    next_scan_run = get_next_duplicate_scan_run(cwa_settings)

    return render_title_template("cwa_settings.html", title=_("Lily User Settings"), page="cwa-settings",
                                    cwa_settings=cwa_settings, ignorable_formats=ignorable_formats, target_formats=target_formats,
                                    automerge_options=automerge_options, autoingest_options=autoingest_options,
                                    hardcover_token_available=hardcover_token_available,
                                    next_duplicate_scan_run=next_scan_run, config=config)


def get_next_duplicate_scan_run(settings):
    """Compute next scheduled duplicate scan run time based on settings."""
    try:
        enabled = bool(settings.get('duplicate_scan_enabled', 0))
        cron_expr = (settings.get('duplicate_scan_cron') or '').strip()

        if not enabled:
            return None

        if not cron_expr:
            return None

        from apscheduler.triggers.cron import CronTrigger
        now = datetime.now().astimezone()
        trigger = CronTrigger.from_crontab(cron_expr, timezone=now.tzinfo)
        next_run = trigger.get_next_fire_time(None, now)
        return next_run.isoformat() if next_run else None
    except Exception:
        return None
