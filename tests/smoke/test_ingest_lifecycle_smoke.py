#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Smoke Tests for the ingest lifecycle endpoints

Fast static checks that the NFS ingest reconnect/retry hardening stays in place.
"""

import pytest
import ast
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
