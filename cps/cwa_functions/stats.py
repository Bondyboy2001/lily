# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Stats pages, CSV export and scheduled-job (auto-send / ops) listing and cancel routes."""

from flask import request, jsonify
from flask_babel import gettext as _

from .. import ub
from ..usermanagement import login_required_if_no_ano
from ..admin import admin_required
from ..render_template import render_title_template
from ..cw_login import current_user


from ..web import cwa_get_num_books_in_library

# common puts the scripts dir on sys.path, so it must be imported before cwa_db
from .common import cwa_stats, log
from cwa_db import CWA_DB
from ..services.background_scheduler import BackgroundScheduler

@cwa_stats.route('/cwa-scheduled/cancel', methods=["POST"])
@login_required_if_no_ano
@admin_required
def cwa_scheduled_cancel():
    """Cancel a pending scheduled auto-send by id.

    Payload JSON: {id:int}
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        sid = int(data.get('id'))
    except Exception:
        return jsonify({"error": "Invalid id"}), 400

    try:
        from cwa_db import CWA_DB
        db = CWA_DB()
        row = db.scheduled_get_by_id(sid)
        if not row:
            return jsonify({"error": "Not found"}), 404

        # Attempt to remove scheduled APScheduler job
        job_id = (row.get('scheduler_job_id') or '').strip()
        try:
            scheduler = BackgroundScheduler()
            if scheduler and job_id:
                scheduler.remove_job(job_id)
        except Exception:
            # Ignore removal errors (job may have already run or been removed)
            pass

        # Mark as cancelled regardless of job removal result
        db.scheduled_mark_cancelled(sid)
        return jsonify({"status": "cancelled", "id": sid}), 200
    except Exception as e:
        log.error(f"Error cancelling scheduled auto-send: {e}")
        return jsonify({"error": str(e)}), 500

##————————————————————————————————————————————————————————————————————————————##
##                                                                            ##
##                               CWA SHOW HISTORY                             ##
##                                                                            ##
##————————————————————————————————————————————————————————————————————————————##

def get_cwa_stats() -> dict[str,int]:
    """Returns CWA stat totals as a dict (keys are table names except for total_books)"""
    cwa_db = CWA_DB()
    totals = cwa_db.get_stat_totals()
    totals["total_books"] = cwa_get_num_books_in_library() # from web.py

    return totals

### TABLE HEADERS
headers = {
    "enforcement":{
        "no_paths":[
            _("Timestamp"), _("Book ID"), _("Book Title"), _("Book Author"), _("Trigger Type")],
        "with_paths":[
            _("Timestamp"), _("Book ID"), _("Filepath")]
        },
    "epub_fixer":{
        "no_fixes":[
            _("Timestamp"), _("Filename"), _("Manual?"), _("No. Fixes"), _("Original Backed Up?")],
        "with_fixes":[
            _("Timestamp"), _("Filename"), _("Filepath"), _("Fixes Applied")]
        },
    "imports":[
        _("Timestamp"), _("Filename"), _("Original Backed Up?")],
    "conversions":[
        _("Timestamp"), _("Filename"), _("Original Format"), _("End Format"), _("Original Backed Up?")],
}

@cwa_stats.route("/cwa-stats-show", methods=["GET", "POST"])
@login_required_if_no_ano
@admin_required
def cwa_stats_show():
    from datetime import datetime, timedelta
    
    # Check which tab to show (default to user activity)
    active_tab = request.args.get('tab', 'activity')
    
    # Parse date range parameters
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    days_param = request.args.get('days')
    
    # Initialize defaults
    date_range_label = None
    show_warning = False
    today = datetime.now().strftime('%Y-%m-%d')
    
    # Handle 'all' as a special string value, otherwise parse as int
    if days_param == 'all':
        days = None  # None means all time
        date_range_label = "All Time"
    else:
        days = int(days_param) if days_param else None
    
    user_id = request.args.get('user_id', type=int)
    
    # Set default label if not set
    if not date_range_label:
        date_range_label = "Last 30 days"
    
    if start_date and end_date:
        try:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')
            
            # Calculate range in days
            range_days = (end_dt - start_dt).days
            
            # Show warning if range > 1 year
            if range_days > 365:
                show_warning = True
            
            date_range_label = f"{start_date} to {end_date}"
        except ValueError:
            # Invalid date format, fall back to 30 days
            start_date = None
            end_date = None
            days = 30
            date_range_label = "Last 30 days"
    elif days:
        if date_range_label != "All Time":
            date_range_label = f"Last {days} days"
        if days > 365:
            show_warning = True
    elif days is None and date_range_label != "All Time":
        # Default to 30 days if no parameters provided
        days = 30
        date_range_label = "Last 30 days"
    
    cwa_db = CWA_DB()
    
    # Get list of active users for dropdown (resolve names via app.db)
    active_users_raw = cwa_db.get_active_users()
    active_user_ids = [row[0] for row in active_users_raw if row and row[0] is not None]
    user_name_map = {}
    if active_user_ids:
        try:
            db_users = ub.session.query(ub.User.id, ub.User.name, ub.User.email)\
                .filter(ub.User.id.in_(active_user_ids))\
                .all()
            for user_id_entry, name, email in db_users:
                display_name = name or email or _("Unknown User")
                user_name_map[user_id_entry] = display_name
        except Exception as e:
            log.debug(f"Error resolving active users: {e}")

    active_users = []
    seen_user_ids = set()
    unknown_user_ids = set()
    for user_id_entry, user_name in active_users_raw:
        if user_id_entry in seen_user_ids:
            continue
        seen_user_ids.add(user_id_entry)

        resolved_name = user_name_map.get(user_id_entry)
        if resolved_name:
            display_name = resolved_name
            active_users.append((user_id_entry, display_name))
            continue

        raw_name = (user_name or "").strip()
        is_unknown = not raw_name or raw_name.lower() in {"unknown", "unknown user"}

        if is_unknown:
            unknown_user_ids.add(user_id_entry)
            continue

        active_users.append((user_id_entry, raw_name))

    if unknown_user_ids:
        active_users.append((-1, _("Unknown User")))

    active_users.sort(key=lambda item: (item[1] or "").lower())

    user_id_filter = user_id
    if user_id == -1:
        user_id_filter = list(unknown_user_ids)
    
    # Get user activity dashboard stats with date range and optional user filter
    if start_date and end_date:
        dashboard_stats = cwa_db.get_dashboard_stats(start_date=start_date, end_date=end_date, user_id=user_id_filter)
        hourly_heatmap = cwa_db.get_hourly_activity_heatmap(start_date=start_date, end_date=end_date, user_id=user_id_filter)
        reading_velocity = cwa_db.get_reading_velocity(start_date=start_date, end_date=end_date, user_id=user_id_filter)
        format_preferences = cwa_db.get_format_preferences(start_date=start_date, end_date=end_date, user_id=user_id_filter)
        discovery_sources = cwa_db.get_discovery_sources(start_date=start_date, end_date=end_date, user_id=user_id_filter)
        device_breakdown = cwa_db.get_device_breakdown(start_date=start_date, end_date=end_date, user_id=user_id_filter)
        failed_logins = cwa_db.get_failed_logins(start_date=start_date, end_date=end_date)
    else:
        dashboard_stats = cwa_db.get_dashboard_stats(days=days, user_id=user_id_filter)
        hourly_heatmap = cwa_db.get_hourly_activity_heatmap(days=days, user_id=user_id_filter)
        reading_velocity = cwa_db.get_reading_velocity(days=days, user_id=user_id_filter)
        format_preferences = cwa_db.get_format_preferences(days=days, user_id=user_id_filter)
        discovery_sources = cwa_db.get_discovery_sources(days=days, user_id=user_id_filter)
        device_breakdown = cwa_db.get_device_breakdown(days=days, user_id=user_id_filter)
        failed_logins = cwa_db.get_failed_logins(days=days)
    
    # Get library stats (for Library tab)
    if start_date and end_date:
        library_growth = cwa_db.get_library_growth(start_date=start_date, end_date=end_date)
        library_formats = cwa_db.get_library_formats(start_date=start_date, end_date=end_date)
        conversion_stats = cwa_db.get_conversion_success_rate(start_date=start_date, end_date=end_date)
        books_added_stats = cwa_db.get_books_added_count(start_date=start_date, end_date=end_date)
    else:
        library_growth = cwa_db.get_library_growth(days=days)
        library_formats = cwa_db.get_library_formats(days=days)
        conversion_stats = cwa_db.get_conversion_success_rate(days=days)
        books_added_stats = cwa_db.get_books_added_count(days=days)
    
    # Get additional library stats (not time-dependent)
    series_completion = cwa_db.get_series_completion_stats(limit=10)
    publication_years = cwa_db.get_publication_year_distribution()
    most_fixed_books = cwa_db.get_most_fixed_books(limit=10)
    
    # Get Sprint 6 advanced library metrics
    if start_date and end_date:
        rating_statistics = cwa_db.get_rating_statistics(start_date=start_date, end_date=end_date)
    else:
        rating_statistics = cwa_db.get_rating_statistics(days=days)
    
    top_enforced_books = cwa_db.get_top_enforced_books(limit=10)
    import_source_flows = cwa_db.get_import_source_flows(limit=15)
    
    # Get Sprint 5 user activity enhancements
    if start_date and end_date:
        session_duration = cwa_db.get_session_duration_stats(start_date=start_date, end_date=end_date, user_id=user_id_filter)
        search_success = cwa_db.get_search_success_rate(start_date=start_date, end_date=end_date, user_id=user_id_filter)
        shelf_activity = cwa_db.get_shelf_activity_stats(start_date=start_date, end_date=end_date, user_id=user_id_filter, limit=10)
        api_usage_breakdown = cwa_db.get_api_usage_breakdown(start_date=start_date, end_date=end_date, user_id=user_id_filter)
        endpoint_frequency = cwa_db.get_endpoint_frequency_grouped(start_date=start_date, end_date=end_date, user_id=user_id_filter, limit=20)
        api_timing = cwa_db.get_api_timing_heatmap(start_date=start_date, end_date=end_date, user_id=user_id_filter)
    else:
        session_duration = cwa_db.get_session_duration_stats(days=days, user_id=user_id_filter)
        search_success = cwa_db.get_search_success_rate(days=days, user_id=user_id_filter)
        shelf_activity = cwa_db.get_shelf_activity_stats(days=days, user_id=user_id_filter, limit=10)
        api_usage_breakdown = cwa_db.get_api_usage_breakdown(days=days, user_id=user_id_filter)
        endpoint_frequency = cwa_db.get_endpoint_frequency_grouped(days=days, user_id=user_id_filter, limit=20)
        api_timing = cwa_db.get_api_timing_heatmap(days=days, user_id=user_id_filter)
    
    # Get system logs data
    data_enforcement = cwa_db.enforce_show(paths=False, verbose=False, web_ui=True)
    data_enforcement_with_paths = cwa_db.enforce_show(paths=True, verbose=False, web_ui=True)
    data_imports = cwa_db.get_import_history(verbose=False)
    data_conversions = cwa_db.get_conversion_history(verbose=False)
    data_epub_fixer = cwa_db.get_epub_fixer_history(fixes=False, verbose=False)
    data_epub_fixer_with_fixes = cwa_db.get_epub_fixer_history(fixes=True, verbose=False)

    # Get Hardcover auto-fetch stats
    hardcover_stats = None
    try:
        # Get total stats from hardcover_auto_fetch_stats table
        total_processed = cwa_db.execute_read(
            "SELECT SUM(books_processed) FROM hardcover_auto_fetch_stats"
        )
        total_auto_matched = cwa_db.execute_read(
            "SELECT SUM(auto_matched) FROM hardcover_auto_fetch_stats"
        )
        
        # Get pending review count
        pending_review = ub.session.query(ub.HardcoverMatchQueue).filter(
            ub.HardcoverMatchQueue.reviewed == 0
        ).count()
        
        # Get manually reviewed count
        manually_reviewed = ub.session.query(ub.HardcoverMatchQueue).filter(
            ub.HardcoverMatchQueue.reviewed == 1,
            ub.HardcoverMatchQueue.review_action == 'accept'
        ).count()
        
        hardcover_stats = {
            'total_processed': total_processed[0][0] if total_processed and total_processed[0][0] else 0,
            'total_auto_matched': total_auto_matched[0][0] if total_auto_matched and total_auto_matched[0][0] else 0,
            'pending_review': pending_review,
            'manually_reviewed': manually_reviewed
        }
    except Exception as e:
        log.debug(f"Error fetching Hardcover stats: {e}")
        hardcover_stats = None

    return render_title_template("cwa_stats_tabs.html", title=_("Lily Stats & Activity"),
                                page="cwa-stats",
                                active_tab=active_tab,
                                dashboard_stats=dashboard_stats,
                                hourly_heatmap=hourly_heatmap,
                                reading_velocity=reading_velocity,
                                format_preferences=format_preferences,
                                discovery_sources=discovery_sources,
                                device_breakdown=device_breakdown,
                                failed_logins=failed_logins,
                                session_duration=session_duration,
                                search_success=search_success,
                                shelf_activity=shelf_activity,
                                api_usage_breakdown=api_usage_breakdown,
                                endpoint_frequency=endpoint_frequency,
                                api_timing=api_timing,
                                library_growth=library_growth,
                                library_formats=library_formats,
                                conversion_stats=conversion_stats,
                                books_added_stats=books_added_stats,
                                series_completion=series_completion,
                                publication_years=publication_years,
                                most_fixed_books=most_fixed_books,
                                rating_statistics=rating_statistics,
                                top_enforced_books=top_enforced_books,
                                import_source_flows=import_source_flows,
                                date_range_label=date_range_label,
                                show_warning=show_warning,
                                start_date=start_date,
                                end_date=end_date,
                                days=days,
                                today=today,
                                is_admin=current_user.role_admin(),
                                active_users=active_users,
                                selected_user_id=user_id,
                                cwa_stats=get_cwa_stats(),
                                hardcover_stats=hardcover_stats,
                                data_enforcement=data_enforcement, headers_enforcement=headers["enforcement"]["no_paths"], 
                                data_enforcement_with_paths=data_enforcement_with_paths, headers_enforcement_with_paths=headers["enforcement"]["with_paths"], 
                                data_imports=data_imports, headers_import=headers["imports"],
                                data_conversions=data_conversions, headers_conversion=headers["conversions"],
                                data_epub_fixer=data_epub_fixer, headers_epub_fixer=headers["epub_fixer"]["no_fixes"],
                                data_epub_fixer_with_fixes=data_epub_fixer_with_fixes, headers_epub_fixer_with_fixes=headers["epub_fixer"]["with_fixes"])

@cwa_stats.route("/cwa-stats-export-csv/<tab_name>", methods=["GET"])
@login_required_if_no_ano
@admin_required
def export_stats_csv(tab_name):
    """Export stats data as CSV for the specified tab."""
    import csv
    from io import StringIO
    from flask import make_response
    from datetime import datetime
    
    # Parse same filter parameters as main stats route
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    days_param = request.args.get('days')
    user_id = request.args.get('user_id', type=int)
    
    # Handle 'all' as special value
    if days_param == 'all':
        days = None
    else:
        days = int(days_param) if days_param else 30
    
    cwa_db = CWA_DB()
    output = StringIO()
    writer = csv.writer(output)
    
    try:
        if tab_name == 'activity':
            # User Activity Tab Export
            writer.writerow(['=== USER ACTIVITY STATISTICS ==='])
            writer.writerow([])
            
            # Dashboard stats
            if start_date and end_date:
                dashboard_stats = cwa_db.get_dashboard_stats(start_date=start_date, end_date=end_date, user_id=user_id)
            else:
                dashboard_stats = cwa_db.get_dashboard_stats(days=days, user_id=user_id)
            
            writer.writerow(['Metric', 'Value'])
            for key, value in dashboard_stats.get('totals', {}).items():
                writer.writerow([key, value])
            writer.writerow([])
            
            # Top users or most active days
            top_users = dashboard_stats.get('top_users', [])
            if user_id:
                writer.writerow(['=== MOST ACTIVE DAYS ==='])
                writer.writerow(['Date', 'Activity Count'])
                for day, count in top_users:
                    writer.writerow([day, count])
            else:
                writer.writerow(['=== TOP USERS ==='])
                writer.writerow(['User ID', 'Username', 'Event Count'])
                for uid, username, count in top_users:
                    writer.writerow([uid, username, count])
            writer.writerow([])
            
            # Format distribution
            writer.writerow(['=== FORMAT DISTRIBUTION ==='])
            writer.writerow(['Format', 'Download Count'])
            for format_name, count in dashboard_stats.get('format_distribution', []):
                writer.writerow([format_name, count])
            writer.writerow([])
            
            # Discovery sources
            writer.writerow(['=== DISCOVERY SOURCES ==='])
            writer.writerow(['Source', 'Access Count'])
            if start_date and end_date:
                discovery = cwa_db.get_discovery_sources(start_date=start_date, end_date=end_date, user_id=user_id)
            else:
                discovery = cwa_db.get_discovery_sources(days=days, user_id=user_id)
            for source, count in discovery:
                writer.writerow([source, count])
            writer.writerow([])
            
            # Device breakdown
            writer.writerow(['=== DEVICE BREAKDOWN ==='])
            writer.writerow(['Device', 'Access Count'])
            if start_date and end_date:
                devices = cwa_db.get_device_breakdown(start_date=start_date, end_date=end_date, user_id=user_id)
            else:
                devices = cwa_db.get_device_breakdown(days=days, user_id=user_id)
            for device, count in devices:
                writer.writerow([device, count])
            
        elif tab_name == 'library':
            # Library Stats Tab Export
            writer.writerow(['=== LIBRARY STATISTICS ==='])
            writer.writerow([])
            
            # Summary stats
            cwa_stats = get_cwa_stats()
            writer.writerow(['Total Books', cwa_stats['total_books']])
            if start_date and end_date:
                books_added = cwa_db.get_books_added_count(start_date=start_date, end_date=end_date)
                conversions = cwa_db.get_conversion_success_rate(start_date=start_date, end_date=end_date)
            else:
                books_added = cwa_db.get_books_added_count(days=days)
                conversions = cwa_db.get_conversion_success_rate(days=days)
            writer.writerow(['Books Added', books_added.get('total', 0)])
            writer.writerow(['Conversions', conversions.get('total', 0)])
            writer.writerow([])
            
            # Library growth
            writer.writerow(['=== LIBRARY GROWTH ==='])
            writer.writerow(['Date', 'Books Added'])
            if start_date and end_date:
                growth = cwa_db.get_library_growth(start_date=start_date, end_date=end_date)
            else:
                growth = cwa_db.get_library_growth(days=days)
            for date, count in growth:
                writer.writerow([date, count])
            writer.writerow([])
            
            # Format distribution
            writer.writerow(['=== FORMAT DISTRIBUTION ==='])
            writer.writerow(['Format', 'Book Count'])
            if start_date and end_date:
                formats = cwa_db.get_library_formats(start_date=start_date, end_date=end_date)
            else:
                formats = cwa_db.get_library_formats(days=days)
            for format_name, count in formats:
                writer.writerow([format_name, count])
            writer.writerow([])
            
            # Series completion
            writer.writerow(['=== SERIES STATISTICS ==='])
            writer.writerow(['Series Name', 'Book Count', 'Highest Index'])
            series = cwa_db.get_series_completion_stats(limit=50)
            for series_name, book_count, highest_index in series:
                writer.writerow([series_name, book_count, highest_index])
            writer.writerow([])
            
            # Rating statistics
            writer.writerow(['=== RATING STATISTICS ==='])
            if start_date and end_date:
                ratings = cwa_db.get_rating_statistics(start_date=start_date, end_date=end_date)
            else:
                ratings = cwa_db.get_rating_statistics(days=days)
            writer.writerow(['Average Rating', ratings.get('average_rating', 0)])
            writer.writerow(['Unrated Percentage', ratings.get('unrated_percentage', 0)])
            writer.writerow([])
            writer.writerow(['Stars', 'Book Count'])
            for stars, count in ratings.get('rating_distribution', []):
                writer.writerow([stars, count])
            writer.writerow([])
            
            # Top enforced books
            writer.writerow(['=== TOP ENFORCED BOOKS ==='])
            writer.writerow(['Book Title', 'Enforcement Count', 'Last Enforced'])
            top_enforced = cwa_db.get_top_enforced_books(limit=20)
            for book_id, title, count, last_enforced in top_enforced:
                writer.writerow([title, count, last_enforced])
            
        elif tab_name == 'api':
            # API Usage Tab Export
            writer.writerow(['=== API USAGE STATISTICS ==='])
            writer.writerow([])
            
            # API usage breakdown
            writer.writerow(['=== USAGE BREAKDOWN ==='])
            writer.writerow(['Category', 'Access Count'])
            if start_date and end_date:
                breakdown = cwa_db.get_api_usage_breakdown(start_date=start_date, end_date=end_date, user_id=user_id)
            else:
                breakdown = cwa_db.get_api_usage_breakdown(days=days, user_id=user_id)
            for category, count in breakdown:
                writer.writerow([category, count])
            writer.writerow([])
            
            # Endpoint frequency
            writer.writerow(['=== ENDPOINT ACCESS FREQUENCY ==='])
            writer.writerow(['Endpoint', 'Category', 'Access Count', 'Last Accessed'])
            if start_date and end_date:
                endpoints = cwa_db.get_endpoint_frequency_grouped(start_date=start_date, end_date=end_date, user_id=user_id, limit=50)
            else:
                endpoints = cwa_db.get_endpoint_frequency_grouped(days=days, user_id=user_id, limit=50)
            for endpoint, category, count, last_accessed in endpoints:
                writer.writerow([endpoint, category, count, last_accessed])
        
        else:
            # Unknown tab
            writer.writerow(['Error: Unknown tab name'])
        
        # Create response
        output.seek(0)
        csv_data = output.getvalue()
        response = make_response(csv_data)
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'cwa_stats_{tab_name}_{timestamp}.csv'
        response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response
        
    except Exception as e:
        log.error(f"Error generating CSV export for tab {tab_name}: {e}")
        import traceback
        traceback.print_exc()
        
        # Return error CSV
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(['Error generating export'])
        writer.writerow([str(e)])
        output.seek(0)
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        response.headers['Content-Disposition'] = 'attachment; filename="error.csv"'
        return response


@cwa_stats.route("/cwa-stats-debug", methods=["GET"])
@login_required_if_no_ano
@admin_required
def debug_stats_data():
    """Debug endpoint to inspect raw activity data and diagnose parsing issues."""
    try:
        cwa_db = CWA_DB()
        import json as json_module
        
        # Get sample of recent activity records WITHOUT json_extract to avoid errors
        cwa_db.cur.execute("""
            SELECT 
                timestamp,
                user_name,
                event_type,
                item_title,
                extra_data
            FROM cwa_user_activity
            WHERE event_type IN ('DOWNLOAD', 'READ', 'EMAIL', 'LOGIN')
            ORDER BY timestamp DESC
            LIMIT 100
        """)
        
        records = []
        json_valid = 0
        json_invalid = 0
        
        for row in cwa_db.cur.fetchall():
            extra_data_raw = row[4]
            is_valid_json = False
            parsed_data = None
            error_msg = None
            
            # Try to parse as JSON
            if extra_data_raw:
                try:
                    parsed_data = json_module.loads(extra_data_raw)
                    is_valid_json = True
                    json_valid += 1
                except Exception as e:
                    is_valid_json = False
                    json_invalid += 1
                    error_msg = str(e)
            
            records.append({
                'timestamp': row[0],
                'user': row[1],
                'event': row[2],
                'item': row[3],
                'raw_extra_data': extra_data_raw,
                'is_valid_json': is_valid_json,
                'parsed_data': parsed_data,
                'parse_error': error_msg
            })
        
        # Get basic counts
        cwa_db.cur.execute("""
            SELECT 
                event_type,
                COUNT(*) as count,
                COUNT(extra_data) as with_extra_data
            FROM cwa_user_activity
            GROUP BY event_type
            ORDER BY count DESC
        """)
        
        event_counts = [{'event_type': row[0], 'total': row[1], 'with_extra_data': row[2]} 
                       for row in cwa_db.cur.fetchall()]
        
        # Try to get valid JSON count (might fail, that's okay)
        json_stats = {
            'valid_in_sample': json_valid,
            'invalid_in_sample': json_invalid,
            'sample_size': len(records)
        }
        
        return jsonify({
            'event_counts': event_counts,
            'json_stats': json_stats,
            'sample_records': records[:20]  # Only return first 20 to keep response size manageable
        })
        
    except Exception as e:
        import traceback
        return jsonify({
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500


@cwa_stats.route('/cwa-scheduled/upcoming', methods=["GET"])
@login_required_if_no_ano
@admin_required
def cwa_scheduled_upcoming():
    try:
        from cwa_db import CWA_DB
        db = CWA_DB()
        rows = db.scheduled_get_upcoming_autosend(limit=100)
        return jsonify({"items": rows}), 200
    except Exception as e:
        log.error(f"Error fetching upcoming scheduled sends: {e}")
        return jsonify({"items": []}), 200

@cwa_stats.route('/cwa-scheduled/upcoming-ops', methods=["GET"])
@login_required_if_no_ano
@admin_required
def cwa_scheduled_upcoming_ops():
    """Return upcoming scheduled operations (non auto-send), e.g., convert_library, epub_fixer."""
    try:
        db = CWA_DB()
        ops = []
        for jt in ('convert_library', 'epub_fixer'):
            ops.extend(db.scheduled_get_upcoming_by_type(jt, limit=100))
        # sort by time ascending
        ops.sort(key=lambda r: r.get('run_at_utc') or '')
        return jsonify({"items": ops}), 200
    except Exception as e:
        log.error(f"Error fetching upcoming scheduled ops: {e}")
        return jsonify({"items": []}), 200
                                    
@cwa_stats.route("/cwa-stats-show/full-enforcement", methods=["GET", "POST"])
@login_required_if_no_ano
@admin_required
def show_full_enforcement():
    cwa_db = CWA_DB()
    data = cwa_db.enforce_show(paths=False, verbose=True, web_ui=True)
    return render_title_template("cwa_stats_full.html", title=_("Lily - Full Enforcement History"), page="cwa-stats-full",
                                    table_headers=headers["enforcement"]["no_paths"], data=data)

@cwa_stats.route("/cwa-stats-show/full-enforcement-with-paths", methods=["GET", "POST"])
@login_required_if_no_ano
@admin_required
def show_full_enforcement_path():
    cwa_db = CWA_DB()
    data = cwa_db.enforce_show(paths=True, verbose=True, web_ui=True)
    return render_title_template("cwa_stats_full.html", title=_("Lily - Full Enforcement History (w/ Paths)"), page="cwa-stats-full",
                                    table_headers=headers["enforcement"]["with_paths"], data=data)

@cwa_stats.route("/cwa-stats-show/full-imports", methods=["GET", "POST"])
@login_required_if_no_ano
@admin_required
def show_full_imports():
    cwa_db = CWA_DB()
    data = cwa_db.get_import_history(verbose=True)
    return render_title_template("cwa_stats_full.html", title=_("Lily - Full Import History"), page="cwa-stats-full",
                                    table_headers=headers["imports"], data=data)

@cwa_stats.route("/cwa-stats-show/full-conversions", methods=["GET", "POST"])
@login_required_if_no_ano
@admin_required
def show_full_conversions():
    cwa_db = CWA_DB()
    data = cwa_db.get_conversion_history(verbose=True)
    return render_title_template("cwa_stats_full.html", title=_("Lily - Full Conversion History"), page="cwa-stats-full",
                                    table_headers=headers["conversions"], data=data)

@cwa_stats.route("/cwa-stats-show/full-epub-fixer", methods=["GET", "POST"])
@login_required_if_no_ano
@admin_required
def show_full_epub_fixer():
    cwa_db = CWA_DB()
    data = cwa_db.get_epub_fixer_history(fixes=False, verbose=True)
    return render_title_template("cwa_stats_full.html", title=_("Lily - Full EPUB Fixer History (w/out Paths & Fixes)"), page="cwa-stats-full",
                                    table_headers=headers["epub_fixer"]["no_fixes"], data=data)

@cwa_stats.route("/cwa-stats-show/full-epub-fixer-with-paths-fixes", methods=["GET", "POST"])
@login_required_if_no_ano
@admin_required
def show_full_epub_fixer_with_paths_fixes():
    cwa_db = CWA_DB()
    data = cwa_db.get_epub_fixer_history(fixes=True, verbose=True)
    return render_title_template("cwa_stats_full.html", title=_("Lily - Full EPUB Fixer History (w/ Paths & Fixes)"), page="cwa-stats-full",
                                    table_headers=headers["epub_fixer"]["with_fixes"], data=data)
