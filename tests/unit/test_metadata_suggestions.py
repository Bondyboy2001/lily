"""Matching rules for ranking metadata provider results."""

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


def test_contained_title_scores_well_but_never_high_confidence():
    # an added subtitle and a sequel look the same to a title comparison
    for other in ("Dune: Deluxe Edition", "Dune Messiah"):
        s = m.match_score("Dune", ["Frank Herbert"], other, ["Frank Herbert"])
        assert 0.6 <= s < m.HIGH_CONFIDENCE, other


def test_unrelated_book_scores_below_the_threshold():
    assert m.match_score("Dune", ["Frank Herbert"], "Gardening Basics", ["Jo Green"]) < 0.6


def test_right_title_wrong_author_is_not_high_confidence():
    s = m.match_score("Night Watch", ["Terry Pratchett"], "Night Watch", ["Sergei Lukyanenko"])
    assert 0.6 <= s < m.HIGH_CONFIDENCE


def test_no_author_on_the_book_never_reaches_high_confidence():
    assert m.match_score("Dune", [], "Dune", ["Frank Herbert"]) < m.HIGH_CONFIDENCE


def test_empty_or_stopword_only_titles_score_zero():
    assert m.title_similarity("", "Dune") == 0.0
    assert m.title_similarity("The", "of the") == 0.0


def test_calibres_unknown_author_counts_as_no_author():
    assert m.match_score("Dune", ["Unknown"], "Dune", ["Frank Herbert"]) == m.match_score("Dune", [], "Dune", ["Frank Herbert"])
