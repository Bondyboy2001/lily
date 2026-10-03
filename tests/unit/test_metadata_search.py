"""Fetch Metadata: what the search box recognises as an identifier, who gets asked,
and arXiv lookups."""

import pytest

from cps.services.identifiers import arxiv_id_from_doi, parse_identifier

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("query, expected", [
    ("978-0-441-17271-9", {"isbn": "9780441172719"}),
    ("10.1038/nature14539", {"doi": "10.1038/nature14539"}),
    ("https://doi.org/10.1038/nature14539", {"doi": "10.1038/nature14539"}),
    ("10.48550/arXiv.1706.03762", {"doi": "10.48550/arXiv.1706.03762"}),
    ("1706.03762", {"arxiv": "1706.03762"}),
    ("arXiv:1706.03762v7", {"arxiv": "1706.03762"}),
    ("https://arxiv.org/abs/1706.03762", {"arxiv": "1706.03762"}),
    ("https://arxiv.org/pdf/1706.03762v2.pdf", {"arxiv": "1706.03762"}),
    ("hep-th/9901001", {"arxiv": "hep-th/9901001"}),
    ("math.AG/0309136", {"arxiv": "math.AG/0309136"}),
    ("hardcover-id:12345", {"hardcover-id": "12345"}),
])
def test_identifier_typed_into_the_search_box_is_recognised(query, expected):
    assert parse_identifier(query) == expected


@pytest.mark.parametrize("query", ["Attention Is All You Need", "Dune Frank Herbert", "", "1984"])
def test_ordinary_text_is_not_an_identifier(query):
    assert parse_identifier(query) == {}


def test_arxiv_doi_names_the_arxiv_id():
    assert arxiv_id_from_doi("10.48550/ARXIV.1706.03762") == "1706.03762"
    assert arxiv_id_from_doi("10.1038/nature14539") == ""


def test_books_identifiers_are_normalised():
    from cps.search_metadata import _form_identifiers
    assert _form_identifiers('{"ISBN": "978-0-441-17271-9", "doi": " 10.1/x ", "asin": ""}') == {
        "isbn": "9780441172719", "doi": "10.1/x"}
    assert _form_identifiers("not json") == {}


def test_typed_identifier_goes_to_the_providers_that_handle_its_type():
    from cps.search_metadata import _providers_to_ask, cl
    ask = {c.__id__ for c in _providers_to_ask({"doi": "10.48550/arXiv.1706.03762"}, cl)}
    assert ask == {"googlescholar"}
    ask = {c.__id__ for c in _providers_to_ask({"isbn": "9780441172719"}, cl)}
    assert ask == {"google", "openlibrary"}


def _scholar_calls(monkeypatch, identifiers):
    from cps.metadata_provider.scholar import google_scholar
    scholar = google_scholar()
    calls = []
    monkeypatch.setattr(scholar, "_search_arxiv", lambda q: calls.append(("arxiv", q)) or [])
    monkeypatch.setattr(scholar, "_search_crossref", lambda q: calls.append(("crossref", q)) or [])
    scholar.search_identifiers(identifiers)
    return calls


def test_arxiv_doi_is_looked_up_on_arxiv_not_crossref(monkeypatch):
    assert _scholar_calls(monkeypatch, {"doi": "10.48550/arXiv.1706.03762"}) == [("arxiv", "1706.03762")]


def test_journal_doi_still_goes_to_crossref(monkeypatch):
    assert _scholar_calls(monkeypatch, {"doi": "10.1038/nature14539"}) == [("crossref", "10.1038/nature14539")]


def test_arxiv_entry_without_journal_doi_gets_arxivs_doi():
    from xml.etree import ElementTree
    from cps.metadata_provider.scholar import google_scholar
    entry = ElementTree.fromstring(
        '<entry xmlns="http://www.w3.org/2005/Atom">'
        "<id>http://arxiv.org/abs/hep-th/9901001v1</id><title>A paper</title>"
        "<author><name>A. Author</name></author></entry>")
    record = google_scholar()._parse_arxiv_entry(entry)
    assert record.identifiers == {"arxiv": "hep-th/9901001", "doi": "10.48550/arXiv.hep-th/9901001"}


def test_a_text_search_asks_every_enabled_provider():
    from cps.search_metadata import _providers_to_ask, cl
    assert _providers_to_ask({}, cl) == list(cl)


