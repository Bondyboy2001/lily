"""A lookup tells a book by its cover when its title and authors can't: the comparison, and
how matching uses it."""
import random
from types import SimpleNamespace

import pytest

from cps import cover_match
from cps.metadata_helper import best_metadata_match, loose_metadata_match

pytestmark = pytest.mark.unit

N = cover_match.SIZE * cover_match.SIZE


def _picture(seed):
    """A detailed 32x32 grey picture."""
    rng = random.Random(seed)
    return [rng.randrange(256) for _ in range(N)]


def _copy(pixels, seed=99):
    """The same picture as another scan or compression would give it: lighter, a little noisy."""
    rng = random.Random(seed)
    return [min(255, max(0, int(p * 0.9 + 20 + rng.randrange(-6, 7)))) for p in pixels]


def test_a_cover_and_a_copy_of_it_are_the_same_and_another_is_not():
    cover = cover_match.from_pixels(_picture(1))
    assert cover_match.same_cover(cover, cover_match.from_pixels(_copy(_picture(1))))
    assert not cover_match.same_cover(cover, cover_match.from_pixels(_picture(2)))


def test_a_picture_with_little_detail_says_nothing():
    # A page of text: white with a few grey marks
    page = [250] * N
    for i in range(0, N, 37):
        page[i] = 200
    assert cover_match.from_pixels(page) is None
    assert not cover_match.same_cover(None, cover_match.from_pixels(_picture(1)))


def test_a_picture_that_cannot_be_read_or_fetched_is_none():
    assert cover_match.thumbnail(b"not an image") is None
    assert cover_match.remote_thumbnail("") is None
    assert cover_match.remote_thumbnail("file:///etc/passwd") is None


def _covers(monkeypatch, by_url):
    monkeypatch.setattr(cover_match, "remote_thumbnail",
                        lambda url: cover_match.from_pixels(by_url[url]) if url in by_url else None)


def _record(title, authors, cover):
    return SimpleNamespace(title=title, authors=authors, cover=cover)


def test_a_book_with_no_author_is_matched_by_its_cover(monkeypatch):
    _covers(monkeypatch, {"https://c/spivak.jpg": _copy(_picture(1)), "https://c/stewart.jpg": _picture(2)})
    mine = cover_match.from_pixels(_picture(1))
    spivak = _record("Calculus", ["Michael Spivak"], "https://c/spivak.jpg")
    stewart = _record("Calculus", ["James Stewart"], "https://c/stewart.jpg")
    assert best_metadata_match("Calculus", [], [stewart, spivak], "", lambda: mine) is spivak
    # Without a cover of its own, nothing
    assert best_metadata_match("Calculus", [], [stewart, spivak], "", lambda: None) is None


def test_of_several_editions_the_one_with_the_books_cover_wins(monkeypatch):
    _covers(monkeypatch, {"https://c/1e.jpg": _picture(3), "https://c/2e.jpg": _copy(_picture(4))})
    mine = cover_match.from_pixels(_picture(4))
    first = _record("Dune", ["Frank Herbert"], "https://c/1e.jpg")
    second = _record("Dune", ["Frank Herbert"], "https://c/2e.jpg")
    assert best_metadata_match("Dune", ["Frank Herbert"], [first, second], "", lambda: mine) is second
    # No cover to weigh: the first, as before
    assert best_metadata_match("Dune", ["Frank Herbert"], [first, second]) is first


def test_a_single_match_downloads_no_cover(monkeypatch):
    monkeypatch.setattr(cover_match, "remote_thumbnail", lambda url: pytest.fail("downloaded " + url))
    dune = _record("Dune", ["Frank Herbert"], "https://c/dune.jpg")
    assert best_metadata_match("Dune", ["Frank Herbert"], [dune], "", lambda: pytest.fail("shrunk")) is dune


def test_a_title_cut_short_is_confirmed_by_its_cover(monkeypatch):
    _covers(monkeypatch, {"https://c/graphs.jpg": _copy(_picture(5))})
    mine = cover_match.from_pixels(_picture(5))
    cut = "Graph Drawing Algorithms for the Visualiza"
    graphs = _record("Graph Drawing: Algorithms for the Visualization of Graphs", ["Giuseppe Di Battista"],
                     "https://c/graphs.jpg")
    graphs.title = "Graph Drawing Algorithms for the Visualization of Graphs"
    assert loose_metadata_match(cut, [], [graphs], "", lambda: None) is None
    assert loose_metadata_match(cut, [], [graphs], "", lambda: mine) is graphs
