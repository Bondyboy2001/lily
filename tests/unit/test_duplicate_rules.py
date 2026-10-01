"""Pure duplicate rules: hashing, title normalisation and which book to keep."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def rules():
    from cps import duplicate_rules
    return duplicate_rules


def _book(ts=None, formats=(), tags=(), size=0, comment=None):
    data = [SimpleNamespace(format=f, uncompressed_size=size) for f in formats]
    comments = [SimpleNamespace(text=comment)] if comment else []
    return SimpleNamespace(timestamp=ts, data=data, tags=list(tags), series=None, ratings=[],
                           comments=comments, publishers=[], pubdate=None, identifiers=[])


def test_group_hash_ignores_case_and_padding_and_has_defaults(rules):
    assert rules.generate_group_hash("  Dune ", "FRANK Herbert") == rules.generate_group_hash("dune", "frank herbert")
    assert rules.generate_group_hash(None, None) == rules.generate_group_hash("untitled", "unknown")
    assert len(rules.generate_group_hash("a", "b")) == 32
    assert rules.generate_group_hash("a", "b") != rules.generate_group_hash("a", "c")


def test_title_normalisation_strips_a_leading_author_prefix(rules):
    assert rules.normalize_title_for_duplicates("Homer, The Iliad", "Homer") == "the iliad"
    assert rules.normalize_title_for_duplicates("The Iliad", "Homer") == "the iliad"
    assert rules.normalize_title_for_duplicates(None) == "untitled"


def test_strategy_validation(rules):
    assert rules.validate_resolution_strategy("newest") and rules.validate_resolution_strategy("merge")
    assert not rules.validate_resolution_strategy("random") and not rules.validate_resolution_strategy(None)


def test_newest_oldest_and_empty(rules):
    old = _book(datetime(2020, 1, 1))
    new = _book(datetime(2024, 1, 1, tzinfo=timezone.utc))  # mixing naive and aware must not raise
    assert rules.select_book_to_keep([old, new], "newest") is new
    assert rules.select_book_to_keep([old, new], "oldest") is old
    assert rules.select_book_to_keep([old, new], "merge") is new
    assert rules.select_book_to_keep([], "newest") is None
    assert rules.select_book_to_keep([old, new], "no-such-strategy") is new


def test_largest_file_size_and_most_metadata(rules):
    small = _book(datetime(2024, 1, 1), formats=["EPUB"], size=10)
    big = _book(datetime(2020, 1, 1), formats=["EPUB"], size=999)
    assert rules.select_book_to_keep([small, big], "largest_file_size") is big
    plain = _book(datetime(2024, 1, 1))
    rich = _book(datetime(2020, 1, 1), tags=["a", "b"], comment="x" * 80)
    assert rules.select_book_to_keep([plain, rich], "most_metadata") is rich


def test_highest_quality_format_uses_configured_priority_with_default_fallback(rules, monkeypatch):
    class _Boom:
        def __init__(self):
            raise RuntimeError("no settings db")

    monkeypatch.setattr(rules, "CWA_DB", _Boom)  # falls back to the built-in priority table
    pdf = _book(datetime(2024, 1, 1), formats=["PDF"])
    epub = _book(datetime(2020, 1, 1), formats=["EPUB"])
    assert rules.select_book_to_keep([pdf, epub], "highest_quality_format") is epub

    class _Settings:
        def __init__(self):
            self.cwa_settings = {"duplicate_format_priority": '{"PDF": 500, "EPUB": 1}'}

    monkeypatch.setattr(rules, "CWA_DB", _Settings)
    assert rules.select_book_to_keep([pdf, epub], "highest_quality_format") is pdf
