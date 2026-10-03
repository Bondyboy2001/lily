"""A book imported under its file's name keeps the damage the name did to its title: cut off at
42 characters ("Graph Drawing Algorithms for the Visualiza"), an edition or a series tacked on
("Nanofluidics 2e"), or just the ISBN ("9781118230725.pdf"). Such a book is still found: by the
ISBN in its name or on its copyright page, or by the title as far as it goes when the author or
the book's own pages confirm it."""
from types import SimpleNamespace

import pytest

from .lily_env import lily_env
from .metadata_fakes import lookup_setup as _setup, recording_provider as _provider

pytestmark = pytest.mark.unit

GRAPHS = SimpleNamespace(title="Graph Drawing: Algorithms for the Visualization of Graphs",
                         subtitle="Algorithms for the Visualization of Graphs",
                         authors=["Giuseppe Di Battista", "Peter Eades"], identifiers={"isbn": "9780133016154"})
CUT = "Graph Drawing Algorithms for the Visualiza"

# Pages 2 to 4 of the book: title page and copyright page
FRONT_MATTER = """Graph Drawing
Algorithms for the Visualization of Graphs
Giuseppe Di Battista  Peter Eades
ISBN 0-13-301615-3
© 1999 Prentice Hall"""


def record(title, authors=None, **kw):
    return SimpleNamespace(title=title, authors=authors or [], **kw)


@pytest.mark.parametrize("title, expected", [
    (CUT, True),                                          # 42 characters
    ("Lattice Gauge Theory A Challenge in Large", True),  # the cut fell on a space: 41
    ("Dune", False),
    ("Graph Drawing: Algorithms for the Visualization of Graphs", False),
])
def test_a_title_as_long_as_a_file_name_allows_may_be_cut(title, expected):
    from cps.metadata_helper import cut_short
    assert cut_short(title) is expected


@pytest.mark.parametrize("title, bare", [
    ("Nanofluidics 2e", "Nanofluidics"),
    ("Elements of Quantum Optics 4e", "Elements of Quantum Optics"),
    ("Linear Algebra, 3rd Edition", "Linear Algebra"),
    ("Linear Algebra (Second Edition)", "Linear Algebra"),
    ("The Jet Paradigm: From Microquasars to Quasars (Lecture Notes in Physics)",
     "The Jet Paradigm: From Microquasars to Quasars"),
    ("Harmonic Analysis Method, I (297 Pages)", "Harmonic Analysis Method, I"),
    ("Automorphic Forms on GL(2)", "Automorphic Forms on GL(2)"),  # part of the title
    ("Calculus (Volume 2)", "Calculus (Volume 2)"),                # which book it is
    ("Dynamical Systems (IV)", "Dynamical Systems (IV)"),
    ("Dune", "Dune"),
    ("2e", "2e"),                                                  # nothing would be left
])
def test_what_a_file_name_adds_after_the_title_is_taken_off(title, bare):
    from cps.metadata_helper import bare_title
    assert bare_title(title) == bare


@pytest.mark.parametrize("title, isbn", [
    ("9781118230725.pdf", "9781118230725"),
    ("978-0-7923-0760-0_Book_PrintPDF.pdf", "9780792307600"),
    ("9781118230725", "9781118230725"),
    ("9781118230726.pdf", ""),          # the check digit is wrong
    ("1984", ""),
    ("97811182307251234.pdf", ""),      # a longer number
])
def test_the_isbn_a_file_is_named_by_is_read(title, isbn):
    from cps.metadata_helper import isbn_in_title
    assert isbn_in_title(title) == isbn


@pytest.mark.parametrize("title, query", [
    (CUT, "Graph Drawing Algorithms for the"),           # the last word is half a word
    ("Nanofluidics 2e", "Nanofluidics"),
    ("Dune", "Dune"),
])
def test_the_search_asks_for_the_title_without_the_damage(title, query):
    from cps.metadata_helper import search_title
    assert search_title(title) == query


def test_a_cut_title_is_the_record_it_starts_when_an_author_agrees():
    from cps.metadata_helper import best_metadata_match, loose_metadata_match
    assert best_metadata_match(CUT, ["Battista"], [GRAPHS]) is None
    assert loose_metadata_match(CUT, ["Battista"], [GRAPHS]) is GRAPHS


