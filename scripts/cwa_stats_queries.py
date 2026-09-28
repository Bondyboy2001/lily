# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Read-only statistics queries over cwa.db, used by the stats pages.

Mixed into CWA_DB (scripts/cwa_db.py), which provides the cursor (self.cur)
and the user-filter helpers (self._build_user_filter, self._has_user_filter).

Dates, day counts and limits are always passed as bound parameters
(:start_date, :end_date, :prev_start, :prev_end, :days, :days2, :limit), never
formatted into the SQL. ``_bind`` collects and validates them from the caller's locals.
"""

from datetime import datetime

_DATE_PARAMS = ("start_date", "end_date", "prev_start", "prev_end")


def valid_stats_date(value):
    """Return ``value`` normalised to 'YYYY-MM-DD'; raise ValueError for anything else."""
    if not isinstance(value, str):
        raise ValueError(f"Invalid date: {value!r}")
    return datetime.strptime(value.strip(), "%Y-%m-%d").strftime("%Y-%m-%d")


def _bind(scope):
    """Named SQL parameters for a stats query, validated, taken from the calling method's locals()."""
    params = {}
    for name in _DATE_PARAMS:
        value = scope.get(name)
        if value:
            params[name] = valid_stats_date(value)
    days = scope.get("days")
    if days is not None:
        days = int(days)
        if days < 0:
            raise ValueError("Invalid day count: %r" % (days,))
        params["days"] = days
        params["days2"] = days * 2
    limit = scope.get("limit")
    if limit is not None:
        params["limit"] = int(limit)
    return params


