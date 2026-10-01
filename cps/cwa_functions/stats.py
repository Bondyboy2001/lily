# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Stats pages and their CSV export."""

from datetime import datetime

from flask import request
from flask_babel import gettext as _

from .. import ub
from ..usermanagement import login_required_if_no_ano
from ..admin import admin_required
from ..render_template import render_title_template


from ..web import cwa_get_num_books_in_library

# common puts the scripts dir on sys.path, so it must be imported before cwa_db
from .common import cwa_stats, log
from cwa_db import CWA_DB

def parse_stats_date_range(start_date, end_date):
    """Validate a 'YYYY-MM-DD' start/end pair from the query string.

    Returns both dates normalised, or (None, None) when either is missing or malformed, in
    which case callers fall back to the days-based range (as the main stats page does).
    """
    if not start_date or not end_date:
        return None, None
    try:
        return (datetime.strptime(start_date, '%Y-%m-%d').strftime('%Y-%m-%d'),
                datetime.strptime(end_date, '%Y-%m-%d').strftime('%Y-%m-%d'))
    except (TypeError, ValueError):
        return None, None


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
    "imports":[
        _("Timestamp"), _("Filename"), _("Original Backed Up?")],
}

@cwa_stats.route("/cwa-stats-show", methods=["GET", "POST"])
@login_required_if_no_ano
@admin_required
def cwa_stats_show():
    from datetime import datetime

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

    # Activity dashboard stats for the date range
    if start_date and end_date:
        dashboard_stats = cwa_db.get_dashboard_stats(start_date=start_date, end_date=end_date)
        hourly_heatmap = cwa_db.get_hourly_activity_heatmap(start_date=start_date, end_date=end_date)
        reading_velocity = cwa_db.get_reading_velocity(start_date=start_date, end_date=end_date)
        format_preferences = cwa_db.get_format_preferences(start_date=start_date, end_date=end_date)
        discovery_sources = cwa_db.get_discovery_sources(start_date=start_date, end_date=end_date)
        device_breakdown = cwa_db.get_device_breakdown(start_date=start_date, end_date=end_date)
        failed_logins = cwa_db.get_failed_logins(start_date=start_date, end_date=end_date)
    else:
        dashboard_stats = cwa_db.get_dashboard_stats(days=days)
        hourly_heatmap = cwa_db.get_hourly_activity_heatmap(days=days)
        reading_velocity = cwa_db.get_reading_velocity(days=days)
        format_preferences = cwa_db.get_format_preferences(days=days)
        discovery_sources = cwa_db.get_discovery_sources(days=days)
        device_breakdown = cwa_db.get_device_breakdown(days=days)
        failed_logins = cwa_db.get_failed_logins(days=days)

    # Get library stats (for Library tab)
    if start_date and end_date:
        library_growth = cwa_db.get_library_growth(start_date=start_date, end_date=end_date)
        library_formats = cwa_db.get_library_formats(start_date=start_date, end_date=end_date)
        books_added_stats = cwa_db.get_books_added_count(start_date=start_date, end_date=end_date)
    else:
        library_growth = cwa_db.get_library_growth(days=days)
        library_formats = cwa_db.get_library_formats(days=days)
        books_added_stats = cwa_db.get_books_added_count(days=days)

    # Get additional library stats (not time-dependent)
    series_completion = cwa_db.get_series_completion_stats(limit=10)
    publication_years = cwa_db.get_publication_year_distribution()

    # Get Sprint 6 advanced library metrics
    if start_date and end_date:
        rating_statistics = cwa_db.get_rating_statistics(start_date=start_date, end_date=end_date)
    else:
        rating_statistics = cwa_db.get_rating_statistics(days=days)

    top_enforced_books = cwa_db.get_top_enforced_books(limit=10)

    # Get Sprint 5 user activity enhancements
    if start_date and end_date:
        session_duration = cwa_db.get_session_duration_stats(start_date=start_date, end_date=end_date)
        search_success = cwa_db.get_search_success_rate(start_date=start_date, end_date=end_date)
        shelf_activity = cwa_db.get_shelf_activity_stats(start_date=start_date, end_date=end_date, limit=10)
        api_usage_breakdown = cwa_db.get_api_usage_breakdown(start_date=start_date, end_date=end_date)
        endpoint_frequency = cwa_db.get_endpoint_frequency_grouped(start_date=start_date, end_date=end_date, limit=20)
        api_timing = cwa_db.get_api_timing_heatmap(start_date=start_date, end_date=end_date)
    else:
        session_duration = cwa_db.get_session_duration_stats(days=days)
        search_success = cwa_db.get_search_success_rate(days=days)
        shelf_activity = cwa_db.get_shelf_activity_stats(days=days, limit=10)
        api_usage_breakdown = cwa_db.get_api_usage_breakdown(days=days)
        endpoint_frequency = cwa_db.get_endpoint_frequency_grouped(days=days, limit=20)
        api_timing = cwa_db.get_api_timing_heatmap(days=days)

    # Get system logs data
    data_enforcement = cwa_db.enforce_show(paths=False, verbose=False, web_ui=True)
    data_enforcement_with_paths = cwa_db.enforce_show(paths=True, verbose=False, web_ui=True)
    data_imports = cwa_db.get_import_history(verbose=False)

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
                                books_added_stats=books_added_stats,
                                series_completion=series_completion,
                                publication_years=publication_years,
                                rating_statistics=rating_statistics,
                                top_enforced_books=top_enforced_books,
                                date_range_label=date_range_label,
                                show_warning=show_warning,
                                start_date=start_date,
                                end_date=end_date,
                                days=days,
                                today=today,
                                cwa_stats=get_cwa_stats(),
                                hardcover_stats=hardcover_stats,
                                data_enforcement=data_enforcement, headers_enforcement=headers["enforcement"]["no_paths"],
                                data_enforcement_with_paths=data_enforcement_with_paths, headers_enforcement_with_paths=headers["enforcement"]["with_paths"],
                                data_imports=data_imports, headers_import=headers["imports"])

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
    start_date, end_date = parse_stats_date_range(request.args.get('start_date'), request.args.get('end_date'))
    days_param = request.args.get('days')

    # Handle 'all' as special value
    if days_param == 'all':
        days = None
    else:
        try:
            days = max(0, int(days_param)) if days_param else 30
        except ValueError:
            days = 30

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
                dashboard_stats = cwa_db.get_dashboard_stats(start_date=start_date, end_date=end_date)
            else:
                dashboard_stats = cwa_db.get_dashboard_stats(days=days)

            writer.writerow(['Metric', 'Value'])
            for key, value in dashboard_stats.get('totals', {}).items():
                writer.writerow([key, value])
            writer.writerow([])

            writer.writerow(['=== MOST ACTIVE DAYS ==='])
            writer.writerow(['Date', 'Activity Count'])
            for day, count in dashboard_stats.get('most_active_days', []):
                writer.writerow([day, count])
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
                discovery = cwa_db.get_discovery_sources(start_date=start_date, end_date=end_date)
            else:
                discovery = cwa_db.get_discovery_sources(days=days)
            for source, count in discovery:
                writer.writerow([source, count])
            writer.writerow([])

            # Device breakdown
            writer.writerow(['=== DEVICE BREAKDOWN ==='])
            writer.writerow(['Device', 'Access Count'])
            if start_date and end_date:
                devices = cwa_db.get_device_breakdown(start_date=start_date, end_date=end_date)
            else:
                devices = cwa_db.get_device_breakdown(days=days)
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
            else:
                books_added = cwa_db.get_books_added_count(days=days)
            writer.writerow(['Books Added', books_added.get('total', 0)])
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
                breakdown = cwa_db.get_api_usage_breakdown(start_date=start_date, end_date=end_date)
            else:
                breakdown = cwa_db.get_api_usage_breakdown(days=days)
            for category, count in breakdown:
                writer.writerow([category, count])
            writer.writerow([])

            # Endpoint frequency
            writer.writerow(['=== ENDPOINT ACCESS FREQUENCY ==='])
            writer.writerow(['Endpoint', 'Category', 'Access Count', 'Last Accessed'])
            if start_date and end_date:
                endpoints = cwa_db.get_endpoint_frequency_grouped(start_date=start_date, end_date=end_date, limit=50)
            else:
                endpoints = cwa_db.get_endpoint_frequency_grouped(days=days, limit=50)
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
