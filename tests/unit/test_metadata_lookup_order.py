"""Which record an automatic lookup applies when the book's title alone can't decide: a book
with no author, and a book whose title an article shares."""
from types import SimpleNamespace

import pytest

from .lily_env import lily_env
from .metadata_fakes import lookup_setup, recording_provider as _provider

pytestmark = pytest.mark.unit

TITLE_PAGE = "Deep Learning\nIan Goodfellow, Yoshua Bengio and Aaron Courville\nThe MIT Press"
BOOK = SimpleNamespace(title="Deep Learning", authors=["Ian Goodfellow", "Yoshua Bengio", "Aaron Courville"])
# The Nature review of the same name, which shares an author
PAPER = SimpleNamespace(title="Deep learning", authors=["Yann LeCun", "Yoshua Bengio", "Geoffrey Hinton"])


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as env:
        yield env


def test_a_book_with_no_author_takes_the_record_whose_author_its_title_page_prints(env, monkeypatch):
    stewart = SimpleNamespace(title="Calculus", authors=["James Stewart"])
    spivak = SimpleNamespace(title="Calculus", authors=["Michael Spivak"])
    helper, applied = lookup_setup(monkeypatch, [_provider("openlibrary", by_text=[spivak, stewart])],
                                   front="CALCULUS\nEarly Transcendentals\nJAMES STEWART")
    book_id = env.add_book("Calculus", author="Unknown", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is True
    assert applied == ["Calculus"]


def test_a_book_with_no_author_and_no_printed_author_is_no_match(env, monkeypatch):
    stewart = SimpleNamespace(title="Calculus", authors=["James Stewart"])
    helper, applied = lookup_setup(monkeypatch, [_provider("openlibrary", by_text=[stewart])])
    book_id = env.add_book("Calculus", author="Unknown", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is False
    assert applied == []


def _book_and_paper(monkeypatch, first):
    calls = []
    applied_records = []
    providers = [_provider("googlescholar", id_types=("doi", "arxiv"), by_text=[PAPER], calls=calls),
                 _provider("google", by_text=[BOOK], calls=calls)]
    helper, __ = lookup_setup(monkeypatch, providers, first=first, front=TITLE_PAGE)
    monkeypatch.setattr(helper, "_apply_record",
                        lambda cdb, book, record, cover, **kw: applied_records.append(record) or True)
    return helper, applied_records


def test_a_book_weighs_google_books_before_the_paper_sources(env, monkeypatch):
    helper, applied = _book_and_paper(monkeypatch, first="")
    book_id = env.add_book("Deep Learning", author="Unknown", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is True
    assert applied == [BOOK]


def test_a_paper_weighs_the_paper_sources_first(env, monkeypatch):
    helper, applied = _book_and_paper(monkeypatch, first="Deep learning\nYann LeCun, Yoshua Bengio\nAbstract\nDeep...")
    book_id = env.add_book("Deep Learning", author="Unknown", fmt="PDF")
    assert helper.fetch_and_apply_metadata(book_id) is True
    assert applied == [PAPER]