def test_a_cut_title_with_another_author_or_none_is_not_enough():
    from cps.metadata_helper import loose_metadata_match
    assert loose_metadata_match(CUT, ["Smith"], [GRAPHS]) is None
    assert loose_metadata_match(CUT, [], [GRAPHS]) is None


def test_the_books_own_pages_confirm_a_cut_title_with_no_author():
    from cps.metadata_helper import loose_metadata_match
    assert loose_metadata_match(CUT, [], [GRAPHS], FRONT_MATTER) is GRAPHS
    assert loose_metadata_match(CUT, [], [GRAPHS], "Chapter 1. Planar graphs") is None


def test_a_short_title_is_not_matched_by_how_it_starts():
    from cps.metadata_helper import loose_metadata_match
    assert loose_metadata_match("Dune", ["Herbert"], [record("Dune Messiah", ["Frank Herbert"])]) is None


def test_an_edition_tacked_on_is_ignored_when_an_author_agrees():
    from cps.metadata_helper import loose_metadata_match
    found = record("Nanofluidics", ["Joshua Edel"])
    assert loose_metadata_match("Nanofluidics 2e", ["Edel"], [found]) is found
    assert loose_metadata_match("Nanofluidics 2e", ["Smith"], [found]) is None
    assert loose_metadata_match("Nanofluidics 2e", [], [found]) is None


def test_two_different_books_starting_the_same_are_neither():
    from cps.metadata_helper import loose_metadata_match
    one = record("Mathematical Analysis: Foundations and Advanced Techniques", ["Mariano Giaquinta"])
    two = record("Mathematical Analysis: Foundations and Advances in One Variable", ["Mariano Giaquinta"])
    cut = "Mathematical Analysis Foundations and Adva"
    assert loose_metadata_match(cut, ["Giaquinta"], [one, two]) is None
    assert loose_metadata_match(cut, ["Giaquinta"], [one, one]) is one


# --- the lookup -------------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as env:
        yield env


