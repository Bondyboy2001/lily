# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Duplicate Books page and its endpoints: status, dismiss, scan trigger, preview and run resolution."""

from flask import Blueprint, jsonify, abort
from flask_babel import gettext as _
from datetime import datetime
from functools import wraps
import os
from shutil import copyfile

from . import db, calibre_db, logger, ub, csrf, config, helper
from .services.worker import WorkerThread, STAT_FINISH_SUCCESS, STAT_FAIL, STAT_ENDED, STAT_CANCELLED
from .admin import admin_required
from .usermanagement import login_required_if_no_ano
from .internal_api import internal_only
from .render_template import render_title_template
from .cw_login import current_user
from .duplicate_detection import (  # noqa: F401  (re-exported: other modules and tests import these from here)
    filter_dismissed_groups, find_duplicate_books, find_duplicate_books_python, find_duplicate_books_sql,
    find_duplicate_candidate_ids_sql, get_common_filters, get_unresolved_duplicate_count)
from .duplicate_rules import (  # noqa: F401  (re-exported: other modules and tests import these from here)
    _AWARE_MAX, _AWARE_MIN, _normalize_timestamp, _timestamp_or_default,
    generate_group_hash, normalize_title_for_duplicates, select_book_to_keep, validate_resolution_strategy)

import sys
sys.path.insert(1, '/app/calibre-web-automated/scripts/')
from cwa_db import CWA_DB

duplicates = Blueprint('duplicates', __name__)
log = logger.create()


def _duplicate_scan_transiently_pending():
    try:
        from cps.duplicate_index import ingest_batch_follow_up_pending
        if ingest_batch_follow_up_pending():
            return True
    except Exception as ex:
        log.debug("[cwa-duplicates] Could not check ingest follow-up marker state: %s", str(ex))

    try:
        from cps.cwa_functions import duplicate_scan_debounce_pending
        if duplicate_scan_debounce_pending():
            return True
    except Exception as ex:
        log.debug("[cwa-duplicates] Could not check duplicate scan debounce state: %s", str(ex))

    try:
        terminal_stats = {STAT_FINISH_SUCCESS, STAT_FAIL, STAT_ENDED, STAT_CANCELLED}
        for __, __, __, task, __ in WorkerThread.get_instance().tasks:
            if task.stat in terminal_stats:
                continue
            if task.__class__.__name__ == "TaskDuplicateScan" or str(getattr(task, "name", "")).lower() == "duplicate scan":
                return True
    except Exception as ex:
        log.debug("[cwa-duplicates] Could not check duplicate scan worker state: %s", str(ex))

    return False


