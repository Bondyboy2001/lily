"""cps.series_nav.pick_next: which book follows the current one in its series."""
import pytest

from cps.series_nav import pick_next

pytestmark = pytest.mark.unit


def test_next_is_the_smallest_index_above_the_current_one():
    books = [(1, 1.0), (2, 3.0), (3, 2.0), (4, 2.5)]
    assert pick_next(1, 1.0, books) == 3
    assert pick_next(3, 2.0, books) == 4
    assert pick_next(4, "2.5", books) == 2


def test_last_book_has_no_next():
    assert pick_next(2, 3.0, [(1, 1.0), (2, 3.0)]) is None
    assert pick_next(1, 1.0, []) is None


def test_ties_and_bad_indexes():
    # Two books share the next index: the lower id wins; the current book never counts.
    assert pick_next(1, 1, [(1, 1), (9, 2), (5, 2), (7, None), (8, "x")]) == 5
    # Another book at the same index is not "next".
    assert pick_next(1, 1, [(1, 1), (2, 1)]) is None
    # An unreadable current index gives nothing rather than a guess.
    assert pick_next(1, "abc", [(2, 2)]) is None