def test_a_book_with_a_cut_title_is_found_by_how_it_starts(env, monkeypatch):
    calls = []
    helper, applied = _setup(monkeypatch, [_provider("openlibrary", by_text=[GRAPHS], calls=calls)])
    book_id = env.add_book(CUT, author="Battista", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is True
    assert applied == [GRAPHS.title]
    assert calls == [("openlibrary", "text", "Graph Drawing Algorithms for the Battista")]


def test_an_exact_title_from_a_later_provider_beats_a_cut_one_from_an_earlier(env, monkeypatch):
    exact = record(CUT, ["Giuseppe Di Battista"])
    helper, applied = _setup(monkeypatch, [_provider("google", by_text=[GRAPHS]),
                                           _provider("openlibrary", by_text=[exact])])
    book_id = env.add_book(CUT, author="Battista", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is True
    assert applied == [CUT]


def test_a_file_named_by_its_isbn_is_looked_up_by_it(env, monkeypatch):
    calls = []
    found = record("Fundamentals of Physics", ["David Halliday"], identifiers={"isbn": "9781118230725"})
    helper, applied = _setup(monkeypatch, [_provider("google", by_id=[found], calls=calls)])
    book_id = env.add_book("9781118230725.pdf", author="Unknown", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is True
    assert applied == ["Fundamentals of Physics"]
    assert calls == [("google", "ids", {"isbn": "9781118230725"})]


def test_a_record_for_another_isbn_is_not_the_file_named_by_one(env, monkeypatch):
    other = record("Some Other Book", ["Jane Roe"], identifiers={"isbn": "9780441172719"})
    helper, applied = _setup(monkeypatch, [_provider("google", by_id=[other])])
    book_id = env.add_book("9781118230725.pdf", author="Unknown", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is False
    assert applied == []


def test_a_book_no_search_finds_is_looked_up_by_the_isbn_on_its_copyright_page(env, monkeypatch):
    calls = []
    helper, applied = _setup(monkeypatch, [_provider("google", by_id=[GRAPHS], calls=calls)], front=FRONT_MATTER)
    # A title no provider has, and an author saved wrong
    book_id = env.add_book("Graph drawing (scan, good)", author="Tamassia", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is True
    assert applied == [GRAPHS.title]
    assert calls == [("google", "text", "Graph drawing Tamassia"), ("google", "ids", {"isbn": "0133016153"})]


def test_an_isbn_on_the_pages_for_a_title_that_is_not_on_them_is_another_books(env, monkeypatch):
    other = record("Planar Graphs", ["Takao Nishizeki"], identifiers={"isbn": "0133016153"})
    helper, applied = _setup(monkeypatch, [_provider("google", by_id=[other])], front=FRONT_MATTER)
    book_id = env.add_book("Graph drawing (scan, good)", author="Tamassia", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is False
    assert applied == []


# A series' list of its other books, then the book's own copyright page, whose ISBN the PDF's
# text layer garbled (a digit too many); the Basel edition's ISBN under it reads right
SERIES_LIST = """Applied and Numerical Harmonic Analysis
J. M. Cooper: Introduction to Partial Differential Equations with MATLAB
(ISBN 0-81 76-3967-5)
G.T. Herman: Geometry of Digital Spaces (ISBN 0-81 76-3897-0)
An Introduction to Wavelet Analysis  David F. Walnut
ISBN 0-81763-3962-4 (alk. paper)
ISBN 3-7643-3962-4 SPIN 10574019"""


def test_the_isbns_a_series_list_prints_come_after_the_books_own():
    from cps.metadata_helper import isbn_on_pages, isbns_on_pages
    assert isbns_on_pages(SERIES_LIST) == ["3764339624", "0817639675", "0817638970"]
    assert isbn_on_pages(SERIES_LIST) == "3764339624"


def test_a_book_in_a_series_list_is_not_the_book_whose_pages_list_it(env, monkeypatch):
    calls = []
    cooper = record("Introduction to partial differential equations with MATLAB", ["Jeffery Cooper"],
                    identifiers={"isbn": "0817639675"})
    walnut = record("An Introduction to Wavelet Analysis", ["David F. Walnut"], identifiers={"isbn": "3764339624"})
    by_isbn = {"0817639675": cooper, "3764339624": walnut}
    provider = _provider("google", calls=calls)
    provider.search_identifiers = lambda ids, *a: calls.append(("google", "ids", ids)) or [by_isbn[ids["isbn"]]]
    # The book's own ISBN comes first; were it a series list's, Cooper's would be refused
    helper, applied = _setup(monkeypatch, [provider], front=SERIES_LIST)
    book_id = env.add_book("Wavelets (scan)", author="David F. Walnut", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is True
    assert applied == [walnut.title]
    assert ("google", "ids", {"isbn": "3764339624"}) in calls


def test_a_title_on_the_pages_by_another_author_is_not_the_printed_isbns_book():
    from cps.metadata_helper import printed_isbn_is_this_book
    cooper = record("Introduction to partial differential equations with MATLAB", ["Jeffery Cooper"])
    assert not printed_isbn_is_this_book(cooper, "An Introduction to Wavelet Analysis", ["David F. Walnut"],
                                         SERIES_LIST)
    # With no author to go by, or one not on the pages (perhaps saved wrong), the title on them decides
    assert printed_isbn_is_this_book(cooper, "Untitled-1", [], SERIES_LIST)
    assert printed_isbn_is_this_book(cooper, "Wavelets", ["Jane Roe"], SERIES_LIST)


def test_a_book_with_its_own_isbn_and_a_cut_title_is_the_isbns_record(env, monkeypatch):
    import sqlite3
    helper, applied = _setup(monkeypatch, [_provider("google", by_id=[GRAPHS])])
    book_id = env.add_book(CUT, author="Battista", fmt="PDF")
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.execute("INSERT INTO identifiers (book, type, val) VALUES (?, 'isbn', '9780133016154')", (book_id,))
    con.commit()
    con.close()
    assert helper.fetch_and_apply_metadata(book_id) is True
    assert applied == [GRAPHS.title]