def admin_or_edit_required(f):
    """Decorator that allows access to admins or users with edit role"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        if not (current_user.role_admin() or current_user.role_edit()):
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


@duplicates.route("/duplicates")
@login_required_if_no_ano
@admin_or_edit_required
def show_duplicates():
    """Display cached duplicate groups and prompt for the initial index scan."""
    print("[cwa-duplicates] Loading duplicates page...", flush=True)
    log.info("[cwa-duplicates] Loading duplicates page for user: %s", current_user.name)

    try:
        cwa_db = CWA_DB()
        settings = cwa_db.cwa_settings
        duplicate_groups = []
        duplicate_index_needs_full_scan = True

        try:
            from cps.duplicate_index import (
                duplicate_index_needs_manual_full_scan,
                get_duplicate_groups_from_index,
                library_has_books,
            )

            duplicate_index_needs_full_scan = (
                library_has_books()
                and duplicate_index_needs_manual_full_scan(settings)
                and not _duplicate_scan_transiently_pending()
            )
            if duplicate_index_needs_full_scan:
                log.info("[cwa-duplicates] Duplicate index baseline missing; prompting for manual full scan")
            else:
                duplicate_groups = get_duplicate_groups_from_index(
                    settings,
                    include_dismissed=False,
                    user_id=current_user.id if current_user else None,
                )
        except Exception as index_ex:
            log.warning("[cwa-duplicates] Could not load duplicate index state: %s", str(index_ex))

        # Compute next scheduled scan run
        next_scan_run = get_next_duplicate_scan_run(settings)

        print(f"[cwa-duplicates] Found {len(duplicate_groups)} duplicate groups total", flush=True)
        log.info("[cwa-duplicates] Found %s duplicate groups total", len(duplicate_groups))

        return render_title_template('duplicates.html',
                                     duplicate_groups=duplicate_groups,
                                     duplicate_index_needs_full_scan=duplicate_index_needs_full_scan,
                                     next_scan_run=next_scan_run,
                                     title=_("Duplicate Books"),
                                     page="duplicates")

    except Exception as e:
        print(f"[cwa-duplicates] Critical error loading duplicates page: {str(e)}", flush=True)
        log.error("[cwa-duplicates] Critical error loading duplicates page: %s", str(e))
        # Return empty page on error
        return render_title_template('duplicates.html',
                                     duplicate_groups=[],
                                     duplicate_index_needs_full_scan=False,
                                     next_scan_run=None,
                                     title=_("Duplicate Books"),
                                     page="duplicates")


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


@duplicates.route("/duplicates/status")
@login_required_if_no_ano
@admin_or_edit_required
def get_duplicate_status():
    """API endpoint to get unresolved duplicate count and sample groups

    Returns JSON with:
        - enabled: Whether notifications are enabled
        - count: Number of unresolved duplicate groups
        - preview: List of up to 3 sample duplicate groups
    """
    cwa_db = None
    try:
        # Check if duplicate detection is enabled
        cwa_db = CWA_DB()
        detection_enabled = cwa_db.cwa_settings.get('duplicate_detection_enabled', 1)

        if not detection_enabled:
            return jsonify({
                'success': True,
                'enabled': False,
                'count': 0,
                'preview': []
            })

        # Check if notifications are enabled
        notifications_enabled = cwa_db.cwa_settings.get('duplicate_notifications_enabled', 1)

        try:
            from cps.duplicate_index import library_has_books
            duplicate_library_has_books = library_has_books()
        except Exception as index_ex:
            log.warning("[cwa-duplicates] Could not check duplicate library size in status endpoint: %s", str(index_ex))
            duplicate_library_has_books = True

        # Try to get cached results first
        cache_data = cwa_db.get_duplicate_cache()
        duplicate_index_needs_full_scan = (
            duplicate_library_has_books
            and (bool(cache_data.get('scan_pending')) if cache_data else True)
        )
        if cache_data and duplicate_library_has_books:
            try:
                from cps.duplicate_index import (
                    duplicate_index_needs_manual_full_scan,
                )

                duplicate_index_needs_full_scan = (
                    duplicate_library_has_books
                    and duplicate_index_needs_manual_full_scan(
                        cwa_db.cwa_settings, cwa_db=cwa_db, cache_data=cache_data
                    )
                    and not _duplicate_scan_transiently_pending()
                )
            except Exception as index_ex:
                log.warning("[cwa-duplicates] Could not check duplicate index baseline in status endpoint: %s", str(index_ex))
                duplicate_index_needs_full_scan = True

        if cache_data and cache_data.get('duplicate_groups') is not None:
            if duplicate_index_needs_full_scan:
                return jsonify({
                    'success': True,
                    'enabled': bool(notifications_enabled),
                    'count': 0,
                    'preview': [],
                    'cached': False,
                    'stale': True,
                    'needs_scan': True,
                    'needs_full_scan': True
                })

            # Cache is available; use it even if scan is pending
            duplicate_groups = cache_data['duplicate_groups']

            # Filter out dismissed groups for this user
            duplicate_groups = filter_dismissed_groups(
                duplicate_groups,
                current_user.id if current_user and current_user.id else None
            )

            count = len(duplicate_groups)

            # Get preview of first 3 groups
            preview = []
            for group in duplicate_groups[:3]:
                preview.append({
                    'title': group['title'],
                    'author': group['author'],
                    'count': group['count'],
                    'hash': group['group_hash']
                })

            return jsonify({
                'success': True,
                'enabled': bool(notifications_enabled),
                'count': count,
                'preview': preview,
                'cached': True,
                'stale': bool(cache_data.get('scan_pending')),
                'needs_scan': duplicate_index_needs_full_scan,
                'needs_full_scan': duplicate_index_needs_full_scan
            })

        # Cache is missing - DO NOT trigger scan here!
        # This endpoint is called on every page load via duplicate-notifier.js
        # Scans should ONLY be triggered by:
        # 1. Manual "Trigger Scan" button on /duplicates page (via /duplicates/trigger-scan)
        # 2. After ingest operations (via cache invalidation + manual trigger)
        # 3. Scheduled background scans (Phase 2 - not yet implemented)
        log.debug("[cwa-duplicates] Cache invalid/pending in status check, returning empty (no auto-scan)")

        return jsonify({
            'success': True,
            'enabled': bool(notifications_enabled),
            'count': 0,
            'preview': [],
            'cached': False,
            'needs_scan': duplicate_index_needs_full_scan,  # Frontend can optionally show "scan needed" message
            'needs_full_scan': duplicate_index_needs_full_scan
        })
    except Exception as e:
        log.error("[cwa-duplicates] Error getting duplicate status: %s", str(e))
        return jsonify({
            'success': False,
            'error': 'Internal error; see server log for details',
            'count': 0,
            'preview': []
        }), 500
    finally:
        if cwa_db is not None:
            close = getattr(cwa_db, "close", None)
            if callable(close):
                close()


@duplicates.route("/duplicates/dismiss-setup-notice", methods=['POST'])
@login_required_if_no_ano
@admin_or_edit_required
def dismiss_duplicate_scan_setup_notice():
    """Remember that the current duplicate-index setup notice was dismissed."""
    try:
        notice_file = f"/config/cwa_duplicate_index_setup_notice_{getattr(current_user, 'id', 'unknown')}"
        with open(notice_file, 'w') as f:
            f.write("dismissed\n")
        return jsonify({"success": True})
    except Exception as e:
        log.error("[cwa-duplicates] Failed to dismiss duplicate setup notice: %s", str(e))
        return jsonify({"success": False, "error": 'Internal error; see server log for details'}), 500


@duplicates.route("/duplicates/dismiss/<group_hash>", methods=['POST'])
@login_required_if_no_ano
@admin_or_edit_required
def dismiss_duplicate_group(group_hash):
    """API endpoint to dismiss a duplicate group

    Args:
        group_hash: MD5 hash of the duplicate group

    Returns:
        JSON response with success status and new count
    """
    try:
        # Check if already dismissed
        existing = ub.session.query(ub.DismissedDuplicateGroup)\
            .filter(ub.DismissedDuplicateGroup.user_id == current_user.id)\
            .filter(ub.DismissedDuplicateGroup.group_hash == group_hash)\
            .first()

        if existing:
            return jsonify({
                'success': True,
                'message': _('Duplicate group already dismissed'),
                'count': get_unresolved_duplicate_count()
            })

        # Create dismissal record
        dismissal = ub.DismissedDuplicateGroup(
            user_id=current_user.id,
            group_hash=group_hash
        )
        ub.session.add(dismissal)
        ub.session.commit()

        log.info("[cwa-duplicates] User %s dismissed duplicate group %s",
                current_user.name, group_hash)

        # Get new count
        new_count = get_unresolved_duplicate_count()

        return jsonify({
            'success': True,
            'message': _('Duplicate group dismissed'),
            'count': new_count
        })

    except Exception as e:
        ub.session.rollback()
        log.error("[cwa-duplicates] Error dismissing duplicate group: %s", str(e))
        return jsonify({
            'success': False,
            'error': 'Internal error; see server log for details'
        }), 500


@duplicates.route("/duplicates/undismiss/<group_hash>", methods=['POST'])
@login_required_if_no_ano
@admin_or_edit_required
def undismiss_duplicate_group(group_hash):
    """API endpoint to un-dismiss a duplicate group

    Args:
        group_hash: MD5 hash of the duplicate group

    Returns:
        JSON response with success status and new count
    """
    try:
        # Find and delete dismissal record
        deleted = ub.session.query(ub.DismissedDuplicateGroup)\
            .filter(ub.DismissedDuplicateGroup.user_id == current_user.id)\
            .filter(ub.DismissedDuplicateGroup.group_hash == group_hash)\
            .delete()

        ub.session.commit()

        if deleted:
            log.info("[cwa-duplicates] User %s un-dismissed duplicate group %s",
                    current_user.name, group_hash)
            message = _('Duplicate group restored')
        else:
            message = _('Duplicate group was not dismissed')

        # Get new count
        new_count = get_unresolved_duplicate_count()

        return jsonify({
            'success': True,
            'message': message,
            'count': new_count
        })

    except Exception as e:
        ub.session.rollback()
        log.error("[cwa-duplicates] Error un-dismissing duplicate group: %s", str(e))
        return jsonify({
            'success': False,
            'error': 'Internal error; see server log for details'
        }), 500


@duplicates.route("/duplicates/invalidate-cache", methods=['POST'])
@csrf.exempt
@internal_only
def invalidate_cache():
    """Internal endpoint to invalidate duplicate cache (called after ingest)"""
    try:
        cwa_db = CWA_DB()
        success = cwa_db.invalidate_duplicate_cache()

        if success:
            log.info("[cwa-duplicates] Cache invalidated - will refresh on next status check")
            return jsonify({'success': True, 'message': 'Cache invalidated'})
        else:
            return jsonify({'success': False, 'error': 'Failed to invalidate cache'}), 500

    except Exception as e:
        log.error("[cwa-duplicates] Error invalidating cache: %s", str(e))
        return jsonify({'success': False, 'error': 'Internal error; see server log for details'}), 500


@duplicates.route("/duplicates/trigger-scan", methods=['POST'])
@login_required_if_no_ano
@admin_or_edit_required
def trigger_scan():
    """Manually trigger a duplicate scan"""
    try:
        try:
            from cps.duplicate_index import ingest_batch_follow_up_pending
            if ingest_batch_follow_up_pending():
                log.info("[cwa-duplicates] Manual full scan blocked while ingest is active")
                return jsonify({
                    'success': False,
                    'blocked': True,
                    'reason': 'ingest_in_progress',
                    'message': _('Import is in progress. Run a full duplicate scan after ingest finishes.'),
                }), 409
        except Exception as ex:
            log.warning("[cwa-duplicates] Could not check ingest state before manual scan: %s", str(ex))

        # Invalidate cache and run fresh scan
        cwa_db = CWA_DB()
        cwa_db.invalidate_duplicate_cache()

        # Queue background task
        try:
            from cps.tasks.duplicate_scan import TaskDuplicateScan
            task = TaskDuplicateScan(full_scan=True, trigger_type='manual', user_id=current_user.id)
            WorkerThread.add(current_user.name, task, hidden=False)

            log.info("[cwa-duplicates] Manual scan queued by user %s (task_id=%s)",
                    current_user.name, task.id)
            print(f"[cwa-duplicates] Manual scan queued for user {current_user.name}, task_id={task.id}", flush=True)

            return jsonify({
                'success': True,
                'message': _('Duplicate scan queued'),
                'task_id': str(task.id),
                'queued': True
            })
        except Exception as e:
            log.error("[cwa-duplicates] Failed to queue scan task, falling back to sync scan: %s", str(e))
            print(f"[cwa-duplicates] Failed to queue task, using fallback: {str(e)}", flush=True)

            # Fallback to synchronous scan to avoid hard failures
            from cps.duplicate_index import get_duplicate_groups_from_index, rebuild_duplicate_index

            settings = cwa_db.cwa_settings
            rebuild_metadata = rebuild_duplicate_index(settings)
            duplicate_groups = get_duplicate_groups_from_index(
                settings,
                include_dismissed=False,
                user_id=current_user.id if current_user else None,
            )
            all_groups = get_duplicate_groups_from_index(settings, include_dismissed=True)
            max_book_id = rebuild_metadata.get('max_book_id', 0)
            cwa_db.update_duplicate_cache(all_groups, len(all_groups), max_book_id)

            # Check if auto-resolution is enabled for fallback sync scan
            if len(duplicate_groups) > 0:
                try:
                    auto_resolve_enabled = cwa_db.cwa_settings.get('duplicate_auto_resolve_enabled', 0)
                    auto_resolve_strategy = cwa_db.cwa_settings.get('duplicate_auto_resolve_strategy', 'newest')

                    if auto_resolve_enabled:
                        log.info("[cwa-duplicates] Auto-resolution enabled in fallback, triggering with strategy: %s",
                                auto_resolve_strategy)
                        print(f"[cwa-duplicates] Fallback scan complete, triggering auto-resolution (strategy: {auto_resolve_strategy})",
                              flush=True)

                        # Pass the pre-scanned duplicate groups to avoid re-scanning
                        result = auto_resolve_duplicates(
                            strategy=auto_resolve_strategy,
                            dry_run=False,
                            user_id=current_user.id if current_user else None,
                            trigger_type='manual',
                            duplicate_groups=duplicate_groups
                        )

                        if result['success'] and result['resolved_count'] > 0:
                            log.info("[cwa-duplicates] Fallback auto-resolution completed: resolved=%s, kept=%s, deleted=%s",
                                    result['resolved_count'], result['kept_count'], result['deleted_count'])
                            print(f"[cwa-duplicates] Fallback auto-resolution completed: {result['resolved_count']} groups resolved",
                                  flush=True)

                            # Re-scan to get updated counts after resolution
                            rebuild_metadata = rebuild_duplicate_index(settings)
                            duplicate_groups = get_duplicate_groups_from_index(
                                settings,
                                include_dismissed=False,
                                user_id=current_user.id if current_user else None,
                            )
                            all_groups = get_duplicate_groups_from_index(settings, include_dismissed=True)
                            cwa_db.update_duplicate_cache(
                                all_groups,
                                len(all_groups),
                                rebuild_metadata.get('max_book_id', max_book_id),
                            )
                            log.debug("[cwa-duplicates] Cache refreshed after fallback auto-resolution")
                except Exception as ex:
                    log.error("[cwa-duplicates] Error during fallback auto-resolution: %s", str(ex))
                    print(f"[cwa-duplicates] Fallback auto-resolution error: {str(ex)}", flush=True)

            return jsonify({
                'success': True,
                'message': _('Duplicate scan completed (fallback)'),
                'count': len(duplicate_groups),
                'fallback': True,
                'queued': False,
                'fallback_reason': str(e)
            })

    except Exception as e:
        log.error("[cwa-duplicates] Error triggering scan: %s", str(e))
        return jsonify({
            'success': False,
            'error': 'Internal error; see server log for details'
        }), 500


@duplicates.route("/duplicates/preview-resolution", methods=["POST"])
@login_required_if_no_ano
@admin_required
def preview_resolution():
    """Preview auto-resolution without executing"""
    print("[cwa-duplicates] Preview resolution endpoint called", flush=True)
    log.info("[cwa-duplicates] Preview resolution endpoint called")
    try:
        from flask import request
        strategy = request.json.get('strategy', 'newest')
        print(f"[cwa-duplicates] Preview strategy: {strategy}", flush=True)
        log.info("[cwa-duplicates] Preview strategy: %s", strategy)

        if not validate_resolution_strategy(strategy):
            error_msg = f'Invalid resolution strategy: {strategy}'
            print(f"[cwa-duplicates] {error_msg}", flush=True)
            log.error("[cwa-duplicates] %s", error_msg)
            return jsonify({
                'success': False,
                'error': _(error_msg)
            }), 400

        print("[cwa-duplicates] Calling auto_resolve_duplicates in dry_run mode...", flush=True)
        duplicate_groups = _get_duplicate_groups_for_resolution(current_user.id)
        result = auto_resolve_duplicates(
            strategy=strategy,
            dry_run=True,
            user_id=current_user.id,
            trigger_type='manual',
            duplicate_groups=duplicate_groups
        )
        if 'preview' not in result:
            result['preview'] = []

        print(f"[cwa-duplicates] Preview result: {result.get('success', False)}, resolved_count={result.get('resolved_count', 0)}", flush=True)
        return jsonify(result)

    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"[cwa-duplicates] Error previewing resolution: {e}\n{error_trace}", flush=True)
        log.error("[cwa-duplicates] Error previewing resolution: %s\n%s", str(e), error_trace)
        return jsonify({
            'success': False,
            'error': 'Internal error; see server log for details'
        }), 500


@duplicates.route("/duplicates/execute-resolution", methods=["POST"])
@login_required_if_no_ano
@admin_required
def execute_resolution():
    """Execute auto-resolution"""
    try:
        from flask import request
        strategy = request.json.get('strategy', 'newest')

        if not validate_resolution_strategy(strategy):
            return jsonify({
                'success': False,
                'error': _('Invalid resolution strategy')
            }), 400

        try:
            from cps.duplicate_index import ingest_batch_follow_up_pending
            if ingest_batch_follow_up_pending():
                log.info("[cwa-duplicates] Auto-resolution blocked while ingest is active")
                return jsonify({
                    'success': False,
                    'blocked': True,
                    'reason': 'ingest_in_progress',
                    'message': _('Import is in progress. Execute duplicate resolution after ingest finishes.'),
                }), 409
        except Exception as ex:
            log.warning("[cwa-duplicates] Could not check ingest state before auto-resolution: %s", str(ex))

        duplicate_groups = _get_duplicate_groups_for_resolution(current_user.id)
        result = auto_resolve_duplicates(
            strategy=strategy,
            dry_run=False,
            user_id=current_user.id,
            trigger_type='manual',
            duplicate_groups=duplicate_groups
        )

        return jsonify(result)

    except Exception as e:
        log.error("[cwa-duplicates] Error executing resolution: %s", str(e))
        return jsonify({
            'success': False,
            'error': 'Internal error; see server log for details'
        }), 500


def _get_duplicate_groups_for_resolution(user_id=None):
    """Use the same indexed duplicate source as the Duplicates page."""
    try:
        from cps.duplicate_index import get_duplicate_groups_from_index

        cwa_db = CWA_DB()
        return get_duplicate_groups_from_index(
            cwa_db.cwa_settings,
            include_dismissed=False,
            user_id=user_id,
        )
    except Exception as ex:
        log.warning("[cwa-duplicates] Failed to load indexed groups for resolution, falling back to legacy scan: %s", str(ex))
        return find_duplicate_books(include_dismissed=False, user_id=user_id)


def _refresh_duplicate_cache_after_resolution(cwa_db):
    """Refresh cached duplicate groups after resolution removes books/index rows."""
    try:
        from cps.duplicate_index import get_duplicate_groups_from_index, _current_max_book_id

        duplicate_groups = get_duplicate_groups_from_index(cwa_db.cwa_settings, include_dismissed=True)
        if not cwa_db.update_duplicate_cache(duplicate_groups, len(duplicate_groups), _current_max_book_id()):
            raise RuntimeError("update_duplicate_cache returned False")
        log.debug("[cwa-duplicates] Duplicate cache refreshed after auto-resolution")
    except Exception as ex:
        log.warning("[cwa-duplicates] Failed to refresh cache after resolution: %s", str(ex))
        try:
            cwa_db.invalidate_duplicate_cache()
        except Exception as invalidate_ex:
            log.warning("[cwa-duplicates] Failed to invalidate cache after resolution: %s", str(invalidate_ex))


def auto_resolve_duplicates(strategy='newest', dry_run=False, user_id=None, trigger_type='manual', duplicate_groups=None):
    """
    Automatically resolve duplicate books by keeping one and deleting others.

    Args:
        strategy: Resolution strategy ('newest', 'highest_quality_format', 'most_metadata', 'largest_file_size')
        dry_run: If True, return preview without actually deleting
        user_id: User ID triggering the resolution (for audit), None for system-initiated
        trigger_type: 'manual', 'scheduled', or 'automatic'
        duplicate_groups: Pre-scanned duplicate groups (avoids re-scanning). If None, will scan.

    Returns:
        dict with keys:
            'success': bool
            'resolved_count': int (number of groups resolved)
            'deleted_count': int (total books deleted)
            'kept_count': int (total books kept)
            'errors': list of error messages
            'preview': list of dicts (if dry_run=True) with 'group', 'kept_book', 'deleted_books'
    """
    try:
        import time
        start_time = time.time()

        log.info("[cwa-duplicates] Starting auto-resolution (strategy=%s, dry_run=%s, trigger=%s, pre_scanned=%s)",
                 strategy, dry_run, trigger_type, duplicate_groups is not None)
        print(f"[cwa-duplicates] Auto-resolve starting: strategy={strategy}, dry_run={dry_run}, trigger={trigger_type}, "
              f"pre_scanned={duplicate_groups is not None}", flush=True)

        # Initialize database sessions for thread safety
        from cps.ub import init_db_thread
        try:
            init_db_thread()
        except Exception as e:
            log.warning("Could not initialise DB thread session for duplicate scan: %s", e)

        calibre_db.ensure_session()

        # Disk space check (strategy-dependent thresholds)
        try:
            import shutil as shutil_disk
            stat = shutil_disk.disk_usage('/config')
            available_gb = stat.free / (1024**3)

            # Merge strategy needs more space (copies formats before deletion)
            min_space_gb = 2.0 if strategy == 'merge' else 0.5

            if available_gb < min_space_gb:
                log.warning("[cwa-duplicates] Low disk space (%.2f GB available, %.2f GB recommended for %s strategy)",
                           available_gb, min_space_gb, strategy)
                print(f"[cwa-duplicates] WARNING: Low disk space ({available_gb:.2f} GB available, "
                      f"{min_space_gb:.2f} GB recommended for {strategy} strategy)", flush=True)

                if trigger_type == 'automatic' and available_gb < min_space_gb * 0.5:
                    # For automatic triggers, abort if critically low
                    return {
                        'success': False,
                        'resolved_count': 0,
                        'deleted_count': 0,
                        'kept_count': 0,
                        'errors': [f'Insufficient disk space: {available_gb:.2f} GB available, {min_space_gb} GB required']
                    }
        except Exception as e:
            log.debug("[cwa-duplicates] Disk space check failed: %s", str(e))

        import shutil

        # Validate strategy
        if not validate_resolution_strategy(strategy):
            return {'success': False, 'errors': [f'Invalid strategy: {strategy}']}

        # Get duplicate groups (exclude dismissed)
        # If groups were passed in, use them (avoids expensive re-scan)
        if duplicate_groups is None:
            log.debug("[cwa-duplicates] No groups provided, scanning for duplicates...")
            print("[cwa-duplicates] auto_resolve received None groups - will scan", flush=True)
            duplicate_groups = find_duplicate_books(include_dismissed=False)
        else:
            log.debug("[cwa-duplicates] Using %d pre-scanned duplicate groups", len(duplicate_groups))
            print(f"[cwa-duplicates] auto_resolve using {len(duplicate_groups)} pre-scanned groups (type: {type(duplicate_groups).__name__})",
                  flush=True)

        if not duplicate_groups:
            return {
                'success': True,
                'resolved_count': 0,
                'deleted_count': 0,
                'kept_count': 0,
                'errors': [],
                'message': 'No unresolved duplicates found'
            }

        result = {
            'success': True,
            'resolved_count': 0,
            'deleted_count': 0,
            'kept_count': 0,
            'errors': [],
            'preview': [] if dry_run else None
        }

        cwa_db = CWA_DB()

        for group in duplicate_groups:
            try:
                # Select book to keep
                book_to_keep = select_book_to_keep(group['books'], strategy)

                if not book_to_keep:
                    result['errors'].append(f"Could not select book to keep for group: {group['title']}")
                    continue

                # Get books to delete
                books_to_delete = [b for b in group['books'] if b.id != book_to_keep.id]

                if not books_to_delete:
                    continue  # Only one book in group, nothing to resolve

                if dry_run:
                    kept_formats = []
                    if book_to_keep.data:
                        for data in book_to_keep.data:
                            if data.format and data.format not in kept_formats:
                                kept_formats.append(data.format)
                    if strategy == 'merge':
                        for book in books_to_delete:
                            if book.data:
                                for data in book.data:
                                    if data.format and data.format not in kept_formats:
                                        kept_formats.append(data.format)
                    # Preview mode: just collect info
                    result['preview'].append({
                        'group_hash': group['group_hash'],
                        'title': group['title'],
                        'author': group['author'],
                        'kept_book_id': book_to_keep.id,
                        'kept_book_timestamp': book_to_keep.timestamp.strftime('%Y-%m-%d %H:%M') if book_to_keep.timestamp else 'Unknown',
                        'kept_book_formats': kept_formats,
                        'deleted_book_ids': [b.id for b in books_to_delete],
                        'deleted_books_info': [{
                            'id': b.id,
                            'timestamp': b.timestamp.strftime('%Y-%m-%d %H:%M') if b.timestamp else 'Unknown',
                            'formats': [d.format for d in b.data] if b.data else []
                        } for b in books_to_delete]
                    })
                    result['kept_count'] += 1
                    result['deleted_count'] += len(books_to_delete)
                    result['resolved_count'] += 1
                    continue

                # Actual resolution mode
                # Re-fetch books in the active session to avoid detached object issues
                book_to_keep_id = book_to_keep.id
                books_to_delete_ids = [b.id for b in books_to_delete]
                book_to_keep_ref = calibre_db.get_book(book_to_keep_id)
                if not book_to_keep_ref:
                    result['errors'].append(f"Book to keep (ID {book_to_keep_id}) no longer exists")
                    continue
                books_to_delete = [calibre_db.get_book(book_id) for book_id in books_to_delete_ids]
                books_to_delete = [b for b in books_to_delete if b]
                if not books_to_delete:
                    continue
                book_to_keep = book_to_keep_ref

                deleted_ids = []
                backup_dir = f"/config/processed_books/duplicate_resolutions/{datetime.now().strftime('%Y%m%d_%H%M%S')}_group_{group['group_hash'][:8]}"
                os.makedirs(backup_dir, exist_ok=True)

                if strategy == 'merge':
                    try:
                        merge_duplicate_group(book_to_keep, books_to_delete)
                    except Exception as e:
                        log.error("[cwa-duplicates] Error merging books for group '%s': %s", group.get('title', 'unknown'), e)
                        result['errors'].append(f"Group '{group.get('title', 'unknown')}': merge failed: {str(e)}")
                        continue

                # Backup and delete each duplicate
                for book in books_to_delete:
                    try:
                        print(f"[cwa-duplicates-auto] Starting deletion of book {book.id}...", flush=True)

                        # Backup book files
                        book_path = os.path.join(config.config_calibre_dir, book.path)
                        if os.path.exists(book_path):
                            backup_path = os.path.join(backup_dir, f"book_{book.id}")
                            print(f"[cwa-duplicates-auto] Backing up book {book.id} to {backup_path}...", flush=True)
                            shutil.copytree(book_path, backup_path)
                            log.info("[cwa-duplicates] Backed up book %s to %s", book.id, backup_path)

                        print(f"[cwa-duplicates-auto] Deleting book {book.id} from library...", flush=True)
                        # Delete from Calibre library (bypass user permission check for automatic resolution)
                        from cps import helper
                        delete_result, delete_error = helper.delete_book(book, config.get_book_path(), book_format="")

                        if not delete_result:
                            raise Exception(f"Delete failed: {delete_error}")

                        print(f"[cwa-duplicates-auto] Cleaning up database for book {book.id}...", flush=True)
                        # Clean up database references
                        from cps.editbooks import delete_whole_book
                        delete_whole_book(book.id, book)

                        calibre_db.session.commit()
                        deleted_ids.append(book.id)
                        log.info("[cwa-duplicates] Deleted duplicate book %s: %s", book.id, book.title)

                        print(f"[cwa-duplicates-auto] Cancelling tasks for book {book.id}...", flush=True)
                        # Cancel any pending tasks for this book
                        try:
                            from cps.services.worker import WorkerThread
                            worker = WorkerThread.get_instance()
                            if worker:
                                cancelled_count = worker.cancel_tasks_for_book(book.id)
                                if cancelled_count > 0:
                                    log.info("[cwa-duplicates] Cancelled %d pending task(s) for deleted book %s",
                                            cancelled_count, book.id)
                                    print(f"[cwa-duplicates-auto] Cancelled {cancelled_count} pending task(s) for book {book.id}",
                                          flush=True)
                        except Exception as cancel_ex:
                            log.warning("[cwa-duplicates] Failed to cancel tasks for book %s: %s", book.id, cancel_ex)

                        print(f"[cwa-duplicates-auto] Book {book.id} deletion complete", flush=True)

                    except Exception as e:
                        log.error("[cwa-duplicates] Error deleting book %s: %s", book.id, e)
                        result['errors'].append(f"Failed to delete book {book.id}: {str(e)}")

                if deleted_ids:
                    try:
                        from cps.duplicate_index import delete_book_keys
                        delete_book_keys(deleted_ids)
                    except Exception as e:
                        log.warning("[cwa-duplicates] Failed to delete duplicate index keys for books %s: %s",
                                    deleted_ids, str(e))

                    # Log to audit table
                    cwa_db.log_duplicate_resolution(
                        group_hash=group['group_hash'],
                        group_title=group['title'],
                        group_author=group['author'],
                        kept_book_id=book_to_keep.id,
                        deleted_book_ids=deleted_ids,
                        strategy=strategy,
                        trigger_type=trigger_type,
                        user_id=user_id,
                        notes=f"Resolved {len(deleted_ids)} duplicate(s) using {strategy} strategy"
                    )

                    result['resolved_count'] += 1
                    result['kept_count'] += 1
                    result['deleted_count'] += len(deleted_ids)

                    log.info("[cwa-duplicates] Resolved duplicate group '%s' by %s: kept book %s, deleted %s duplicates",
                            group['title'], group['author'], book_to_keep.id, len(deleted_ids))

                    # Docker log for automatic triggers
                    if trigger_type == 'automatic':
                        print(f"[cwa-duplicates-auto] ✓ Resolved '{group['title']}' by {group['author']}: "
                              f"kept book {book_to_keep.id}, deleted {len(deleted_ids)} duplicate(s) [{strategy} strategy]",
                              flush=True)

            except Exception as e:
                log.error("[cwa-duplicates] Error resolving duplicate group '%s': %s", group.get('title', 'unknown'), e)
                result['errors'].append(f"Group '{group.get('title', 'unknown')}': {str(e)}")


        if result['errors']:
            result['success'] = False

        # Refresh cache only after real deletions. Dry-run previews report the
        # would-be deleted count but must not mutate scan/index state.
        if not dry_run and result['deleted_count'] > 0:
            _refresh_duplicate_cache_after_resolution(cwa_db)

        # Log timing information
        elapsed_time = time.time() - start_time
        log.info("[cwa-duplicates] Auto-resolution completed in %.2f seconds: resolved=%d, kept=%d, deleted=%d, errors=%d",
                 elapsed_time, result['resolved_count'], result['kept_count'], result['deleted_count'], len(result['errors']))
        print(f"[cwa-duplicates] Auto-resolution completed in {elapsed_time:.2f}s: "
              f"resolved={result['resolved_count']}, kept={result['kept_count']}, deleted={result['deleted_count']}, "
              f"errors={len(result['errors'])}", flush=True)

        return result

    finally:
        # Always cleanup sessions
        try:
            if calibre_db.session is not None:
                calibre_db.session.close()
        except Exception:
            pass


def merge_duplicate_group(book_to_keep, books_to_merge):
    """Merge formats from duplicate books into the target book."""
    if not book_to_keep or not books_to_merge:
        return

    to_book = calibre_db.get_book(book_to_keep.id)
    if not to_book:
        raise ValueError("Target book not found for merge")

    existing_formats = [file.format for file in to_book.data] if to_book.data else []
    author_name = "unknown"
    if to_book.authors:
        author_name = to_book.authors[0].name
    to_name = helper.get_valid_filename(to_book.title, chars=96) + ' - ' + helper.get_valid_filename(author_name, chars=96)

    for source in books_to_merge:
        from_book = calibre_db.get_book(source.id)
        if not from_book:
            continue
        for element in from_book.data:
            if element.format not in existing_formats:
                filepath_new = os.path.normpath(os.path.join(config.get_book_path(),
                                                             to_book.path,
                                                             to_name + "." + element.format.lower()))
                filepath_old = os.path.normpath(os.path.join(config.get_book_path(),
                                                             from_book.path,
                                                             element.name + "." + element.format.lower()))
                copyfile(filepath_old, filepath_new)
                to_book.data.append(db.Data(to_book.id,
                                            element.format,
                                            element.uncompressed_size,
                                            to_name))
                existing_formats.append(element.format)
    calibre_db.session.commit()
