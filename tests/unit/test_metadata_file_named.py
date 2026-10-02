"""A book whose title is the file it was typeset from ("427551_Print.indd", as calibre reads
it from a Springer PDF's own details) is looked up by the ISBN on its copyright page, and is
the record whose title is on its first pages."""
from types import SimpleNamespace

import pytest

from .lily_env import lily_env
from .metadata_fakes import lookup_setup, recording_provider as _provider

pytestmark = pytest.mark.unit

# Pages 2 to 4 of a Springer book: series page, title page, copyright page
FRONT_MATTER = """Universitext
Series editors Sheldon Axler San Francisco State University
Alexandru Dimca
Hyperplane Arrangements
An Introduction
Alexandru Dimca Université Côte d'Azur Nice, France
ISSN 0172-5939 ISSN 2191-6675 (electronic)
ISBN 978-3-319-56220-9 ISBN 978-3-319-56221-6 (eBook)
DOI 10.1007/978-3-319-56221-6
© Springer International Publishing AG 2017"""

DIMCA = SimpleNamespace(title="Hyperplane Arrangements: An Introduction", subtitle="An Introduction",
                        authors=["Alexandru Dimca"], identifiers={"isbn": "9783319562209"})


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as env:
        yield env


def _setup(monkeypatch, providers, front=FRONT_MATTER):
    # The cover is an image: no text on the first page
    return lookup_setup(monkeypatch, providers, front=front)


@pytest.mark.parametrize("title, expected", [
    ("427551_Print.indd", True),
    ("13577JC Printciples_1781#150.indd", True),
    ("Microsoft Word - thesis.docx", True),
    ("chapter3.tex", True),
    ("Dune", False),
    ("Node.js in Action", False),
    ("1706.03762v7", False),
])
def test_a_title_that_is_a_file_name_is_known(title, expected):
    from cps.metadata_helper import named_by_file
    assert named_by_file(title) is expected


@pytest.mark.parametrize("name, expected", [
    ("0000253", True), ("Unknown", True), ("", True), ("Alexandru Dimca", False), ("leo", False),
])
def test_an_author_with_no_letters_is_a_placeholder(name, expected):
    from cps.metadata_helper import placeholder_author
    assert placeholder_author(name) is expected


@pytest.mark.parametrize("text, expected", [
    (FRONT_MATTER, "9783319562209"),
    ("ISBN-10: 0-387-95385-X", "038795385X"),
    # A garbled digit fails the check, and the next ISBN is taken
    ("ISBN 978-3-319-56220-8 ISBN 978-3-319-56221-6 (eBook)", "9783319562216"),
    ("ISSN 0172-5939", ""),
])
def test_the_isbn_printed_on_the_pages_is_read(text, expected):
    from cps.metadata_helper import isbn_on_pages
    assert isbn_on_pages(text) == expected


def test_a_book_named_by_its_file_is_found_by_the_isbn_on_its_copyright_page(env, monkeypatch):
    calls = []
    helper, applied = _setup(monkeypatch, [_provider("google", {"isbn"}, by_id=[DIMCA], calls=calls)])
    book_id = env.add_book("427551_Print.indd", author="0000253", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is True
    assert applied == ["Hyperplane Arrangements: An Introduction"]
    assert calls == [("google", "ids", {"isbn": "9783319562209"})]


def test_a_record_whose_title_is_not_on_the_pages_is_not_this_book(env, monkeypatch):
    other = SimpleNamespace(title="Commutative Algebra", authors=["Alexandru Dimca"], identifiers={})
    helper, applied = _setup(monkeypatch, [_provider("google", {"isbn"}, by_id=[other])])
    book_id = env.add_book("427551_Print.indd", author="0000253", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is False
    assert applied == []


def test_a_book_named_by_its_file_is_not_searched_for_by_that_name(env, monkeypatch):
    calls = []
    helper, applied = _setup(monkeypatch, [_provider("google", {"isbn"}, calls=calls)], front="")
    book_id = env.add_book("427551_Print.indd", author="0000253", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is False
    assert calls == []


def test_an_ordinary_title_is_still_searched_without_reading_more_pages(env, monkeypatch):
    calls = []
    dune = SimpleNamespace(title="Dune", authors=["Frank Herbert"])
    helper, applied = _setup(monkeypatch, [_provider("google", {"isbn"}, by_text=[dune], calls=calls)])
    monkeypatch.setattr(helper, "pdf_front_matter_text", lambda book: pytest.fail("read more pages"))
    book_id = env.add_book("Dune", author="Frank Herbert", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is True
    assert calls == [("google", "text", "Dune Frank Herbert")]


def test_a_numeric_author_is_left_out_of_the_search(env, monkeypatch):
    calls = []
    dune = SimpleNamespace(title="Dune", authors=["Frank Herbert"])
    helper, applied = _setup(monkeypatch, [_provider("google", {"isbn"}, by_text=[dune], calls=calls)])
    book_id = env.add_book("Dune", author="0000253", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is True
    assert calls == [("google", "text", "Dune")]
