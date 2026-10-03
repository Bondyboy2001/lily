"""Tags keep to subjects: imports, lookups and Rebuild metadata drop PDF keyword junk."""
import sqlite3
from types import SimpleNamespace

import pytest

from cps.tag_cleanup import clean_tags, is_junk_tag

from .lily_env import lily_env
from .metadata_fakes import FakeProvider

# Real tags calibre made from PDF Keywords fields
JUNK = [
    "#", "# Hardcover: 308 pages", "# ISBN-10 / ASIN:", "# ISBN-10: 0521849039", "•ISBN-13 / EAN:",
    "ISBN-13:", "9780511629563", "0521693187", "978-0-472-11935-6", "013143747X", "B0000CJ4BU",
    "Springer 2011", "Springer", "Cambridge University Press;2011", "Basic Books;2011",
    "Nova Science Publishers; Inc", "World Scientific Publishing Company", "Dover Publications",
    "The MIT Press", "American Mathematical Society", "Chapman and Hall/CRC", "Birkhäuser Basel",
    "London Mathematical Society Lecture Note Series (No. 238)", "ACS SYMPOSIUM SERIES 514",
    "Oxford Graduate Texts in Mathematics", "THIRD EDITION", "Version 1.0", "Vol 56",
    "Compiled by MP@GP", "Uploaded By Dr Yasser S. El-Sayed", "Team DDU", "tanas.olesya (avax)",
    "(for softarchive)", "scanned to PDF by gnv64", "Knowledge iS POWER", "EBC Converted",
    "TeX output 2010.11.25:1539", "STM LaTeX and Word Typesetting", "gnuplot plot", "Keypagebackref",
    "http://archive.org/details/antennasinmatter00rono", "editorial@devaland.com",
    "cseweb.ucsd.edu/~gill/BWLectSite", "9780470409725.pdf", "Language: English", "Publisher:",
    "# Publication Date: 2008-10-01", "WCN: 02-200-203", "62H12", "6.25x9.25", "1999", "None",
    "Image", "Front Matter", "fm", "Table of Contents", "General", "HC", "New Subject", "A Primer",
    "Economics; Finance; Business & Industry", "SIAM Rev. 1970.12:1-63",
    "<p>Suitable for upper-level undergraduates; this accessible approach to set theory",
    "applications to specific problems are taken up only to illustrate a principle",
    "Спизжено у http://avaxhome.ws/blogs/exlib/",
    "Cambridge University Press 2005", "AMS 2005", "New Age International 2009", "Publication Date",
    "1991 Mathematics Subject Classification",
]
SUBJECTS = [
    "Mathematics", "linear algebra", "Linear Logic", "List Edge Colorings", "Quantum Mechanics",
    "Technology & Engineering", "Business & Economics", "cs.LG", "math.AG", "c++", "R",
    "l1 penalized likelihood", "Homogenization (Differential equations)", "Gauge fields (Physics)",
    "Reproducing kernel Hilbert space (RKHS)", "Hammersley–Clifford", "Multivariate analysis -- Bibliography",
    "Kendall's tau correlation matrix", "Space–Time", "poincaré bundle", "Literature",
    "Computers and Society", "Distributed, Parallel, and Cluster Computing", "High Energy Physics - Theory",
    # Subjects with numbers or publishing words in them, and a reader's own tag
    "20th Century", "19th-century fiction", "World War 2", "Python 3", "Windows 10", "H2O", "MP3",
    "History, 1914-1918", "To Read 2024", "Publishing", "Electronic publishing", "Government publications",
    "Association football", "Press freedom",
]


@pytest.mark.unit
@pytest.mark.parametrize("name", JUNK)
def test_junk_is_not_a_subject(name):
    assert is_junk_tag(name)


@pytest.mark.unit
@pytest.mark.parametrize("name", SUBJECTS)
def test_subjects_are_kept(name):
    assert not is_junk_tag(name)


@pytest.mark.unit
def test_arxiv_codes_are_named_and_unknown_ones_dropped():
    assert clean_tags(["cs.LG", "Machine Learning", "math.ST", "cond-mat.stat-mech", "hep-th", "cs.ZZ"]) == [
        "Machine Learning", "Statistics Theory", "Condensed Matter", "High Energy Physics - Theory"]


@pytest.mark.unit
def test_an_authors_surname_is_not_a_subject():
    assert clean_tags(["lienhard", "Heat transfer"], authors=["John H. Lienhard IV"]) == ["Heat transfer"]
    assert clean_tags(["Levine", "Chemistry"], authors=["Levine| Ira N."]) == ["Chemistry"]