def _record(title, authors=()):
    from types import SimpleNamespace
    return SimpleNamespace(title=title, authors=list(authors))


def test_typed_title_ranks_results_when_the_book_title_is_a_filename():
    # A PDF imported as "1706.03762v7" scores every result zero against its own title
    from cps.search_metadata import _scorer
    score = _scorer({"title": "1706.03762v7", "authors": "Unknown"}, "Attention Is All You Need")
    exact = score(_record("Attention Is All You Need"))
    longer = score(_record("Not All Attention Is All You Need"))
    assert exact > longer > score(_record("Deep Residual Learning"))


def test_typed_title_does_not_outrank_the_books_author():
    from cps.search_metadata import _scorer
    score = _scorer({"title": "Dune", "authors": "Frank Herbert"}, "Dune")
    assert score(_record("Dune", ["Frank Herbert"])) > score(_record("Dune", ["Someone Else"]))


def test_exact_title_beats_a_subtitled_one_when_both_hit_the_authorless_cap():
    from cps.search_metadata import _scorer
    title = "Deep Residual Learning for Image Recognition in Very Large Networks"
    score = _scorer({"title": title, "authors": ""}, title)
    assert score(_record(title)) > score(_record(title + ": A Survey"))


def test_a_title_without_the_records_subtitle_still_scores_in_full():
    # Google's and Open Library's titles carry the subtitle; an import accepts the book either way
    from cps.search_metadata import _scorer
    record = _record("Sapiens: A Brief History of Humankind", ["Yuval Noah Harari"])
    record.subtitle = "A Brief History of Humankind"
    for title in ("Sapiens", "Sapiens: A Brief History of Humankind"):
        score = _scorer({"title": title, "authors": "Yuval Noah Harari"}, "")
        assert score(record) == pytest.approx(1.0)


def _file_ids_setup(monkeypatch, page, book=True):
    from types import SimpleNamespace
    from cps import metadata_helper
    import cps
    found = SimpleNamespace(title="1706.03762v7") if book else None
    monkeypatch.setattr(cps.calibre_db, "get_filtered_book", lambda book_id: found, raising=False)
    monkeypatch.setattr(metadata_helper, "pdf_first_page_text", lambda b: page)


def test_fetch_metadata_finds_the_arxiv_id_on_the_pdfs_first_page(monkeypatch):
    from cps.search_metadata import _file_identifiers
    _file_ids_setup(monkeypatch, "Some title\narXiv:2601.22106v1 [stat.ME] 29 Jan 2026")
    assert _file_identifiers("7", "Information-geometry-driven graph")[0] == {"arxiv": "2601.22106"}


def test_fetch_metadata_reads_no_file_for_a_book_it_cannot_see(monkeypatch):
    from cps.search_metadata import _file_identifiers
    _file_ids_setup(monkeypatch, "arXiv:2601.22106", book=False)
    assert _file_identifiers("7", "x") == ({}, "")
    assert _file_identifiers("not a number", "x") == ({}, "")


def test_a_cited_doi_on_the_first_page_is_not_pinned_as_exact():
    from types import SimpleNamespace
    from cps.search_metadata import _pinned
    page = "Mastering the game of Go with deep neural networks\nand tree search doi:10.1038/nature16961"
    paper = SimpleNamespace(title="Mastering the Game of Go with Deep Neural Networks and Tree Search",
                            identifiers={"doi": "10.1038/nature16961"})
    cited = SimpleNamespace(title="A cited dataset", identifiers={"doi": "10.1038/nature16961"})
    file_ids = {"doi": "10.1038/nature16961"}
    assert _pinned(paper, file_ids, {}, page) is True
    assert _pinned(cited, file_ids, {}, page) is False
    # The book's own DOI, typed into its identifiers, is trusted
    assert _pinned(cited, file_ids, {"doi": "10.1038/nature16961"}, page) is True


@pytest.mark.parametrize("given, expected", [
    ("Garcia, Stephan Ramon", "Stephan Ramon Garcia"),
    ("Miller,  Steven J.", "Steven J. Miller"),
    ("Steven J. Miller", "Steven J. Miller"),
    ("King, Jr.", "King, Jr."),
    ("Martin Luther King, Jr.", "Martin Luther King, Jr."),
    ("Smith, John, III", "Smith, John, III"),
    ("", ""),
])
def test_fetched_authors_read_first_name_first(given, expected):
    from cps.search_metadata import natural_author
    assert natural_author(given) == expected
