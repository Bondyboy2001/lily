#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Smoke Tests for Operations Scheduling Endpoints

Fast static verification tests that check Convert Library and EPUB Fixer
scheduling code structure exists and is properly integrated.
"""

import pytest
import sys
import os
import ast
import re
from pathlib import Path

# Mark all tests in this file as smoke tests
pytestmark = pytest.mark.smoke

# Get project root (3 levels up from this file)
project_root = Path(__file__).parent.parent.parent


def _function_source(source_path, function_name):
    source = source_path.read_text(encoding='utf-8')
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return ast.get_source_segment(source, node)
    raise AssertionError(f"Function {function_name} not found in {source_path}")


def _assert_schedule_forms(template_path, endpoint, route_path, library_ops_path):
    """The schedule buttons must be CSRF-protected POST forms for 5 and 15 minutes.

    They used to be plain GET links (``<a href="{{ url_for(endpoint, delay=5) }}">``);
    scheduling is a state-changing admin action, so each delay is now a
    ``<form method="post">`` targeting the same endpoint with a csrf_token field,
    and the route only accepts POST.
    """
    content = template_path.read_text(encoding='utf-8')

    action_re = re.escape("url_for('" + endpoint + "', delay=") + r"(?P<delay>\w+)\)"
    form_re = re.compile(
        r"<form\b(?P<attrs>[^>]*)>(?P<body>.*?)</form>",
        re.DOTALL | re.IGNORECASE,
    )
    schedule_forms = []
    for match in form_re.finditer(content):
        action = re.search(action_re, match.group('attrs'))
        if action:
            schedule_forms.append((match, action.group('delay')))
    assert schedule_forms, f"No schedule form posting to {endpoint} in {template_path.name}"

    delays = set()
    for match, delay in schedule_forms:
        attrs, body = match.group('attrs'), match.group('body')
        assert re.search(r'method\s*=\s*"post"', attrs, re.IGNORECASE), \
            f"Schedule form in {template_path.name} must use method=\"post\""
        assert re.search(
            r'<input[^>]*type="hidden"[^>]*name="csrf_token"[^>]*value="\{\{\s*csrf_token\(\)\s*\}\}"',
            body,
        ), f"Schedule form in {template_path.name} is missing its csrf_token field"
        assert 'type="submit"' in body
        if delay.isdigit():
            delays.add(int(delay))
        else:
            # delay comes from an enclosing Jinja loop, e.g. {% for delay in [5, 15] %}
            loops = re.findall(
                r"\{%\s*for\s+" + re.escape(delay) + r"\s+in\s+[\[(]([^\])]*)[\])]\s*%\}",
                content[:match.start()],
            )
            assert loops, f"Loop variable {delay!r} is not defined by an enclosing for-loop"
            delays.update(int(v) for v in re.findall(r"\d+", loops[-1]))
    assert {5, 15} <= delays, f"Expected 5m and 15m schedule buttons, found {sorted(delays)}"

    # The route behind the buttons must only accept POST (matches the form).
    ops_source = library_ops_path.read_text(encoding='utf-8')
    assert f"route('{route_path}', methods=[\"POST\"])" in ops_source


class TestConvertLibraryScheduling:
    """Test Convert Library scheduling integration"""
    
    def test_convert_library_has_schedule_route(self):
        """Verify convert_library blueprint has schedule/<delay> route"""
        cwa_functions_file = project_root / 'cps' / 'cwa_functions' / 'library_ops.py'
        
        with open(cwa_functions_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Verify route exists
        assert "@convert_library.route('/cwa-convert-library/schedule/<int:delay>'" in content
    
    def test_convert_library_schedule_uses_shared_scheduler(self):
        """Verify the admin schedule route schedules in-process (not via an HTTP call to itself)"""
        cwa_functions_file = project_root / 'cps' / 'cwa_functions' / 'library_ops.py'
        
        with open(cwa_functions_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        assert "_schedule_library_op('convert_library', 'Convert Library', TaskConvertLibraryRun, delay, username)" in content
        assert 'get_internal_api_url("/cwa-internal/schedule-convert-library")' not in content
    
    def test_internal_convert_library_endpoint_exists(self):
        """Verify /cwa-internal/schedule-convert-library endpoint exists"""
        cwa_functions_file = project_root / 'cps' / 'cwa_functions' / 'library_ops.py'
        
        with open(cwa_functions_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Verify internal endpoint function exists
        assert "@cwa_internal.route('/cwa-internal/schedule-convert-library'" in content
        assert "def cwa_internal_schedule_convert_library():" in content
    
    def test_convert_library_persists_to_db(self):
        """Verify convert_library scheduling persists job to cwa.db"""
        cwa_functions_file = project_root / 'cps' / 'cwa_functions' / 'library_ops.py'
        
        with open(cwa_functions_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Verify DB persistence call with correct job type
        assert "_schedule_library_op_response('convert_library', 'Convert Library', TaskConvertLibraryRun)" in content
        assert "CWA_DB().scheduled_add_job(job_type, run_at_utc_iso" in content
    
    def test_convert_library_template_has_schedule_buttons(self):
        """Verify convert library template has scheduling buttons"""
        template_file = project_root / 'cps' / 'templates' / 'cwa_convert_library.html'
        
        with open(template_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Buttons are CSRF-protected POST forms targeting the admin-only schedule route
        assert "current_user.role_admin()" in content
        _assert_schedule_forms(
            template_file,
            'convert_library.schedule_convert_library',
            '/cwa-convert-library/schedule/<int:delay>',
            project_root / 'cps' / 'cwa_functions' / 'library_ops.py',
        )
    
    def test_convert_library_task_wrapper_exists(self):
        """Verify TaskConvertLibraryRun task wrapper exists"""
        # Check if ops.py imports or defines the task
        ops_file = project_root / 'cps' / 'tasks' / 'ops.py'
        
        if os.path.exists(ops_file):
            with open(ops_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            assert 'TaskConvertLibraryRun' in content


class TestEpubFixerScheduling:
    """Test EPUB Fixer scheduling integration"""
    
    def test_epub_fixer_has_schedule_route(self):
        """Verify epub_fixer blueprint has schedule/<delay> route"""
        cwa_functions_file = project_root / 'cps' / 'cwa_functions' / 'library_ops.py'
        
        with open(cwa_functions_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Verify route exists
        assert "@epub_fixer.route('/cwa-epub-fixer/schedule/<int:delay>'" in content
    
    def test_epub_fixer_schedule_uses_shared_scheduler(self):
        """Verify the admin schedule route schedules in-process (not via an HTTP call to itself)"""
        cwa_functions_file = project_root / 'cps' / 'cwa_functions' / 'library_ops.py'
        
        with open(cwa_functions_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        assert "_schedule_library_op('epub_fixer', 'EPUB Fixer', TaskEpubFixerRun, delay, username)" in content
        assert 'get_internal_api_url("/cwa-internal/schedule-epub-fixer")' not in content
    
    def test_internal_epub_fixer_endpoint_exists(self):
        """Verify /cwa-internal/schedule-epub-fixer endpoint exists"""
        cwa_functions_file = project_root / 'cps' / 'cwa_functions' / 'library_ops.py'
        
        with open(cwa_functions_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Verify internal endpoint function exists
        assert "@cwa_internal.route('/cwa-internal/schedule-epub-fixer'" in content
        assert "def cwa_internal_schedule_epub_fixer():" in content
    
    def test_epub_fixer_persists_to_db(self):
        """Verify epub_fixer scheduling persists job to cwa.db"""
        cwa_functions_file = project_root / 'cps' / 'cwa_functions' / 'library_ops.py'
        
        with open(cwa_functions_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Verify DB persistence call with correct job type
        assert "_schedule_library_op_response('epub_fixer', 'EPUB Fixer', TaskEpubFixerRun)" in content
        assert "CWA_DB().scheduled_add_job(job_type, run_at_utc_iso" in content
    
    def test_epub_fixer_template_has_schedule_buttons(self):
        """Verify epub fixer template has scheduling buttons"""
        template_file = project_root / 'cps' / 'templates' / 'cwa_epub_fixer.html'
        
        with open(template_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Buttons are CSRF-protected POST forms targeting the admin-only schedule route
        assert "current_user.role_admin()" in content
        _assert_schedule_forms(
            template_file,
            'epub_fixer.schedule_epub_fixer',
            '/cwa-epub-fixer/schedule/<int:delay>',
            project_root / 'cps' / 'cwa_functions' / 'library_ops.py',
        )
    
    def test_epub_fixer_task_wrapper_exists(self):
        """Verify TaskEpubFixerRun task wrapper exists"""
        ops_file = project_root / 'cps' / 'tasks' / 'ops.py'
        
        if os.path.exists(ops_file):
            with open(ops_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            assert 'TaskEpubFixerRun' in content


class TestUpcomingOpsEndpoint:
    """Test /cwa-scheduled/upcoming-ops endpoint"""
    
    def test_upcoming_ops_endpoint_exists(self):
        """Verify /cwa-scheduled/upcoming-ops endpoint exists"""
        cwa_functions_file = project_root / 'cps' / 'cwa_functions' / 'stats.py'
        
        with open(cwa_functions_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Verify endpoint exists
        assert "@cwa_stats.route('/cwa-scheduled/upcoming-ops'" in content
        assert "def cwa_scheduled_upcoming_ops():" in content
    
    def test_upcoming_ops_queries_both_types(self):
        """Verify upcoming-ops endpoint queries both convert_library and epub_fixer"""
        cwa_functions_file = project_root / 'cps' / 'cwa_functions' / 'stats.py'
        
        with open(cwa_functions_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Verify both job types are queried
        assert "'convert_library', 'epub_fixer'" in content or "('convert_library', 'epub_fixer')" in content
    
    def test_tasks_template_shows_upcoming_ops(self):
        """Verify tasks.html template displays upcoming operations"""
        template_file = project_root / 'cps' / 'templates' / 'tasks.html'
        
        with open(template_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Verify upcoming ops table exists
        assert 'Upcoming scheduled operations' in content or 'cwa_scheduled_upcoming_ops' in content
        assert 'upcomingopstable' in content


class TestNfsImportLifecycleHardening:
    """Static checks for NFS ingest lifecycle endpoint hardening."""

    def test_reconnect_db_logs_exceptions_with_stack(self):
        """Verify reconnect-db failures preserve exception stack details."""
        cwa_functions_file = project_root / 'cps' / 'cwa_functions' / 'ingest.py'

        function_body = _function_source(cwa_functions_file, 'cwa_internal_reconnect_db')

        assert 'log.exception(' in function_body

    def test_ingest_batch_follow_up_retries_reconnect_once_for_transient_failures(self):
        """Verify ingest-side reconnect retry is bounded and covers transient failures."""
        ingest_processor_file = project_root / 'scripts' / 'ingest_processor.py'

        function_body = _function_source(ingest_processor_file, '_post_internal_endpoint')

        assert 'max_attempts = 2 if path == "/cwa-internal/reconnect-db" else 1' in function_body
        assert 'retrying once' in function_body
        assert '500' in function_body
        assert '503' in function_body
        assert 'requests.exceptions.Timeout' in function_body
        assert 'requests.exceptions.ConnectionError' in function_body


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
