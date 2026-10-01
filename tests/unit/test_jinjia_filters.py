# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import pytest
import sys
import os

# Add the parent directory to the path so we can import cps modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from cps.jinjia import flatten_breaks_filter as flatten_breaks, formatfloat


class TestFormatFloatFilter:
    """Tests for the formatfloat Jinja2 filter"""

    def test_formatfloat_with_float(self):
        """Test formatfloat with float input"""
        assert formatfloat(1.5, 2) == '1.5'
        assert formatfloat(1.0, 2) == '1'
        assert formatfloat(1.234, 2) == '1.23'
        assert formatfloat(10.999, 2) == '11'

    def test_formatfloat_with_string(self):
        """Test formatfloat with string input (as stored in DB)"""
        assert formatfloat('1.5', 2) == '1.5'
        assert formatfloat('1.0', 2) == '1'
        assert formatfloat('1.234', 2) == '1.23'
        assert formatfloat('10.999', 2) == '11'

    def test_formatfloat_with_zero(self):
        """Test formatfloat with zero values"""
        assert formatfloat(0, 2) == '0'
        assert formatfloat(0.0, 2) == '0'
        assert formatfloat('0', 2) == '0'
        assert formatfloat('0.0', 2) == '0'
        assert formatfloat('0.00', 2) == '0'

    def test_formatfloat_with_none(self):
        """Test formatfloat with None"""
        assert formatfloat(None, 2) == ''

    def test_formatfloat_with_empty_string(self):
        """Test formatfloat with empty string"""
        assert formatfloat('', 2) == ''
        assert formatfloat('  ', 2) == ''

    def test_formatfloat_with_invalid_string(self):
        """Test formatfloat with non-numeric string - should return empty string"""
        assert formatfloat('invalid', 2) == ''
        assert formatfloat('abc', 2) == ''
        assert formatfloat('1.2.3', 2) == ''

    def test_formatfloat_with_different_decimals(self):
        """Test formatfloat with different decimal precision"""
        assert formatfloat(1.5, 1) == '1.5'
        assert formatfloat(1.5, 3) == '1.5'
        assert formatfloat(1.12345, 4) == '1.1235'  # rounds up
        assert formatfloat('3.14159', 2) == '3.14'

    def test_formatfloat_removes_trailing_zeros(self):
        """Test that formatfloat removes trailing zeros"""
        assert formatfloat(1.00, 2) == '1'
        assert formatfloat(1.10, 2) == '1.1'
        assert formatfloat(1.20, 2) == '1.2'
        assert formatfloat('2.00', 2) == '2'

    def test_formatfloat_integer_input(self):
        """Test formatfloat with integer input"""
        assert formatfloat(1, 2) == '1'
        assert formatfloat(10, 2) == '10'
        assert formatfloat(100, 2) == '100'

    def test_formatfloat_negative_numbers(self):
        """Test formatfloat with negative numbers"""
        assert formatfloat(-1.5, 2) == '-1.5'
        assert formatfloat('-1.5', 2) == '-1.5'
        assert formatfloat(-1.0, 2) == '-1'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])


class TestFlattenBreaksFilter:
    """Tests for the flatten_breaks Jinja2 filter used on book descriptions"""

    def test_br_becomes_space(self):
        assert flatten_breaks('<p>one<br>two<br/>three<br />four</p>') == '<p>one two three four</p>'

    def test_paragraphs_are_joined(self):
        assert flatten_breaks('<p>one</p>\n<p class="x">two</p>') == '<p>one two</p>'

    def test_empty_paragraphs_are_dropped(self):
        assert flatten_breaks('<p>one</p><p>&nbsp;</p><p><br></p><p>two</p>') == '<p>one two</p>'

    def test_newlines_collapse_and_inline_markup_survives(self):
        assert flatten_breaks('a\n\n  <b>b</b>\nc') == 'a <b>b</b> c'

    def test_none_and_empty(self):
        assert flatten_breaks(None) == ''
        assert flatten_breaks('') == ''
