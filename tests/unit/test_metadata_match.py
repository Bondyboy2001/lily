"""Auto metadata fetch only applies a provider result that is the same book."""

from types import SimpleNamespace

import pytest

from cps.metadata_helper import TITLE_MATCH_THRESHOLD, best_metadata_match, title_similarity

pytestmark = pytest.mark.unit


def record(title, authors=None):
    return SimpleNamespace(title=title, authors=authors or [])


def test_exact_match_accepted():
    result = record("Tide Tables for Beginners", ["Marian Hollis"])
    assert best_metadata_match("Tide Tables for Beginners", ["Marian Hollis"], [result]) is result


def test_different_book_with_similar_title_rejected():
    result = record("Tide tables and charts", ["Marian Hollis"])
    assert title_similarity("Tide Tables for Beginners", "Tide tables and charts") < TITLE_MATCH_THRESHOLD
    assert best_metadata_match("Tide Tables for Beginners", ["Marian Hollis"], [result]) is None


@pytest.mark.parametrize("found", [
    "Tide Tables for Beginners: A Practical Guide",
    "TIDE TABLES FOR BEGINNERS",
    "The Tide Tables for Beginners",
    "Tide Tables for Beginners!",
])
def test_subtitle_case_article_and_punctuation_variants_accepted(found):
    result = record(found, ["Marian Hollis"])
    assert best_metadata_match("Tide Tables for Beginners", ["Marian Hollis"], [result]) is result


def test_accent_and_curly_quote_variants_accepted():
    result = record("Les Misérables", ["Victor Hugo"])
    assert best_metadata_match("Les Miserables", ["Victor Hugo"], [result]) is result
    result = record("Ender’s Game", ["Orson Scott Card"])
    assert best_metadata_match("Ender's Game", ["Card, Orson Scott"], [result]) is result


def test_author_mismatch_rejected():
    result = record("Tide Tables for Beginners", ["John Smith"])
    assert best_metadata_match("Tide Tables for Beginners", ["Marian Hollis"], [result]) is None


def test_missing_authors_on_result_lets_title_decide():
    result = record("Tide Tables for Beginners")
    assert best_metadata_match("Tide Tables for Beginners", ["Marian Hollis"], [result]) is result
    assert best_metadata_match("Tide Tables for Beginners", ["Marian Hollis"], [record("Tide tables and charts")]) is None


def test_unknown_book_author_lets_title_decide():
    result = record("Tide Tables for Beginners", ["Marian Hollis"])
    assert best_metadata_match("Tide Tables for Beginners", ["Unknown"], [result]) is result


def test_best_of_several_results_chosen():
    wrong = record("Tide tables and charts", ["Marian Hollis"])
    close = record("Tide Tables for Beginner", ["Marian Hollis"])
    exact = record("Tide Tables for Beginners", ["M. Hollis"])
    other_author = record("Tide Tables for Beginners", ["John Smith"])
    results = [wrong, other_author, close, exact]
    assert best_metadata_match("Tide Tables for Beginners", ["Marian Hollis"], results) is exact


def test_no_results():
    assert best_metadata_match("Tide Tables for Beginners", ["Marian Hollis"], []) is None
