"""Authors as the people they name: lists split, catalogue names turned round, capitals
lowered, and names that are no one's (ids, accounts, job stamps) dropped. The examples are
names a real library's PDFs gave calibre."""
import sqlite3

import pytest

from .lily_env import lily_env

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name, expected", [
    # Lists of people in one name
    ("Stefano Bellucci, Sergio Ferrara, Alessio Marrani", ["Stefano Bellucci", "Sergio Ferrara", "Alessio Marrani"]),
    ("Daniel Gorenstein,Richard Lyons,Ronald Solomon", ["Daniel Gorenstein", "Richard Lyons", "Ronald Solomon"]),
    ("Akcoglu, Mustafa A.,Ha, Dzung Minh.,Bartha, Paul F. A.",
     ["Mustafa A. Akcoglu", "Dzung Minh Ha", "Paul F. A. Bartha"]),
    ("Raab, R. E.; De Lange, O. L.", ["R. E. Raab", "O. L. De Lange"]),
    ("Komori, Y.; Markovic, V.", ["Y. Komori", "V. Markovic"]),
    ("Editor: H. Weber, G. Herziger, R. Poprawe", ["H. Weber", "G. Herziger", "R. Poprawe"]),
    # Catalogue names: surname first, roles, life dates
    ("Lyndon, Roger C.", ["Roger C. Lyndon"]),
    ("Murray, Francis J. (Francis Joseph), 1911-1996.", ["Francis J. Murray"]),
    ("Atkins, P. W. (Peter William), 1940-", ["P. W. Atkins"]),
    ("Giessen, E. van der.", ["E. van der Giessen"]),
    ("Thomas Ward, editors", ["Thomas Ward"]),
    ("Miles Reid (editor), Alexei Skorobogatov (editor)", ["Miles Reid", "Alexei Skorobogatov"]),
    ("Applegate, David L.(Author)", ["David L. Applegate"]),
    ("Alex H. Barnett, et al.", ["Alex H. Barnett"]),
    ("By Jiří Adámek, Jiří Adámek (ing.), Věra Trnková", ["Jiří Adámek", "Věra Trnková"]),
    # Two single names may be two people's surnames: left as calibre's sorted form
    ("Johnson, Kotz", ["Johnson, Kotz"]),
    # Capitals and escapes
    ("BACHIR BEKKA, PIERRE DE LA HARPE", ["Bachir Bekka", "Pierre de la Harpe"]),
    ("OSCAR GARCIA-PRADA", ["Oscar Garcia-Prada"]),
    ("AOKI YASUMICHI ET AL", ["Aoki Yasumichi"]),
    ("Lech G\\u00f3rniewicz", ["Lech Górniewicz"]),
    # Names left as they are
    ("Frank Herbert", ["Frank Herbert"]),
    ("John H. Lienhard IV", ["John H. Lienhard IV"]),
    ("'t Hooft", ["'t Hooft"]),
    ("SIAM", ["SIAM"]),
    ("Unknown", ["Unknown"]),
    # No one's names
    ("0000253", []),
    ("Administrator@NEO-10", []),
    ("Administrador", []),
    ("Katharina Steingraeber Heidelberg 1107 1997 Oct 17 14:59:21", []),
    ("[Anonymus AC07883860]", []),
    ("2nd -- Harold Abelson", []),
    ("Intech Prepress 2", []),
    ("David1", []),
    ("uoyilmaz", []),
])
def test_a_name_is_cleaned_into_the_people_it_names(name, expected):
    from cps.author_cleanup import clean_author_names
    assert clean_author_names(name) == expected


def test_a_given_name_turns_a_sorted_name_round():
    from cps.author_cleanup import clean_author_names, given_names
    given = given_names(["Daniel Gorenstein", "Caroline Series", "Kleppner| Daniel"])
    assert given == {"daniel", "caroline"}
    assert clean_author_names("Kleppner, Daniel", given) == ["Daniel Kleppner"]
    assert clean_author_names("Johnson, Kotz", given) == ["Johnson, Kotz"]


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as env:
        yield env


def _q(env, sql, *args):
    con = sqlite3.connect(env.library_dir / "metadata.db")
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _authors(env, book_id):
    return [row[0] for row in _q(env, "SELECT a.name FROM books_authors_link l JOIN authors a ON a.id=l.author "
                                      "WHERE l.book=? ORDER BY l.id", book_id)]


def _tidy(env, books=None, folders=False):
    from cps import db
    from cps.author_cleanup import tidy_authors
    cdb = db.CalibreDB(expire_on_commit=False, init=True)
    try:
        if books is not None:
            books = [cdb.get_book(book_id) for book_id in books]
        with env.app.test_request_context():
            return tidy_authors(cdb.session, books, str(env.library_dir) if folders else None)
    finally:
        cdb.session.close()


def test_the_library_tidy_splits_merges_and_drops_authors(env):
    listed = env.add_book("Black Holes", author="Stefano Bellucci| Sergio Ferrara")
    known = env.add_book("Group Theory", author="Sergio Ferrara")
    sorted_name = env.add_book("Physical Chemistry", author="Levine| Ira N.")
    junk = env.add_book("Hyperplane Arrangements", author="0000253")
    fine = env.add_book("Dune", author="Frank Herbert")
    assert _tidy(env) == (3, 3)
    # The links keep no order; author_sort holds it
    assert sorted(_authors(env, listed)) == ["Sergio Ferrara", "Stefano Bellucci"]
    assert _authors(env, known) == ["Sergio Ferrara"]
    assert _authors(env, sorted_name) == ["Ira N. Levine"]
    assert _authors(env, junk) == ["Unknown"]
    assert _authors(env, fine) == ["Frank Herbert"]
    assert _q(env, "SELECT author_sort FROM books WHERE id=?", sorted_name) == [("Levine, Ira N.",)]
    # The old names are gone, and Sergio Ferrara is one author
    assert _q(env, "SELECT COUNT(*) FROM authors WHERE name='Sergio Ferrara'") == [(1,)]
    assert not _q(env, "SELECT 1 FROM authors WHERE name IN ('0000253', 'Levine| Ira N.')")
    # A second run finds nothing to do
    assert _tidy(env) == (0, 0)


def test_a_name_in_capitals_is_renamed_and_its_folder_follows(env):
    book = env.add_book("Spectra", author="OSCAR GARCIA-PRADA")
    old_dir = env.library_dir / "OSCAR GARCIA-PRADA" / "Spectra"
    old_dir.mkdir(parents=True)
    (old_dir / "Spectra.epub").write_bytes(b"epub")
    assert _tidy(env, folders=True) == (1, 0)
    assert _authors(env, book) == ["Oscar Garcia-Prada"]
    [(path,)] = _q(env, "SELECT path FROM books WHERE id=?", book)
    assert path == "Oscar Garcia-Prada/Spectra (%d)" % book
    assert (env.library_dir / path).is_dir()


def test_a_sorted_name_joins_the_author_the_library_already_has(env):
    sorted_name = env.add_book("Plasma Physics", author="Fitzpatrick| Richard")
    known = env.add_book("Newtonian Dynamics", author="Richard Fitzpatrick")
    _tidy(env)
    assert _authors(env, sorted_name) == _authors(env, known) == ["Richard Fitzpatrick"]


def test_only_the_new_books_authors_are_tidied_on_import(env):
    old = env.add_book("Old Book", author="Levine| Ira N.")
    new = env.add_book("Hyperplane Arrangements", author="0000253")
    assert _tidy(env, [new]) == (1, 1)
    assert _authors(env, new) == ["Unknown"]
    assert _authors(env, old) == ["Levine| Ira N."]
