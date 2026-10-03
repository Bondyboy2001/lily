# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Unit tests for cps/helper.py

Tests cover pure Python utility functions that don't require Docker:
- Filename sanitization (get_valid_filename)
- Author name parsing (split_authors, get_sorted_author)
- Password validation (valid_password)
- Email and username validation (valid_email, check_email, check_username)
- List de-duplication (uniq) and readable-format detection (check_read_formats)

Note: Functions involving database queries, file I/O, or external services
are tested in integration tests instead.
"""

import pytest
from unittest.mock import Mock, patch
from types import SimpleNamespace

# Import config for accessing in tests
from cps import config

# Import functions from helper.py
from cps.helper import (
    get_valid_filename,
    split_authors,
    get_sorted_author,
    check_email,
    check_username,
    check_read_formats,
    valid_email,
    valid_password,
    uniq,
)


# ============================================================================
# Tests for get_valid_filename()
# ============================================================================

class TestGetValidFilename:
    """Test filename sanitization logic"""

    @patch('cps.helper.config')
    def test_basic_valid_filename(self, mock_config):
        """Test basic valid filename passes through"""
        # Mock the config attribute used by get_valid_filename
        mock_config.config_unicode_filename = False

        result = get_valid_filename("My Book Title")
        # Plain spaces are preserved, not replaced with underscores
        assert result == "My Book Title"

    @patch('cps.helper.config')
    def test_replace_whitespace_switches_the_special_character_swaps(self, mock_config):
        """Despite its name, replace_whitespace turns the | and ? swaps on and off; spaces always stay"""
        mock_config.config_unicode_filename = False

        assert get_valid_filename("a|b x?y  z") == "a,b x_y  z"
        assert get_valid_filename("a|b x?y  z", replace_whitespace=False) == "a|b x?y  z"

    @patch('cps.helper.config')
    def test_special_characters_sanitized(self, mock_config):
        """Test special characters are replaced"""
        mock_config.config_unicode_filename = False

        result = get_valid_filename('Test<>:"/\\|?*Book')
        # Dangerous characters should be replaced
        for char in '<>:"/\\|?*':
            assert char not in result
        assert "Test" in result
        assert "Book" in result

    @patch('cps.helper.config')
    def test_trailing_dot_removed(self, mock_config):
        """Test trailing dot is replaced with underscore"""
        mock_config.config_unicode_filename = False

        result = get_valid_filename("Test.")
        assert not result.endswith(".")
        assert result.endswith("_")

    @patch('cps.helper.config')
    def test_max_length_truncation(self, mock_config):
        """Test filename truncation to max chars"""
        mock_config.config_unicode_filename = False

        long_name = "A" * 200
        result = get_valid_filename(long_name, chars=50)
        assert len(result.encode('utf-8')) <= 50

    @patch('cps.helper.config')
    def test_unicode_handling(self, mock_config):
        """Test unicode characters in filename"""
        mock_config.config_unicode_filename = True

        # config_unicode_filename transliterates to ASCII; off, the letters are kept
        assert get_valid_filename("Test äöüß Book") == "Test aouss Book"
        mock_config.config_unicode_filename = False
        assert get_valid_filename("Test äöüß Book") == "Test äöüß Book"

    @patch('cps.helper.config')
    def test_null_bytes_removed(self, mock_config):
        """Test null bytes are stripped from edges"""
        mock_config.config_unicode_filename = False

        result = get_valid_filename("Test\x00Book")
        # .strip('\0') only removes from start/end, not middle
        # So null byte in middle will remain
        # Let's test edge stripping instead
        result2 = get_valid_filename("\x00TestBook\x00")
        assert "\x00" not in result2
        assert result2 == "TestBook"

    @patch('cps.helper.config')
    def test_slash_and_colon_replaced(self, mock_config):
        """Test path separators are replaced"""
        mock_config.config_unicode_filename = False

        result = get_valid_filename("Test/Book:Title")
        assert "/" not in result
        assert ":" not in result
        assert "_" in result

    @patch('cps.helper.config')
    def test_empty_string_raises_error(self, mock_config):
        """Test empty filename raises ValueError"""
        mock_config.config_unicode_filename = False

        with pytest.raises(ValueError, match="Filename cannot be empty"):
            get_valid_filename("")

    @patch('cps.helper.config')
    def test_none_value_handled(self, mock_config):
        """Test None value converted to empty string and raises ValueError"""
        mock_config.config_unicode_filename = False

        # Production code converts None to "" which then raises ValueError
        with pytest.raises(ValueError, match="Filename cannot be empty"):
            get_valid_filename(None)

    @patch('cps.helper.config')
    def test_integer_value_converted(self, mock_config):
        """Test integer values are converted to string"""
        mock_config.config_unicode_filename = False
        result = get_valid_filename(12345)
        assert "12345" in result

    @patch('cps.helper.config')
    def test_pipe_replaced_with_comma(self, mock_config):
        """Test pipe character replaced with comma"""
        mock_config.config_unicode_filename = False

        result = get_valid_filename("Author1|Author2|Author3")
        assert "|" not in result
        assert "," in result  # Pipes become commas


# ============================================================================
# Tests for split_authors()
# ============================================================================

class TestSplitAuthors:
    """Test author name splitting logic"""

    def test_single_author_no_delimiter(self):
        """Test single author without delimiter"""
        result = split_authors(["John Doe"])
        assert result == ["John Doe"]

    def test_ampersand_delimiter(self):
        """Test authors split by ampersand"""
        result = split_authors(["John Doe & Jane Smith"])
        assert len(result) == 2
        assert "John Doe" in result
        assert "Jane Smith" in result

    def test_semicolon_delimiter(self):
        """Test authors split by semicolon"""
        result = split_authors(["John Doe;Jane Smith"])
        assert len(result) == 2
        assert "John Doe" in result
        assert "Jane Smith" in result

    def test_lastname_firstname_format(self):
        """Test 'Lastname, Firstname' format is reversed"""
        result = split_authors(["Doe, John"])
        assert result == ["John Doe"]

    def test_multiple_commas_preserved(self):
        """Test names with multiple commas are split"""
        result = split_authors(["Doe, John, Jr."])
        # Multiple commas should result in split
        assert len(result) >= 2

    def test_whitespace_stripped(self):
        """Test whitespace is stripped from author names"""
        result = split_authors(["  John Doe  &  Jane Smith  "])
        assert "John Doe" in result
        assert "Jane Smith" in result
        # No leading/trailing whitespace
        for author in result:
            assert author == author.strip()

    def test_mixed_delimiters(self):
        """Test mixed delimiters in same string"""
        result = split_authors(["John Doe & Jane Smith;Bob Jones"])
        assert len(result) == 3
        assert "John Doe" in result
        assert "Jane Smith" in result
        assert "Bob Jones" in result

    def test_empty_list_returns_empty(self):
        """Test empty list returns empty list"""
        result = split_authors([])
        assert result == []

    def test_multiple_input_values(self):
        """Test multiple input values are processed"""
        result = split_authors(["John Doe", "Jane Smith & Bob Jones"])
        assert len(result) == 3
        assert "John Doe" in result
        assert "Jane Smith" in result
        assert "Bob Jones" in result


# ============================================================================
# Tests for get_sorted_author()
# ============================================================================

class TestGetSortedAuthor:
    """Test author name sorting logic"""

    def test_single_name_unchanged(self):
        """Test single name is unchanged"""
        result = get_sorted_author("Aristotle")
        assert result == "Aristotle"

    def test_first_last_sorted(self):
        """Test 'First Last' becomes 'Last, First'"""
        result = get_sorted_author("John Doe")
        assert result == "Doe, John"

    def test_jr_suffix_preserved(self):
        """Test Jr. suffix is preserved correctly"""
        result = get_sorted_author("John Doe Jr.")
        assert "Jr." in result
        assert "Doe" in result

    def test_sr_suffix_preserved(self):
        """Test Sr. suffix is preserved correctly"""
        result = get_sorted_author("John Doe SR")
        assert "SR" in result or "Sr" in result
        assert "Doe" in result

    def test_roman_numeral_suffix_preserved(self):
        """Test Roman numeral suffixes (I, II, III, IV)"""
        result = get_sorted_author("John Doe III")
        assert "III" in result
        assert "Doe" in result

    def test_already_sorted_unchanged(self):
        """Test 'Last, First' format is preserved"""
        result = get_sorted_author("Doe, John")
        assert result == "Doe, John"

    def test_three_part_name_sorted(self):
        """Test 'First Middle Last' becomes 'Last, First Middle'"""
        result = get_sorted_author("John William Doe")
        assert result == "Doe, John William"

    def test_empty_name_stays_empty(self):
        assert get_sorted_author("") == ""


# ============================================================================
# Tests for valid_email()
# ============================================================================

class TestValidEmail:
    """Test email validation logic"""

    def test_valid_single_email(self):
        """Test valid single email passes"""
        result = valid_email("test@example.com")
        assert result == "test@example.com"

    def test_valid_multiple_emails(self):
        """Test multiple comma-separated emails"""
        result = valid_email("test1@example.com,test2@example.com")
        assert "test1@example.com" in result
        assert "test2@example.com" in result

    def test_invalid_email_format_raises(self):
        """Test invalid email format raises exception"""
        with pytest.raises(Exception, match="Invalid Email address format"):
            valid_email("not_an_email")

    def test_whitespace_stripped(self):
        """Test whitespace is stripped from emails"""
        result = valid_email("  test@example.com  ")
        assert result == "test@example.com"

    def test_multiple_with_whitespace(self):
        """Test multiple emails with whitespace"""
        result = valid_email(" test1@example.com , test2@example.com ")
        assert "test1@example.com" in result
        assert "test2@example.com" in result

    def test_empty_string_returns_empty(self):
        """Test empty string returns empty string"""
        result = valid_email("")
        assert result == ""

    def test_invalid_domain_raises(self):
        """Test invalid domain raises exception"""
        with pytest.raises(Exception, match="Invalid Email address format"):
            valid_email("test@")

    def test_missing_at_symbol_raises(self):
        """Test missing @ symbol raises exception"""
        with pytest.raises(Exception, match="Invalid Email address format"):
            valid_email("testexample.com")

    def test_special_chars_in_local_part(self):
        """Test special characters allowed in local part"""
        result = valid_email("test.name+tag@example.com")
        assert result == "test.name+tag@example.com"


# ============================================================================
# Tests for valid_password()
# ============================================================================

POLICY_FLAGS = ("config_password_number", "config_password_lower", "config_password_upper",
                "config_password_character", "config_password_special")


@pytest.fixture
def password_policy(monkeypatch):
    """Turns the policy on with every rule off; each test enables the rule it checks.

    raising=False because unit tests run without a loaded settings table."""
    import cps.helper
    monkeypatch.setattr(cps.helper, "_", lambda message, **kwargs: message)  # Babel isn't initialised here
    monkeypatch.setattr(config, "config_password_policy", True, raising=False)
    monkeypatch.setattr(config, "config_password_min_length", 0, raising=False)
    for flag in POLICY_FLAGS:
        monkeypatch.setattr(config, flag, False, raising=False)
    return lambda **rules: [monkeypatch.setattr(config, k, v) for k, v in rules.items()]


class TestValidPassword:
    """Test password validation logic"""

    def test_no_policy_allows_any_password(self, password_policy, monkeypatch):
        monkeypatch.setattr(config, "config_password_policy", False)
        monkeypatch.setattr(config, "config_password_min_length", 99)
        assert valid_password("abc") == "abc"

    @pytest.mark.parametrize("rule, good, bad", [
        ({"config_password_min_length": 8}, "abcdefgh", "abc"),
        ({"config_password_number": True}, "abc123", "abcdef"),
        ({"config_password_lower": True}, "ABCabc", "ABC123"),
        ({"config_password_upper": True}, "abcABC", "abc123"),
        ({"config_password_character": True}, "123abc", "123456"),
        ({"config_password_special": True}, "abc@123", "abc123"),
    ], ids=["min-length", "number", "lowercase", "uppercase", "letter", "special"])
    def test_each_rule_is_enforced(self, password_policy, rule, good, bad):
        password_policy(**rule)
        assert valid_password(good) == good
        with pytest.raises(Exception, match="Password doesn't comply"):
            valid_password(bad)

    def test_rules_combine(self, password_policy):
        password_policy(config_password_min_length=8, config_password_number=True,
                        config_password_upper=True, config_password_special=True)
        assert valid_password("Tulips!2026") == "Tulips!2026"
        for bad in ("Tul!2", "tulips!2026", "Tulips!here", "Tulips2026"):
            with pytest.raises(Exception, match="Password doesn't comply"):
                valid_password(bad)

    def test_unicode_letters_count_as_cased(self, password_policy):
        password_policy(config_password_lower=True, config_password_upper=True)
        assert valid_password("Ångström") == "Ångström"


# ============================================================================
# Tests for check_email() and check_username()
# ============================================================================

class TestCheckEmailAndUsername:
    """Test email and username uniqueness checks"""

    @patch('cps.ub.session')
    def test_check_email_unique_passes(self, mock_session):
        """Test unique email passes check"""
        mock_session.query().filter().first.return_value = None
        result = check_email("new@example.com")
        assert result == "new@example.com"

    @patch('cps.ub.session')
    def test_check_email_duplicate_raises(self, mock_session):
        """Test duplicate email raises exception"""
        mock_session.query().filter().first.return_value = Mock()
        with pytest.raises(Exception, match="Found an existing account"):
            check_email("existing@example.com")

    @patch('cps.ub.session')
    def test_check_username_unique_passes(self, mock_session):
        """Test unique username passes check"""
        mock_session.query().filter().scalar.return_value = None
        result = check_username("newuser")
        assert result == "newuser"

    @patch('cps.ub.session')
    def test_check_username_duplicate_raises(self, mock_session):
        """Test duplicate username raises exception"""
        mock_session.query().filter().scalar.return_value = True
        with pytest.raises(Exception, match="This username is already taken"):
            check_username("existinguser")

    @patch('cps.ub.session')
    def test_check_username_strips_whitespace(self, mock_session):
        """Test username whitespace is stripped"""
        mock_session.query().filter().scalar.return_value = None
        result = check_username("  newuser  ")
        assert result == "newuser"


# ============================================================================
# Tests for uniq()
# ============================================================================

class TestUniq:
    """Test unique list function"""

    def test_removes_duplicates(self):
        """Test duplicate items are removed"""
        result = uniq(["a", "b", "a", "c", "b"])
        assert len(result) == 3
        assert "a" in result
        assert "b" in result
        assert "c" in result

    def test_preserves_order(self):
        """Test first occurrence order is preserved"""
        result = uniq(["c", "a", "b", "a"])
        # First occurrence of each should be preserved
        assert result.index("c") < result.index("a")
        assert result.index("a") < result.index("b")

    def test_normalizes_whitespace(self):
        """Test multiple spaces are normalized"""
        result = uniq(["a  b", "a b", "c"])
        # "a  b" and "a b" should be treated as same
        assert len(result) == 2

    def test_empty_list_returns_empty(self):
        """Test empty list returns empty list"""
        result = uniq([])
        assert result == []

    def test_single_item_unchanged(self):
        """Test single item list is unchanged"""
        result = uniq(["only"])
        assert result == ["only"]


class TestCheckReadFormats:
    @staticmethod
    def _entry(*formats):
        return SimpleNamespace(data=[SimpleNamespace(format=f) for f in formats])

    def test_unsupported_formats_yield_no_reader(self):
        for fmt in ("TXT", "MOBI", "CBZ", "HTML", "AZW3", "FB2", "CBR", "KEPUB"):
            assert check_read_formats(self._entry(fmt)) == [], fmt
        assert check_read_formats(SimpleNamespace(data=[])) == []

    def test_document_and_audio_formats_are_supported(self):
        for fmt in ("EPUB", "PDF", "DJVU", "DJV", "MP3", "M4B", "FLAC"):
            assert check_read_formats(self._entry(fmt)) == [fmt.lower()], fmt

    def test_reader_preference_orders_the_list(self):
        entry = self._entry("TXT", "M4B", "PDF", "MP3", "EPUB", "MOBI")
        assert check_read_formats(entry) == ["epub", "pdf", "m4b", "mp3"]


# ============================================================================
# Test Markers
# ============================================================================

# Mark all tests in this module as unit tests
pytestmark = pytest.mark.unit