@pytest.mark.unit
def test_clean_tags_trims_dedupes_and_drops_the_books_own_details():
    tags = ['"functional analysis', "Functional Analysis", "Linear Algebra Concepts and Applications",
            "Bei Hu", "Basic Books", "Cambridge Library Collection", "linear algebra", "# ISBN-10 / ASIN:"]
    assert clean_tags(tags, title="Linear Algebra Concepts and Applications", authors=["Bei Hu"],
                      publishers=["Basic Books"], series=["Cambridge Library Collection"]) == [
        "functional analysis", "linear algebra"]


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as env:
        yield env


def _tags(env):
    con = sqlite3.connect(env.library_dir / "metadata.db")
    try:
        links = con.execute("SELECT l.book, t.name FROM books_tags_link l JOIN tags t ON t.id = l.tag "
                            "ORDER BY l.book, t.name").fetchall()
        names = {row[0] for row in con.execute("SELECT name FROM tags")}
    finally:
        con.close()
    by_book = {}
    for book, name in links:
        by_book.setdefault(book, []).append(name)
    return by_book, names


@pytest.mark.unit
def test_rebuild_tidies_every_books_tags_before_the_lookups(env, monkeypatch):
    from cps import metadata_helper
    from cps.tasks.metadata_rebuild import TaskRebuildMetadata
    first = env.add_book("Analysis", tags=["Mathematics", "# Hardcover: 308 pages", "9780511629563"])
    second = env.add_book("Logic", tags=['"functional analysis', "Springer 2011", "Mathematics"])
    seen = []
    monkeypatch.setattr(metadata_helper, "fetch_and_apply_metadata",
                        lambda book_id, force=False, unanswered=None: seen.append(_tags(env)[0][book_id]) or False)
    monkeypatch.setattr("cps.duplicate_index.mark_duplicate_index_pending", lambda reason=None: None)

    with env.app.test_request_context():
        TaskRebuildMetadata().start(None)

    by_book, names = _tags(env)
    assert by_book == {first: ["Mathematics"], second: ["functional analysis", "Mathematics"]}
    assert names == {"Mathematics", "functional analysis"}
    assert sorted(seen) == sorted([by_book[first], by_book[second]])


@pytest.mark.unit
def test_new_import_keeps_only_subjects(env):
    from cps.tag_cleanup import tidy_new_book_tags
    book = env.add_book("Measure Theory", tags=["Measure theory", "Hardcover: 436 pages", "B0000CJ4BU"])
    keep = env.add_book("Other", tags=["Probability"])
    assert tidy_new_book_tags(book) is True
    assert tidy_new_book_tags(keep) is False
    by_book, names = _tags(env)
    assert by_book == {keep: ["Probability"]}
    assert names == {"Probability"}


@pytest.mark.unit
def test_lookup_adds_only_subject_tags(env, monkeypatch):
    from cps import metadata_helper
    settings = {"auto_metadata_fetch_enabled": 1}
    monkeypatch.setattr(metadata_helper, "CWA_DB", lambda: SimpleNamespace(get_cwa_settings=lambda: settings))
    record = SimpleNamespace(title="Abstract Algebra", authors=["Test Author"], description="", publisher="",
                             tags=["Mathematics", "Springer 2011", "Abstract Algebra", "ISBN-13:"], series="",
                             series_index=0, publishedDate=None, identifiers={}, cover=None,
                             source=SimpleNamespace(description="Google Books"))
    monkeypatch.setattr(metadata_helper, "metadata_providers", [FakeProvider(
        __id__="google", __name__="Google", identifier_types=frozenset(),
        search=lambda q, *a: [record])])
    book = env.add_book("Abstract Algebra")
    assert metadata_helper.fetch_and_apply_metadata(book)
    assert _tags(env)[0] == {book: ["Mathematics"]}


def test_open_librarys_facets_go_and_inverted_fiction_subjects_turn_round():
    from cps.tag_cleanup import clean_tags
    assert clean_tags(["form:novel", "genre:gothic", "Fiction, psychological", "Married people, fiction",
                       "Long island (n.y.), fiction", "Psychological fiction", "Holmes, Sherlock (Fictitious character)"]) == [
        "Psychological fiction", "Married people", "Long island (n.y.)", "Holmes, Sherlock (Fictitious character)"]
