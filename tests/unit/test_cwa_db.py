# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""
Unit Tests for CWA Database Module

These tests verify the CWA_DB class functions correctly in isolation.
"""

import pytest
import sys
from pathlib import Path

# Add scripts directory to path (works in both dev container and CI)
scripts_dir = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))

from cwa_db import CWA_DB


@pytest.mark.unit
class TestCWADBInitialization:
    """Test CWA database initialization and schema creation."""

    def test_all_required_tables_exist(self, temp_cwa_db):
        """Verify all required tables are created."""
        expected_tables = {
            'cwa_enforcement',
            'cwa_settings'
        }

        # Extract table names from CREATE TABLE statements
        import re
        actual_table_names = set()
        for table_stmt in temp_cwa_db.tables:
            match = re.search(r'CREATE TABLE IF NOT EXISTS (\w+)\(', table_stmt)
            if match:
                actual_table_names.add(match.group(1))

        assert expected_tables.issubset(actual_table_names), \
            f"Missing tables: {expected_tables - actual_table_names}"


@pytest.mark.unit
class TestCWADBSettings:
    """Test CWA settings management."""

    def test_settings_have_expected_keys(self, temp_cwa_db):
        """Verify all expected settings keys are present"""
        settings = temp_cwa_db.get_cwa_settings()

        expected_keys = ['auto_backup_imports', 'auto_ingest_ignored_formats']
        for key in expected_keys:
            assert key in settings, f"Missing expected setting: {key}"

    def test_can_update_setting(self, temp_cwa_db):
        """Test updating a setting"""
        temp_cwa_db.update_cwa_settings({'auto_backup_imports': False})
        settings = temp_cwa_db.get_cwa_settings()
        assert settings['auto_backup_imports'] == False


@pytest.mark.unit
class TestCWADBEnforcementLogging:
    """Test enforcement operation logging."""

    def test_can_insert_enforcement_log(self, temp_cwa_db):
        """Verify enforcement logs can be inserted."""
        log_info = {
            'timestamp': '2024-01-01 12:00:00',
            'book_id': 1,
            'title': 'Test Book',
            'authors': 'Test Author',
            'file_path': '/test/path.epub'
        }
        temp_cwa_db.enforce_add_entry_from_log(log_info)

        # Verify entry exists in database (schema: id, timestamp, book_id, book_title, author, file_path, trigger_type)
        temp_cwa_db.cur.execute("SELECT * FROM cwa_enforcement WHERE book_title='Test Book'")
        result = temp_cwa_db.cur.fetchone()
        assert result is not None
        assert result[3] == 'Test Book'  # Column 3 is book_title
        assert result[2] == 1  # Column 2 is book_id


@pytest.mark.unit
class TestCWADBErrorHandling:
    """Test database error handling and edge cases."""

    def test_handles_missing_database_gracefully(self, tmp_path, monkeypatch):
        """Verify graceful handling when database doesn't exist."""
        # Point to non-existent path
        monkeypatch.setenv('CWA_DB_PATH', str(tmp_path / "nonexistent"))

        # This should create the database, not crash
        db = CWA_DB(verbose=False)
        assert db.con is not None


if __name__ == '__main__':
    # Allow running directly
    pytest.main([__file__, '-v'])



def test_rebuild_progress_is_one_row_until_cleared(temp_cwa_db):
    assert temp_cwa_db.get_rebuild_progress() is None
    temp_cwa_db.save_rebuild_progress(7, 3, 1, 0, 10)
    temp_cwa_db.save_rebuild_progress(9, 5, 2, 1, 10)
    assert temp_cwa_db.get_rebuild_progress() == {"next_book_id": 9, "checked": 5, "updated": 2,
                                                  "covers": 1, "total": 10, "full": False, "done": []}
    temp_cwa_db.save_rebuild_progress(9, 7, 2, 1, 10, full=True, done={12, 11})
    assert temp_cwa_db.get_rebuild_progress() == {"next_book_id": 9, "checked": 7, "updated": 2,
                                                  "covers": 1, "total": 10, "full": True, "done": [11, 12]}
    temp_cwa_db.clear_rebuild_progress()
    assert temp_cwa_db.get_rebuild_progress() is None


def test_cover_check_keeps_the_last_cover_weighed_per_book(temp_cwa_db):
    assert temp_cwa_db.get_cover_check(4) is None
    temp_cwa_db.save_cover_check(4, "https://covers.example/a.jpg", "10:1")
    temp_cwa_db.save_cover_check(4, "https://covers.example/b.jpg", "12:2")
    assert temp_cwa_db.get_cover_check(4) == ("https://covers.example/b.jpg", "12:2")
    assert temp_cwa_db.get_cover_check(5) is None