class CWAStatsQueries:
    def get_active_users(self):
        """Returns list of distinct users who have activity logged."""
        try:
            self.cur.execute("""
                SELECT DISTINCT user_id, COALESCE(user_name, 'Unknown User') as user_name
                FROM cwa_user_activity
                WHERE user_id IS NOT NULL
                ORDER BY user_name ASC
            """, _bind(locals()))
            return self.cur.fetchall()
        except Exception as e:
            print(f"[cwa-db] Error fetching active users: {e}")
            return []
    def get_discovery_sources(self, days=None, start_date=None, end_date=None, user_id=None):
        """Returns count of book discoveries grouped by source.
        
        Returns list of tuples: (source, count)
        """
        try:
            # Build date filter
            if start_date and end_date:
                date_filter = "timestamp BETWEEN date(:start_date) AND date(:end_date, '+1 day')"
            else:
                days = days or 30
                date_filter = "timestamp >= date('now', '-' || :days || ' days')"
            
            # Add user filter if provided
            user_filter = self._build_user_filter(user_id)
            combined_filter = date_filter + user_filter
            
            self.cur.execute(f"""
                SELECT 
                    COALESCE(
                        CASE WHEN json_valid(extra_data) 
                            THEN json_extract(extra_data, '$.source')
                            ELSE NULL
                        END,
                        'direct'
                    ) as source,
                    COUNT(*) as count
                FROM cwa_user_activity
                WHERE event_type IN ('READ', 'DOWNLOAD')
                    AND {combined_filter}
                GROUP BY source
                ORDER BY count DESC
            """, _bind(locals()))
            return self.cur.fetchall()
        except Exception as e:
            print(f"[cwa-db] Error getting discovery sources: {e}")
            return []

    def get_device_breakdown(self, days=None, start_date=None, end_date=None, user_id=None):
        """Returns activity count grouped by device type.
        
        Returns list of tuples: (device_type, count)
        """
        try:
            # Build date filter
            if start_date and end_date:
                date_filter = "timestamp BETWEEN date(:start_date) AND date(:end_date, '+1 day')"
            else:
                days = days or 30
                date_filter = "timestamp >= date('now', '-' || :days || ' days')"
            
            # Add user filter if provided
            user_filter = self._build_user_filter(user_id)
            combined_filter = date_filter + user_filter
            
            self.cur.execute(f"""
                SELECT 
                    COALESCE(
                        CASE WHEN json_valid(extra_data) 
                            THEN json_extract(extra_data, '$.device_type')
                            ELSE NULL
                        END,
                        'unknown'
                    ) as device_type,
                    COUNT(*) as count
                FROM cwa_user_activity
                WHERE {combined_filter}
                GROUP BY device_type
                ORDER BY count DESC
            """, _bind(locals()))
            return self.cur.fetchall()
        except Exception as e:
            print(f"[cwa-db] Error getting device breakdown: {e}")
            return []

    def get_failed_logins(self, days=None, start_date=None, end_date=None):
        """Returns failed login attempts with details.
        
        Returns list of tuples: (ip, username_attempted, timestamp, count)
        """
        try:
            # Build date filter
            if start_date and end_date:
                date_filter = "timestamp BETWEEN date(:start_date) AND date(:end_date, '+1 day')"
            else:
                days = days or 30
                date_filter = "timestamp >= date('now', '-' || :days || ' days')"
            
            self.cur.execute(f"""
                SELECT 
                    json_extract(extra_data, '$.ip') as ip_address,
                    json_extract(extra_data, '$.username_attempted') as username,
                    MAX(timestamp) as last_attempt,
                    COUNT(*) as attempt_count
                FROM cwa_user_activity
                WHERE event_type = 'LOGIN_FAILED'
                    AND {date_filter}
                GROUP BY ip_address, username
                ORDER BY attempt_count DESC, last_attempt DESC
                LIMIT 20
            """, _bind(locals()))
            return self.cur.fetchall()
        except Exception as e:
            print(f"[cwa-db] Error getting failed logins: {e}")
            return []

    def get_library_growth(self, days=None, start_date=None, end_date=None):
        """Returns books added per day from Calibre metadata.db for library growth timeline.
        
        Returns list of tuples: (date, books_added_count)
        """
        try:
            import sqlite3
            
            # Connect to Calibre's metadata.db
            metadata_db_path = "/calibre-library/metadata.db"
            metadata_con = sqlite3.connect(metadata_db_path, timeout=10)
            metadata_cur = metadata_con.cursor()
            
            # Build date filter
            if start_date and end_date:
                date_filter = "timestamp BETWEEN date(:start_date) AND date(:end_date, '+1 day')"
            else:
                days = days or 365  # Default to 1 year for growth chart
                date_filter = "timestamp >= date('now', '-' || :days || ' days')"
            
            metadata_cur.execute(f"""
                SELECT 
                    date(timestamp) as add_date,
                    COUNT(*) as books_added
                FROM books
                WHERE timestamp IS NOT NULL
                    AND {date_filter}
                GROUP BY add_date
                ORDER BY add_date ASC
            """, _bind(locals()))
            result = metadata_cur.fetchall()
            metadata_con.close()
            return result
        except Exception as e:
            print(f"[cwa-db] Error getting library growth: {e}")
            return []

    def get_books_added_count(self, days=None, start_date=None, end_date=None):
        """Returns total books added in time period with trend comparison.
        
        Returns dict with: total, trend
        """
        try:
            import sqlite3
            
            # Connect to Calibre's metadata.db
            metadata_db_path = "/calibre-library/metadata.db"
            metadata_con = sqlite3.connect(metadata_db_path, timeout=10)
            metadata_cur = metadata_con.cursor()
            
            # Build date filter for current period
            if start_date and end_date:
                date_filter = "timestamp BETWEEN date(:start_date) AND date(:end_date, '+1 day')"
            elif days:
                date_filter = "timestamp >= date('now', '-' || :days || ' days')"
            else:
                date_filter = "1=1"  # All time - no filter
            
            # Get current period count
            metadata_cur.execute(f"""
                SELECT COUNT(*) as total
                FROM books
                WHERE timestamp IS NOT NULL
                    AND {date_filter}
            """, _bind(locals()))
            current = metadata_cur.fetchone()
            
            # Get previous period for trend comparison
            if start_date and end_date:
                # Calculate previous period of same duration
                from datetime import datetime, timedelta
                start_dt = datetime.strptime(start_date, '%Y-%m-%d')
                end_dt = datetime.strptime(end_date, '%Y-%m-%d')
                duration = (end_dt - start_dt).days
                prev_start = (start_dt - timedelta(days=duration)).strftime('%Y-%m-%d')
                prev_end = start_date
                prev_filter = "timestamp BETWEEN date(:prev_start) AND date(:prev_end)"
            elif days:
                prev_filter = "timestamp >= date('now', '-' || :days2 || ' days') AND timestamp < date('now', '-' || :days || ' days')"
            else:
                # All time - no previous period comparison
                prev_filter = "1=0"  # Returns 0
            
            metadata_cur.execute(f"""
                SELECT COUNT(*) as total
                FROM books
                WHERE timestamp IS NOT NULL
                    AND {prev_filter}
            """, _bind(locals()))
            previous = metadata_cur.fetchone()
            
            total = current[0] or 0
            prev_total = previous[0] or 0
            
            # Calculate trend based on volume change
            if prev_total > 0:
                trend = ((total - prev_total) / prev_total * 100)
            else:
                trend = 0
            
            metadata_con.close()
            
            return {
                'total': total,
                'trend': round(trend, 1)
            }
        except Exception as e:
            print(f"[cwa-db] Error getting books added count: {e}")
            import traceback
            traceback.print_exc()
            return {
                'total': 0,
                'trend': 0
            }

    def get_library_formats(self, days=None, start_date=None, end_date=None):
        """Returns format distribution from Calibre metadata.db.
        
        Args:
            days: Number of days back from now (optional)
            start_date: Start date string 'YYYY-MM-DD' (optional)
            end_date: End date string 'YYYY-MM-DD' (optional)
        
        Returns list of tuples: (format, count)
        """
        try:
            import sqlite3
            
            # Connect to Calibre's metadata.db
            metadata_db_path = "/calibre-library/metadata.db"
            metadata_con = sqlite3.connect(metadata_db_path, timeout=10)
            metadata_cur = metadata_con.cursor()
            
            # Build date filter
            if start_date and end_date:
                date_filter = "WHERE books.timestamp BETWEEN date(:start_date) AND date(:end_date, '+1 day')"
            elif days:
                date_filter = "WHERE books.timestamp >= date('now', '-' || :days || ' days')"
            else:
                date_filter = ""  # No filter, show all time
            
            metadata_cur.execute(f"""
                SELECT 
                    UPPER(data.format) as format,
                    COUNT(*) as count
                FROM books
                JOIN data ON books.id = data.book
                {date_filter}
                GROUP BY format
                ORDER BY count DESC
            """, _bind(locals()))
            result = metadata_cur.fetchall()
            metadata_con.close()
            return result
        except Exception as e:
            print(f"[cwa-db] Error getting library formats: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_series_completion_stats(self, limit=10):
        """Returns largest series by book count from Calibre metadata.db.
        
        Args:
            limit: Number of series to return (default 10)
        
        Returns: List of tuples: (series_name, book_count, highest_index)
        """
        try:
            import sqlite3
            
            # Connect to Calibre's metadata.db
            metadata_db_path = "/calibre-library/metadata.db"
            metadata_con = sqlite3.connect(metadata_db_path, timeout=10)
            metadata_cur = metadata_con.cursor()
            
            # Query series with book counts and highest index, ordered by count
            metadata_cur.execute(f"""
                SELECT 
                    s.name as series_name,
                    COUNT(DISTINCT bs.book) as book_count,
                    CAST(MAX(b.series_index) AS INTEGER) as highest_index
                FROM series s
                JOIN books_series_link bs ON s.id = bs.series
                JOIN books b ON bs.book = b.id
                GROUP BY s.id, s.name
                ORDER BY book_count DESC, series_name ASC
                LIMIT :limit
            """, _bind(locals()))
            
            results = metadata_cur.fetchall()
            metadata_con.close()
            
            return results
        except Exception as e:
            print(f"[cwa-db] Error getting series completion stats: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_publication_year_distribution(self):
        """Returns distribution of books by publication year from Calibre metadata.db.
        
        Returns: List of tuples: (year, count)
        """
        try:
            import sqlite3
            
            # Connect to Calibre's metadata.db
            metadata_db_path = "/calibre-library/metadata.db"
            metadata_con = sqlite3.connect(metadata_db_path, timeout=10)
            metadata_cur = metadata_con.cursor()
            
            # Extract year from pubdate and count books
            metadata_cur.execute("""
                SELECT 
                    CAST(strftime('%Y', pubdate) as INTEGER) as year,
                    COUNT(*) as count
                FROM books
                WHERE pubdate IS NOT NULL
                    AND pubdate != '0101-01-01 00:00:00+00:00'
                    AND pubdate != ''
                GROUP BY year
                HAVING year >= 1800 AND year <= 2030
                ORDER BY year ASC
            """, _bind(locals()))
            
            results = metadata_cur.fetchall()
            metadata_con.close()
            
            return results
        except Exception as e:
            print(f"[cwa-db] Error getting publication year distribution: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_session_duration_stats(self, days=None, start_date=None, end_date=None, user_id=None):
        """Returns session duration statistics by calculating time between LOGIN events.
        
        Args:
            days: Number of days back (optional)
            start_date/end_date: Custom range 'YYYY-MM-DD' (takes precedence)
            user_id: Filter by user (optional)
        
        Returns: Dict with average_minutes, median_minutes, session_distribution
        """
        try:
            # Build date filter
            if start_date and end_date:
                date_filter = "timestamp BETWEEN date(:start_date) AND date(:end_date, '+1 day')"
            else:
                days = days or 30
                date_filter = "timestamp >= date('now', '-' || :days || ' days')"
            
            # Add user filter if provided
            user_filter = self._build_user_filter(user_id)
            combined_filter = date_filter + user_filter
            
            # Get LOGIN events ordered by user and time
            self.cur.execute(f"""
                WITH ordered_logins AS (
                    SELECT 
                        user_id,
                        user_name,
                        timestamp,
                        LEAD(timestamp) OVER (PARTITION BY user_id ORDER BY timestamp) as next_login,
                        julianday(LEAD(timestamp) OVER (PARTITION BY user_id ORDER BY timestamp)) - 
                        julianday(timestamp) as duration_days
                    FROM cwa_user_activity
                    WHERE event_type = 'LOGIN' AND {combined_filter}
                )
                SELECT 
                    ROUND(AVG(duration_days * 24 * 60), 1) as avg_minutes,
                    duration_days * 24 * 60 as session_minutes
                FROM ordered_logins
                WHERE next_login IS NOT NULL
                    AND duration_days < 1  -- Ignore sessions > 24 hours
            """, _bind(locals()))
            
            results = self.cur.fetchall()
            if not results:
                return {'average_minutes': 0, 'median_minutes': 0, 'distribution': []}
            
            # Calculate average
            avg_result = self.cur.execute(f"""
                WITH ordered_logins AS (
                    SELECT 
                        julianday(LEAD(timestamp) OVER (PARTITION BY user_id ORDER BY timestamp)) - 
                        julianday(timestamp) as duration_days
                    FROM cwa_user_activity
                    WHERE event_type = 'LOGIN' AND {combined_filter}
                )
                SELECT ROUND(AVG(duration_days * 24 * 60), 1) as avg_minutes
                FROM ordered_logins
                WHERE duration_days IS NOT NULL AND duration_days < 1
            """, _bind(locals())).fetchone()
            
            avg_minutes = avg_result[0] if avg_result and avg_result[0] else 0
            
            # Get distribution for histogram (5-minute buckets)
            self.cur.execute(f"""
                WITH ordered_logins AS (
                    SELECT 
                        julianday(LEAD(timestamp) OVER (PARTITION BY user_id ORDER BY timestamp)) - 
                        julianday(timestamp) as duration_days
                    FROM cwa_user_activity
                    WHERE event_type = 'LOGIN' AND {combined_filter}
                )
                SELECT 
                    CAST((duration_days * 24 * 60) / 5 AS INTEGER) * 5 as bucket_start,
                    COUNT(*) as count
                FROM ordered_logins
                WHERE duration_days IS NOT NULL AND duration_days < 1
                GROUP BY bucket_start
                ORDER BY bucket_start
            """, _bind(locals()))
            
            distribution = self.cur.fetchall()
            
            return {
                'average_minutes': avg_minutes,
                'distribution': distribution  # List of (bucket_start_minutes, count)
            }
            
        except Exception as e:
            print(f"[cwa-db] Error getting session duration stats: {e}")
            import traceback
            traceback.print_exc()
            return {'average_minutes': 0, 'distribution': []}

    def get_search_success_rate(self, days=None, start_date=None, end_date=None, user_id=None):
        """Returns search success rate (searches followed by DOWNLOAD/READ within 5 minutes).
        
        Args:
            days: Number of days back (optional)
            start_date/end_date: Custom range 'YYYY-MM-DD' (takes precedence)
            user_id: Filter by user (optional)
        
        Returns: Dict with total_searches, successful_searches, success_rate, trend
        """
        try:
            # Build date filter
            if start_date and end_date:
                date_filter = "timestamp BETWEEN date(:start_date) AND date(:end_date, '+1 day')"
                date_filter_prev = None
            else:
                days = days or 30
                date_filter = "timestamp >= date('now', '-' || :days || ' days')"
                date_filter_prev = "timestamp >= date('now', '-' || :days2 || ' days') AND timestamp < date('now', '-' || :days || ' days')"
            
            # Add user filter if provided
            user_filter = self._build_user_filter(user_id)
            combined_filter = date_filter + user_filter
            
            # Count total searches in period
            total_searches = self.cur.execute(f"""
                SELECT COUNT(*)
                FROM cwa_user_activity
                WHERE event_type = 'SEARCH' AND {combined_filter}
            """, _bind(locals())).fetchone()[0]
            
            # Count successful searches (followed by DOWNLOAD or READ within 5 minutes)
            successful_searches = self.cur.execute(f"""
                SELECT COUNT(DISTINCT s.id)
                FROM cwa_user_activity s
                WHERE s.event_type = 'SEARCH' 
                    AND {combined_filter}
                    AND EXISTS (
                        SELECT 1 FROM cwa_user_activity a
                        WHERE a.user_id = s.user_id
                            AND a.event_type IN ('DOWNLOAD', 'READ')
                            AND a.timestamp BETWEEN s.timestamp AND datetime(s.timestamp, '+5 minutes')
                    )
            """, _bind(locals())).fetchone()[0]
            
            success_rate = (successful_searches / total_searches * 100) if total_searches > 0 else 0
            
            # Calculate trend if we have previous period
            trend = 0
            if date_filter_prev:
                combined_filter_prev = date_filter_prev + user_filter
                total_prev = self.cur.execute(f"""
                    SELECT COUNT(*)
                    FROM cwa_user_activity
                    WHERE event_type = 'SEARCH' AND {combined_filter_prev}
                """, _bind(locals())).fetchone()[0]
                
                successful_prev = self.cur.execute(f"""
                    SELECT COUNT(DISTINCT s.id)
                    FROM cwa_user_activity s
                    WHERE s.event_type = 'SEARCH' 
                        AND {combined_filter_prev}
                        AND EXISTS (
                            SELECT 1 FROM cwa_user_activity a
                            WHERE a.user_id = s.user_id
                                AND a.event_type IN ('DOWNLOAD', 'READ')
                                AND a.timestamp BETWEEN s.timestamp AND datetime(s.timestamp, '+5 minutes')
                        )
                """, _bind(locals())).fetchone()[0]
                
                success_rate_prev = (successful_prev / total_prev * 100) if total_prev > 0 else 0
                trend = success_rate - success_rate_prev if success_rate_prev > 0 else 0
            
            return {
                'total_searches': total_searches,
                'successful_searches': successful_searches,
                'success_rate': round(success_rate, 1),
                'trend': round(trend, 1)
            }
            
        except Exception as e:
            print(f"[cwa-db] Error getting search success rate: {e}")
            import traceback
            traceback.print_exc()
            return {
                'total_searches': 0,
                'successful_searches': 0,
                'success_rate': 0,
                'trend': 0
            }

    def get_shelf_activity_stats(self, days=None, start_date=None, end_date=None, user_id=None, limit=10):
        """Returns most active shelves by number of additions.
        
        Args:
            days: Number of days back (optional)
            start_date/end_date: Custom range 'YYYY-MM-DD' (takes precedence)
            user_id: Filter by user (optional)
            limit: Number of shelves to return (default 10)
        
        Returns: List of tuples: (shelf_name, add_count, remove_count, net_change)
        """
        try:
            
            # Build date filter
            if start_date and end_date:
                date_filter = "timestamp BETWEEN date(:start_date) AND date(:end_date, '+1 day')"
            else:
                days = days or 30
                date_filter = "timestamp >= date('now', '-' || :days || ' days')"
            
            # Add user filter if provided
            user_filter = self._build_user_filter(user_id)
            combined_filter = date_filter + user_filter
            
            # Get shelf activity (parse shelf_name from extra_data JSON)
            self.cur.execute(f"""
                SELECT 
                    json_extract(extra_data, '$.shelf_name') as shelf_name,
                    SUM(CASE WHEN event_type = 'SHELF_ADD' THEN 1 ELSE 0 END) as add_count,
                    SUM(CASE WHEN event_type = 'SHELF_REMOVE' THEN 1 ELSE 0 END) as remove_count,
                    SUM(CASE 
                        WHEN event_type = 'SHELF_ADD' THEN 1 
                        WHEN event_type = 'SHELF_REMOVE' THEN -1 
                        ELSE 0 
                    END) as net_change
                FROM cwa_user_activity
                WHERE event_type IN ('SHELF_ADD', 'SHELF_REMOVE')
                    AND {combined_filter}
                    AND json_valid(extra_data)
                    AND json_extract(extra_data, '$.shelf_name') IS NOT NULL
                GROUP BY shelf_name
                ORDER BY add_count DESC, net_change DESC
                LIMIT :limit
            """, _bind(locals()))
            
            return self.cur.fetchall()
            
        except Exception as e:
            print(f"[cwa-db] Error getting shelf activity stats: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_api_usage_breakdown(self, days=None, start_date=None, end_date=None, user_id=None):
        """Returns API usage breakdown by category (Web, Kobo, OPDS, Email).
        
        Args:
            days: Number of days back (optional)
            start_date/end_date: Custom range 'YYYY-MM-DD' (takes precedence)
            user_id: Filter by user (optional)
        
        Returns: List of tuples: (category, count)
        """
        try:
            # Build date filter
            if start_date and end_date:
                date_filter = "timestamp BETWEEN date(:start_date) AND date(:end_date, '+1 day')"
            else:
                days = days or 30
                date_filter = "timestamp >= date('now', '-' || :days || ' days')"
            
            # Add user filter if provided
            user_filter = self._build_user_filter(user_id)
            combined_filter = date_filter + user_filter
            
            # Categorize events
            self.cur.execute(f"""
                SELECT 
                    CASE 
                        WHEN event_type = 'KOBO_SYNC' THEN 'Kobo Sync'
                        WHEN event_type = 'OPDS_ACCESS' THEN 'OPDS Feed'
                        WHEN event_type = 'EMAIL' THEN 'Email Delivery'
                        WHEN event_type IN ('DOWNLOAD', 'READ', 'SEARCH', 'LOGIN') THEN 'Web UI'
                        ELSE 'Other'
                    END as category,
                    COUNT(*) as count
                FROM cwa_user_activity
                WHERE {combined_filter}
                GROUP BY category
                ORDER BY count DESC
            """, _bind(locals()))
            
            return self.cur.fetchall()
            
        except Exception as e:
            print(f"[cwa-db] Error getting API usage breakdown: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_endpoint_frequency_grouped(self, days=None, start_date=None, end_date=None, user_id=None, limit=20):
        """Returns endpoint access frequency with grouping by category.
        
        Args:
            days: Number of days back (optional)
            start_date/end_date: Custom range 'YYYY-MM-DD' (takes precedence)
            user_id: Filter by user (optional)
            limit: Number of endpoints to return (default 20)
        
        Returns: List of tuples: (endpoint, category, count, last_accessed)
        """
        try:
            
            # Build date filter
            if start_date and end_date:
                date_filter = "timestamp BETWEEN date(:start_date) AND date(:end_date, '+1 day')"
            else:
                days = days or 30
                date_filter = "timestamp >= date('now', '-' || :days || ' days')"
            
            # Add user filter if provided
            user_filter = self._build_user_filter(user_id)
            combined_filter = date_filter + user_filter
            
            # Debug: Check what event types actually exist
            debug_query = f"SELECT DISTINCT event_type FROM cwa_user_activity WHERE {combined_filter}"
            print(f"[cwa-db] Checking event types with filter: {debug_query}")
            self.cur.execute(debug_query, _bind(locals()))
            event_types = self.cur.fetchall()
            print(f"[cwa-db] Found event types: {event_types}")
            
            # Debug: Check what events exist
            query = f"""
                SELECT 
                    CASE
                        WHEN extra_data IS NOT NULL AND extra_data != '' 
                            AND json_valid(extra_data) = 1
                            AND json_extract(extra_data, '$.endpoint') IS NOT NULL 
                        THEN json_extract(extra_data, '$.endpoint')
                        ELSE event_type
                    END as endpoint,
                    CASE 
                        WHEN event_type = 'KOBO_SYNC' THEN 'Kobo'
                        WHEN event_type = 'OPDS_ACCESS' THEN 'OPDS'
                        WHEN event_type = 'EMAIL' THEN 'Email'
                        WHEN event_type = 'DOWNLOAD' THEN 'Downloads'
                        WHEN event_type = 'READ' THEN 'Reading'
                        WHEN event_type = 'SEARCH' THEN 'Search'
                        WHEN event_type = 'LOGIN' THEN 'Authentication'
                        ELSE 'Other'
                    END as category,
                    COUNT(*) as access_count,
                    MAX(timestamp) as last_accessed
                FROM cwa_user_activity
                WHERE {combined_filter}
                GROUP BY endpoint, category
                HAVING COUNT(*) > 0
                ORDER BY access_count DESC, last_accessed DESC
                LIMIT :limit
            """
            
            print(f"[cwa-db] Endpoint frequency query: {query}")
            self.cur.execute(query, _bind(locals()))
            
            results = self.cur.fetchall()
            print(f"[cwa-db] Endpoint frequency results: {results}")
            return results
            
        except Exception as e:
            print(f"[cwa-db] Error getting endpoint frequency: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_api_timing_heatmap(self, days=None, start_date=None, end_date=None, user_id=None):
        """Returns API activity timing for heatmap (hour × day of week).
        
        Args:
            days: Number of days back (optional)
            start_date/end_date: Custom range 'YYYY-MM-DD' (takes precedence)
            user_id: Filter by user (optional)
        
        Returns: List of tuples: (day_of_week, hour, count)
        """
        try:
            # Build date filter
            if start_date and end_date:
                date_filter = "timestamp BETWEEN date(:start_date) AND date(:end_date, '+1 day')"
            else:
                days = days or 30
                date_filter = "timestamp >= date('now', '-' || :days || ' days')"
            
            # Add user filter if provided
            user_filter = self._build_user_filter(user_id)
            combined_filter = date_filter + user_filter
            
            # Get API activity by time (focus on API events)
            self.cur.execute(f"""
                SELECT 
                    CAST(strftime('%w', timestamp) AS INTEGER) as day_of_week,
                    CAST(strftime('%H', timestamp) AS INTEGER) as hour,
                    COUNT(*) as api_count
                FROM cwa_user_activity
                WHERE event_type IN ('KOBO_SYNC', 'OPDS_ACCESS', 'EMAIL', 'DOWNLOAD')
                    AND {combined_filter}
                GROUP BY day_of_week, hour
                ORDER BY day_of_week, hour
            """, _bind(locals()))
            
            return self.cur.fetchall()
            
        except Exception as e:
            print(f"[cwa-db] Error getting API timing heatmap: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_rating_statistics(self, days=None, start_date=None, end_date=None):
        """Returns rating statistics from metadata.db.
        
        Args:
            days: Number of days back (optional) - filters books added in period
            start_date/end_date: Custom range 'YYYY-MM-DD' (takes precedence)
        
        Returns: Dict with:
            - average_rating: float (0-5 scale)
            - rating_distribution: [(stars, count), ...] sorted by stars descending
            - unrated_percentage: float
            - trend: float (percentage change from previous period)
        """
        try:
            import sqlite3
            
            metadata_db_path = "/calibre-library/metadata.db"
            metadata_con = sqlite3.connect(metadata_db_path, timeout=10)
            metadata_cur = metadata_con.cursor()
            
            # Build date filter for books added in time period
            if start_date and end_date:
                date_filter = "WHERE b.timestamp BETWEEN date(:start_date) AND date(:end_date, '+1 day')"
                # Calculate previous period for trend
                from datetime import datetime, timedelta
                start_dt = datetime.strptime(start_date, '%Y-%m-%d')
                end_dt = datetime.strptime(end_date, '%Y-%m-%d')
                period_days = (end_dt - start_dt).days
                prev_start = (start_dt - timedelta(days=period_days)).strftime('%Y-%m-%d')
                prev_end = start_date
                prev_date_filter = "WHERE b.timestamp BETWEEN date(:prev_start) AND date(:prev_end, '+1 day')"
            elif days:
                date_filter = "WHERE b.timestamp >= date('now', '-' || :days || ' days')"
                prev_date_filter = "WHERE b.timestamp >= date('now', '-' || :days2 || ' days') AND b.timestamp < date('now', '-' || :days || ' days')"
            else:
                date_filter = ""
                prev_date_filter = ""
            
            # Get average rating (convert 0-10 scale to 0-5)
            avg_query = f"""
                SELECT AVG(r.rating) / 2.0 as avg_rating
                FROM books b
                JOIN books_ratings_link brl ON b.id = brl.book
                JOIN ratings r ON brl.rating = r.id
                {date_filter.replace('WHERE', 'WHERE' if date_filter else '') if date_filter else ''}
                {'AND' if date_filter else 'WHERE'} r.rating > 0
            """
            metadata_cur.execute(avg_query, _bind(locals()))
            avg_result = metadata_cur.fetchone()
            average_rating = round(avg_result[0], 2) if avg_result and avg_result[0] else 0.0
            
            # Get previous period average for trend
            if prev_date_filter:
                prev_avg_query = f"""
                    SELECT AVG(r.rating) / 2.0 as avg_rating
                    FROM books b
                    JOIN books_ratings_link brl ON b.id = brl.book
                    JOIN ratings r ON brl.rating = r.id
                    {prev_date_filter}
                    AND r.rating > 0
                """
                metadata_cur.execute(prev_avg_query, _bind(locals()))
                prev_avg_result = metadata_cur.fetchone()
                prev_average = prev_avg_result[0] if prev_avg_result and prev_avg_result[0] else 0.0
                
                if prev_average > 0:
                    trend = round(((average_rating - prev_average) / prev_average) * 100, 1)
                else:
                    trend = 0.0
            else:
                trend = 0.0
            
            # Get rating distribution (1-5 stars)
            dist_query = f"""
                SELECT 
                    CAST(r.rating / 2 AS INTEGER) as stars,
                    COUNT(*) as count
                FROM books b
                JOIN books_ratings_link brl ON b.id = brl.book
                JOIN ratings r ON brl.rating = r.id
                {date_filter}
                {'AND' if date_filter else 'WHERE'} r.rating > 0
                GROUP BY stars
                ORDER BY stars DESC
            """
            metadata_cur.execute(dist_query, _bind(locals()))
            rating_distribution = metadata_cur.fetchall()
            
            # Get total books and unrated count
            total_query = f"SELECT COUNT(*) FROM books b {date_filter}"
            metadata_cur.execute(total_query, _bind(locals()))
            total_books = metadata_cur.fetchone()[0]
            
            unrated_query = f"""
                SELECT COUNT(*) 
                FROM books b
                LEFT JOIN books_ratings_link brl ON b.id = brl.book
                LEFT JOIN ratings r ON brl.rating = r.id
                {date_filter}
                {'AND' if date_filter else 'WHERE'} (brl.rating IS NULL OR r.rating = 0)
            """
            metadata_cur.execute(unrated_query, _bind(locals()))
            unrated_books = metadata_cur.fetchone()[0]
            
            unrated_percentage = round((unrated_books / total_books * 100), 1) if total_books > 0 else 0.0
            
            metadata_con.close()
            
            return {
                'average_rating': average_rating,
                'rating_distribution': rating_distribution,
                'unrated_percentage': unrated_percentage,
                'trend': trend,
                'total_books': total_books,
                'rated_books': total_books - unrated_books
            }
            
        except Exception as e:
            print(f"[cwa-db] Error getting rating statistics: {e}")
            import traceback
            traceback.print_exc()
            return {
                'average_rating': 0.0,
                'rating_distribution': [],
                'unrated_percentage': 0.0,
                'trend': 0.0,
                'total_books': 0,
                'rated_books': 0
            }

    def get_top_enforced_books(self, limit=10):
        """Returns top books by enforcement count (cross-database query).
        
        Args:
            limit: Number of top books to return (default 10, max 10000)
        
        Returns: List of tuples: (book_id, title, enforcement_count, last_enforced)
        """
        try:
            import sqlite3
            
            # Limit to reasonable size
            limit = min(limit, 10000)
            
            # Pass 1: Get enforcement counts from cwa.db
            self.cur.execute(f"""
                SELECT 
                    book_id,
                    COUNT(DISTINCT file_path) as enforcement_count,
                    MAX(timestamp) as last_enforced
                FROM cwa_enforcement
                GROUP BY book_id
                ORDER BY enforcement_count DESC
                LIMIT :limit
            """, _bind(locals()))
            enforcement_data = self.cur.fetchall()
            
            if not enforcement_data:
                return []
            
            # Pass 2: Enrich with book titles from metadata.db
            metadata_db_path = "/calibre-library/metadata.db"
            metadata_con = sqlite3.connect(metadata_db_path, timeout=10)
            metadata_cur = metadata_con.cursor()
            
            results = []
            for book_id, enforcement_count, last_enforced in enforcement_data:
                try:
                    metadata_cur.execute("SELECT title FROM books WHERE id = ?", (book_id,))
                    title_result = metadata_cur.fetchone()
                    if title_result:
                        results.append((book_id, title_result[0], enforcement_count, last_enforced))
                except Exception as e:
                    print(f"[cwa-db] Error getting title for book_id {book_id}: {e}")
                    # Include with placeholder title
                    results.append((book_id, f"Book #{book_id}", enforcement_count, last_enforced))
            
            metadata_con.close()
            return results
            
        except Exception as e:
            print(f"[cwa-db] Error getting top enforced books: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_hourly_activity_heatmap(self, days=None, start_date=None, end_date=None, user_id=None):
        """Returns activity count by hour of day and day of week for heatmap visualization.
        
        Returns list of tuples: (day_of_week, hour, count)
        day_of_week: 0=Sunday, 1=Monday, ..., 6=Saturday
        hour: 0-23
        """
        try:
            # Build date filter
            if start_date and end_date:
                date_filter = "timestamp BETWEEN date(:start_date) AND date(:end_date, '+1 day')"
            else:
                days = days or 30
                date_filter = "timestamp >= date('now', '-' || :days || ' days')"
            
            # Add user filter if provided
            user_filter = self._build_user_filter(user_id)
            combined_filter = date_filter + user_filter
            
            self.cur.execute(f"""
                SELECT 
                    CAST(strftime('%w', timestamp) AS INTEGER) as day_of_week,
                    CAST(strftime('%H', timestamp) AS INTEGER) as hour,
                    COUNT(*) as activity_count
                FROM cwa_user_activity
                WHERE {combined_filter}
                GROUP BY day_of_week, hour
                ORDER BY day_of_week, hour
            """, _bind(locals()))
            return self.cur.fetchall()
        except Exception as e:
            print(f"[cwa-db] Error getting hourly activity heatmap: {e}")
            return []

    def get_reading_velocity(self, days=None, start_date=None, end_date=None, user_id=None):
        """Returns books read per week with data for moving average calculation.
        
        Returns list of tuples: (week_label, books_read_count)
        week_label format: 'YYYY-Www' (e.g., '2025-W01')
        """
        try:
            # Build date filter
            if start_date and end_date:
                date_filter = "timestamp BETWEEN date(:start_date) AND date(:end_date, '+1 day')"
            else:
                days = days or 30
                date_filter = "timestamp >= date('now', '-' || :days || ' days')"
            
            # Add user filter if provided
            user_filter = self._build_user_filter(user_id)
            combined_filter = date_filter + user_filter
            
            self.cur.execute(f"""
                SELECT 
                    strftime('%Y-W%W', timestamp) as week,
                    COUNT(DISTINCT item_id) as books_read
                FROM cwa_user_activity
                WHERE event_type = 'READ'
                    AND {combined_filter}
                GROUP BY week
                ORDER BY week
            """, _bind(locals()))
            return self.cur.fetchall()
        except Exception as e:
            print(f"[cwa-db] Error getting reading velocity: {e}")
            return []

    def get_format_preferences(self, days=None, start_date=None, end_date=None, user_id=None):
        """Returns format usage by user for stacked bar chart.
        
        Returns list of tuples: (user_name, format, count)
        """
        try:
            # Build date filter
            if start_date and end_date:
                date_filter = "timestamp BETWEEN date(:start_date) AND date(:end_date, '+1 day')"
            else:
                days = days or 30
                date_filter = "timestamp >= date('now', '-' || :days || ' days')"
            
            # Add user filter if provided
            user_filter = self._build_user_filter(user_id)
            combined_filter = date_filter + user_filter
            
            self.cur.execute(f"""
                SELECT 
                    COALESCE(user_name, 'Unknown User') as user_name,
                    UPPER(COALESCE(
                        CASE WHEN json_valid(extra_data) THEN json_extract(extra_data, '$.format') END,
                        'Unknown'
                    )) as format,
                    COUNT(*) as count
                FROM cwa_user_activity
                WHERE event_type IN ('DOWNLOAD', 'READ')
                    AND {combined_filter}
                GROUP BY user_name, format
                ORDER BY user_name, count DESC
            """, _bind(locals()))
            return self.cur.fetchall()
        except Exception as e:
            print(f"[cwa-db] Error getting format preferences: {e}")
            return []

    def get_dashboard_stats(self, days=None, start_date=None, end_date=None, user_id=None):
        """Returns comprehensive activity stats for the user dashboard.
        
        Args:
            days: Number of days back from now (legacy support)
            start_date: Start date string 'YYYY-MM-DD' (takes precedence over days)
            end_date: End date string 'YYYY-MM-DD' (takes precedence over days)
            user_id: Filter stats for specific user ID (optional)
        """
        try:
            # Use date range if provided, otherwise fall back to days
            if start_date and end_date:
                date_filter = "timestamp BETWEEN date(:start_date) AND date(:end_date, '+1 day')"
            else:
                days = days or 30  # Default to 30 days
                date_filter = "timestamp >= date('now', '-' || :days || ' days')"
            
            # Add user filter if provided
            user_filter = self._build_user_filter(user_id)
            combined_filter = date_filter + user_filter
            
            # 1. Activity timeline - Daily counts by event type
            self.cur.execute(f"""
                SELECT date(timestamp) as day, event_type, COUNT(*) as count
                FROM cwa_user_activity 
                WHERE {combined_filter}
                GROUP BY day, event_type
                ORDER BY day ASC
            """, _bind(locals()))
            timeline_data = self.cur.fetchall()

            # 2. Top active users or most active days (depending on user filter)
            if self._has_user_filter(user_id):
                # Show most active days for specific user
                self.cur.execute(f"""
                    SELECT date(timestamp) as day, COUNT(*) as activity_count
                    FROM cwa_user_activity 
                    WHERE {combined_filter}
                    GROUP BY day
                    ORDER BY activity_count DESC 
                    LIMIT 10
                """, _bind(locals()))
                top_users = self.cur.fetchall()
            else:
                # Show top active users across all users
                self.cur.execute(f"""
                    SELECT user_id, COALESCE(user_name, 'Unknown User') as user_name, COUNT(*) as activity_count
                    FROM cwa_user_activity 
                    WHERE {combined_filter}
                    GROUP BY user_id, user_name
                    ORDER BY activity_count DESC 
                    LIMIT 10
                """, _bind(locals()))
                top_users = self.cur.fetchall()

            # 3. Most popular books (reads + downloads + emails combined)
            self.cur.execute(f"""
                SELECT item_title, item_id, COUNT(*) as hits
                FROM cwa_user_activity 
                WHERE item_id IS NOT NULL 
                  AND event_type IN ('DOWNLOAD', 'READ', 'EMAIL')
                  AND {combined_filter}
                GROUP BY item_id, item_title
                ORDER BY hits DESC 
                LIMIT 10
            """, _bind(locals()))
            top_books = self.cur.fetchall()
            
            # 4. Recent search terms
            self.cur.execute(f"""
                SELECT extra_data as search_term, timestamp, user_name
                FROM cwa_user_activity 
                WHERE event_type = 'SEARCH' 
                  AND extra_data IS NOT NULL
                  AND {combined_filter}
                ORDER BY timestamp DESC 
                LIMIT 15
            """, _bind(locals()))
            recent_searches = self.cur.fetchall()

            # 5. Download format distribution
            self.cur.execute(f"""
                SELECT 
                    UPPER(COALESCE(
                        CASE WHEN json_valid(extra_data) 
                            THEN json_extract(extra_data, '$.format')
                            ELSE extra_data 
                        END,
                        'UNKNOWN'
                    )) as format,
                    COUNT(*) as count
                FROM cwa_user_activity
                WHERE event_type IN ('DOWNLOAD', 'EMAIL')
                  AND extra_data IS NOT NULL
                  AND {combined_filter}
                GROUP BY format
                ORDER BY count DESC
            """, _bind(locals()))
            format_distribution = self.cur.fetchall()

            # 6. Event type breakdown (LOGIN, DOWNLOAD, READ, SEARCH, EMAIL)
            self.cur.execute(f"""
                SELECT event_type, COUNT(*) as count
                FROM cwa_user_activity
                WHERE {combined_filter}
                GROUP BY event_type
                ORDER BY count DESC
            """, _bind(locals()))
            event_breakdown = self.cur.fetchall()

            # 7. Total activity metrics
            if self._has_user_filter(user_id):
                # For single user, show total logins instead of active users
                self.cur.execute(f"""
                    SELECT 
                        COUNT(*) as total_events,
                        COUNT(CASE WHEN event_type = 'LOGIN' THEN 1 END) as total_logins,
                        COUNT(DISTINCT CASE WHEN event_type IN ('DOWNLOAD', 'EMAIL') THEN item_id END) as unique_downloads,
                        COUNT(DISTINCT CASE WHEN event_type = 'READ' THEN item_id END) as unique_reads,
                        0 as active_users,
                        COUNT(CASE WHEN event_type IN ('DOWNLOAD', 'EMAIL') THEN 1 END) as total_downloads,
                        COUNT(CASE WHEN event_type = 'READ' THEN 1 END) as total_reads,
                        COUNT(CASE WHEN event_type = 'SEARCH' THEN 1 END) as total_searches
                    FROM cwa_user_activity
                    WHERE {combined_filter}
                """, _bind(locals()))
            else:
                # For all users, show active user count
                self.cur.execute(f"""
                    SELECT 
                        COUNT(*) as total_events,
                        COUNT(CASE WHEN event_type = 'LOGIN' THEN 1 END) as total_logins,
                        COUNT(DISTINCT CASE WHEN event_type IN ('DOWNLOAD', 'EMAIL') THEN item_id END) as unique_downloads,
                        COUNT(DISTINCT CASE WHEN event_type = 'READ' THEN item_id END) as unique_reads,
                        COUNT(DISTINCT user_id) as active_users,
                        COUNT(CASE WHEN event_type IN ('DOWNLOAD', 'EMAIL') THEN 1 END) as total_downloads,
                        COUNT(CASE WHEN event_type = 'READ' THEN 1 END) as total_reads,
                        COUNT(CASE WHEN event_type = 'SEARCH' THEN 1 END) as total_searches
                    FROM cwa_user_activity
                    WHERE {combined_filter}
                """, _bind(locals()))
            totals = self.cur.fetchone()

            return {
                "timeline": timeline_data or [],
                "top_users": top_users or [],
                "top_books": top_books or [],
                "recent_searches": recent_searches or [],
                "format_distribution": format_distribution or [],
                "event_breakdown": event_breakdown or [],
                "totals": {
                    "total_events": totals[0] if totals else 0,
                    "total_logins": totals[1] if totals else 0,
                    "unique_downloads": totals[2] if totals else 0,
                    "unique_reads": totals[3] if totals else 0,
                    "active_users": totals[4] if totals else 0,
                    "total_downloads": totals[5] if totals else 0,
                    "total_reads": totals[6] if totals else 0,
                    "total_searches": totals[7] if totals else 0,
                }
            }
        except Exception as e:
            print(f"[cwa-db] Error getting dashboard stats: {e}")
            import traceback
            traceback.print_exc()
            return {
                "timeline": [],
                "top_users": [],
                "top_books": [],
                "recent_searches": [],
                "format_distribution": [],
                "event_breakdown": [],
                "totals": {
                    "total_events": 0,
                    "active_users": 0,
                    "unique_downloads": 0,
                    "unique_reads": 0,
                    "total_logins": 0,
                    "total_downloads": 0,
                    "total_reads": 0,
                    "total_searches": 0,
                }
            }
