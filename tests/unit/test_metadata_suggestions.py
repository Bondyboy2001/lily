"""Matching and gap-filling rules for metadata suggestions."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import metadata_suggestions as m  # noqa: E402

pytestmark = pytest.mark.unit


def test_same_book_scores_high_regardless_of_name_order_and_case():
    s = m.match_score("The Left Hand of Darkness", ["Le Guin, Ursula K."],
                      "the left hand of darkness", ["Ursula K. Le Guin"])
    assert s >= m.HIGH_CONFIDENCE


def test_contained_title_is_shown_for_review_but_never_high_confidence():
    # an added subtitle and a sequel look the same to a title comparison
    for other in ("Dune: Deluxe Edition", "Dune Messiah"):
        s = m.match_score("Dune", ["Frank Herbert"], other, ["Frank Herbert"])
        assert m.MIN_SCORE <= s < m.HIGH_CONFIDENCE, other


def test_unrelated_book_scores_below_the_threshold():
    assert m.match_score("Dune", ["Frank Herbert"], "Gardening Basics", ["Jo Green"]) < m.MIN_SCORE


def test_right_title_wrong_author_is_not_high_confidence():
    s = m.match_score("Night Watch", ["Terry Pratchett"], "Night Watch", ["Sergei Lukyanenko"])
    assert m.MIN_SCORE <= s < m.HIGH_CONFIDENCE


def test_no_author_on_the_book_never_reaches_high_confidence():
    assert m.match_score("Dune", [], "Dune", ["Frank Herbert"]) < m.HIGH_CONFIDENCE


def test_empty_or_stopword_only_titles_score_zero():
    assert m.title_similarity("", "Dune") == 0.0
    assert m.title_similarity("The", "of the") == 0.0


def test_fill_only_adds_gaps():
    book = {"description": "", "identifiers": {"isbn": "111"}}
    record = {"description": " A fine book. ", "identifiers": {"ISBN": "222", "OpenLibrary": "OL1W", "goodreads": ""}}
    assert m.fill_fields(book, record) == {"description": "A fine book.", "identifiers": {"openlibrary": "OL1W"}}


def test_fill_never_overwrites_existing_description():
    book = {"description": "Mine", "identifiers": {}}
    assert m.fill_fields(book, {"description": "Theirs", "identifiers": {}}) == {}


def test_nothing_to_add_gives_empty_result():
    assert m.fill_fields({"description": None, "identifiers": {}}, {"description": "", "identifiers": {}}) == {}
