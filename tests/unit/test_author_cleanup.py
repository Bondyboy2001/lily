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
    ("Intech Prepress 2", []),
    ("David1", []),
    ("uoyilmaz", []),
    # The account that made the file, in any script or none: "Администратор" with each
    # letter's first byte lost
    ("\x104<8=8AB@0B>@", []),
    ("Администратор", []),
    # Companies, machines and the tools that made the file
    ("Lightning Source Inc", []),
    ("Springer-Verlag GmbH", []),
    ("World Scientific Publishing", []),
    ("The National Academies Press", []),
    ("JPG To PDF Converter", []),
    ("ILOVEPDF.COM", []),
    ("Copier User", []),
    ("MY PC", []),
    ("HP_Owner", []),
    ("Pagination_Cover", []),
    ("Second Edition", []),
    ("Author Unknown", []),
    ("massiveMonkey", []),
    ("OCR", []),
    ("A.N.", []),
    ("Differentiable Manifolds: A Theoretical Physics Approach", []),
    ("Borisov, A. V.;Higher Education Press Ltd. Comp.;Mamaev, Ivan S.;", ["A. V. Borisov", "Ivan S. Mamaev"]),
    # A download site's file name: the authors are its second part
    ("-- Marco Taboga -- Second edition, North C", ["Marco Taboga"]),
    ("2nd -- Harold Abelson", ["Harold Abelson"]),
    ("Linear Classifiers, Gradient -- Zsolt Kira; Various -- 994b5c0778648904d24e608b5d8eb048 -- Anna’s Archive",
     ["Zsolt Kira"]),
    ("Europe -- 9780073385", []),
    # Quote marks, brackets, catalogue marks and garbled accents
    ('"Rice, Richard G.,Duong, D. Do."', ["Richard G. Rice", "D. Do Duong"]),
    ('"Jean-François Dat, Sascha Orlik, Michael Rapoport"', ["Jean-François Dat", "Sascha Orlik", "Michael Rapoport"]),
    ("scanning [ POISSON ]", []),
    ("Viktor Vasil_evich Prasolov", ["Viktor Vasil'evich Prasolov"]),
    ("GÃ©rard A Maugin,Martine Rousseau", ["Gérard A Maugin", "Martine Rousseau"]),
    ("罗锋，顾险峰编著", ["罗锋", "顾险峰"]),
    # Initials run into the surname, and an initial's stop set apart
    ("G.C.Smith", ["G.C. Smith"]),
    ("R.TEMAM", ["R. Temam"]),
    ("C.FOIAS, O.MANLEY, R.ROSA", ["C. Foias", "O. Manley", "R. Rosa"]),
    ("ANDREW J . MAJDA", ["Andrew J. Majda"]),
    # More ways a list is written
    ("Peter Atkins . Julio De Paula", ["Peter Atkins", "Julio De Paula"]),
    ("Bryce S. DeWitt, Neill Graham, edrs", ["Bryce S. DeWitt", "Neill Graham"]),
    ("J. R. JAMES, P. S. HALL,and C.WOOD", ["J. R. James", "P. S. Hall", "C. Wood"]),
    ("Murray, Richard M., Del Vecchio, Domitilla", ["Richard M. Murray", "Domitilla Del Vecchio"]),
    ("M. Jamil Aslam, Faheem Hussian, Asghar Qadir, Riazuddin",
     ["M. Jamil Aslam", "Faheem Hussian", "Asghar Qadir", "Riazuddin"]),
    # People these rules must not catch
    ("William H. Press", ["William H. Press"]),
    ("T.A. Springer", ["T.A. Springer"]),
    ("Press", ["Press"]),
    ("d'Inverno", ["d'Inverno"]),
    ("Li", ["Li"]),
    ("McQuarrie", ["McQuarrie"]),
    ("Marius Andronie, Transylvania, Romania", ["Marius Andronie, Transylvania, Romania"]),
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


def test_a_word_that_ends_more_names_than_it_starts_is_a_surname():
    # "Zhang Wei" alone would make Zhang a forename and "Han, Zhang" one person, "Zhang Han"
    from cps.author_cleanup import clean_author_names, given_names
    given = given_names(["Zhang Wei", "Fuzhen Zhang", "Shou-Cheng Zhang", "Karen Rhea", "Karen Smith"])
    assert "karen" in given and "zhang" not in given
    assert clean_author_names("Han, Zhang", given) == ["Han, Zhang"]
    assert clean_author_names("Uhlenbeck, Karen", given) == ["Karen Uhlenbeck"]


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


def test_a_forename_that_only_a_split_list_shows_is_used_the_first_time(env):
    # "Daniel" is a forename only once the list is split; found then, a second tidy (the next
    # rebuild) would turn "Kleppner, Daniel" round and count the book as changed again
    listed = env.add_book("Finite Simple Groups", author="Daniel Gorenstein|Richard Lyons")
    sorted_name = env.add_book("Mechanics", author="Kleppner| Daniel")
    assert _tidy(env) == (2, 2)
    assert sorted(_authors(env, listed)) == ["Daniel Gorenstein", "Richard Lyons"]
    assert _authors(env, sorted_name) == ["Daniel Kleppner"]
    assert _tidy(env) == (0, 0)


def test_the_library_tidy_drops_accounts_and_tools_and_keeps_the_people_beside_them(env):
    made_by = env.add_book("Number Theory", author="\x104<8=8AB@0B>@")
    printer = env.add_book("Algebra", author="Lightning Source Inc")
    mixed = env.add_book("Topology", author="Yan| Min; Higher Education Press Ltd. Comp. Staff;")
    site = env.add_book("Probability", author="-- Marco Taboga -- Second edition| North C")
    assert _tidy(env) == (4, 4)
    assert _authors(env, made_by) == _authors(env, printer) == ["Unknown"]
    assert _authors(env, mixed) == ["Yan| Min"]
    assert _authors(env, site) == ["Marco Taboga"]
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
