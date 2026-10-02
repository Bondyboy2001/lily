"""Auto metadata fetch and Rebuild metadata only apply a provider result that is exactly the book."""

from types import SimpleNamespace

import pytest

from cps.metadata_helper import best_metadata_match, titles_match

pytestmark = pytest.mark.unit


def record(title, authors=None):
    return SimpleNamespace(title=title, authors=authors or [])


def test_exact_match_accepted():
    result = record("Tide Tables for Beginners", ["Marian Hollis"])
    assert best_metadata_match("Tide Tables for Beginners", ["Marian Hollis"], [result]) is result


@pytest.mark.parametrize("found", [
    "TIDE TABLES FOR BEGINNERS",
    "Tide Tables for Beginners!",
    "Tide  Tables  for Beginners",
    "Tide-Tables for Beginners",
])
def test_only_case_punctuation_and_spacing_are_ignored(found):
    result = record(found, ["Marian Hollis"])
    assert best_metadata_match("Tide Tables for Beginners", ["Marian Hollis"], [result]) is result


@pytest.mark.parametrize("found", [
    "Tide tables and charts",
    "Tide Tables for Beginner",
    "Tide Tables for Beginners: A Practical Guide",
    "The Tide Tables for Beginners",
    "Tide Tables",
])
def test_near_titles_are_not_matches(found):
    assert not titles_match("Tide Tables for Beginners", found)
    assert best_metadata_match("Tide Tables for Beginners", ["Marian Hollis"], [record(found, ["Marian Hollis"])]) is None


def test_accent_and_curly_quote_variants_accepted():
    result = record("Les Misérables", ["Victor Hugo"])
    assert best_metadata_match("Les Miserables", ["Victor Hugo"], [result]) is result
    result = record("Ender’s Game", ["Orson Scott Card"])
    assert best_metadata_match("Ender's Game", ["Card, Orson Scott"], [result]) is result


def test_empty_title_matches_nothing():
    assert not titles_match("", "")
    assert best_metadata_match("", [], [record("")]) is None


def test_author_mismatch_rejected():
    result = record("Tide Tables for Beginners", ["John Smith"])
    assert best_metadata_match("Tide Tables for Beginners", ["Marian Hollis"], [result]) is None


def test_missing_authors_on_result_lets_title_decide():
    result = record("Tide Tables for Beginners")
    assert best_metadata_match("Tide Tables for Beginners", ["Marian Hollis"], [result]) is result


def test_unknown_book_author_lets_title_decide():
    result = record("Tide Tables for Beginners", ["Marian Hollis"])
    assert best_metadata_match("Tide Tables for Beginners", ["Unknown"], [result]) is result


def test_result_naming_the_author_beats_one_naming_none():
    nameless = record("Tide Tables for Beginners")
    other_author = record("Tide Tables for Beginners", ["John Smith"])
    named = record("Tide Tables for Beginners", ["M. Hollis"])
    results = [record("Tide tables and charts", ["Marian Hollis"]), nameless, other_author, named]
    assert best_metadata_match("Tide Tables for Beginners", ["Marian Hollis"], results) is named


def test_no_results():
    assert best_metadata_match("Tide Tables for Beginners", ["Marian Hollis"], []) is None


@pytest.mark.parametrize("book_author, found_author", [
    ("Le Guin, Ursula K.", "Ursula K. Le Guin"),      # the family name's last word, either way round
    ("Ursula K. Le Guin", "Le Guin, Ursula"),
    ("García Márquez, Gabriel", "Gabriel Garcia Marquez"),
])
def test_an_author_is_the_same_surname_first_or_last(book_author, found_author):
    result = record("The Dispossessed", [found_author])
    assert best_metadata_match("The Dispossessed", [book_author], [result]) is result
