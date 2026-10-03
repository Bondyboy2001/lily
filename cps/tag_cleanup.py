# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Keeps tags to subjects.

calibre turns a PDF's Keywords field into tags, and those are often ISBNs, publisher lines,
shop listing scraps ("# Hardcover: 308 pages") or the uploader's signature. Imports, metadata
lookups and Rebuild metadata pass a book's tags through clean_tags so only subjects stay."""

import re

# Stray punctuation calibre leaves around a keyword: quotes, list bullets, markdown headings
_EDGE_CHARS = ' \t\r\n"\'“”‘’`•·*#-–—_:;,.|/\\'
_ISBN = re.compile(r'(?<![\dA-Z])(?:97[89][- ]?)?(?:\d[- ]?){9}[\dX](?![\dA-Z])', re.I)
_ASIN = re.compile(r'^B0[0-9A-Z]{8}$')
# Three or more numbers: a date, a journal reference or a page range ("SIAM Rev. 1970.12:1-63").
# A subject has at most a year or two ("History, 1914-1918") or a version ("Python 3").
_NUMBER = re.compile(r'\d+')
# A word of letters and two or more digits ("62H12", "gnv64", "6x9") or a long trailing number
# that isn't a year ("Series 514"); ordinals ("20th Century") are words
_CODE = re.compile(r'(?=\w*\d\w*\d)(?=\w*[^\W\d_])\w{3,}|\s(?!(?:1[5-9]|20)\d\d$)\d{3,}$')
_ORDINAL = re.compile(r'\b\d+(?:st|nd|rd|th)\b', re.I)
_URLISH = re.compile(r'https?:|www\.|@|~|\.(?:com|org|net|edu|gov|ws|in|ru|info)\b|'
                     r'\.(?:pdf|epub|djvu|mobi|azw3?|tex)$', re.I)
# Field labels from shop listings and book front matter
_LABEL = re.compile(
    r'^(?:isbn|asin|ean|doi|issn|lccn|oclc|wcn|publisher|published|publication|language|edition|'
    r'pages?|number of pages|hardcover|paperback|format|file size|print length|series|copyright|'
    r'product dimensions|shipping weight|editor|author|title|subject|keywords?|year|date)\b.*:', re.I)
# Words a subject never contains: identifiers, edition notes, typesetting, release signatures
_JUNK_WORDS = re.compile(
    r'\b(?:isbn|asin|ean|e-?isbn|doi|compiled|uploaded|scanned|convert\w*|copyright|'
    r'tex output|latex|typesetting|gnuplot|backref|keypagebackref|avax\w*|exlib|softarchive|denixxx|'
    r'snorgared|true liar|team \w+|knowledge is power|pages?|pp|retail|e-?books?|front ?matter|'
    r'back ?matter|table of contents|edition|version|volume|vol|no|custom|monographs?|'
    r'library collection|graduate texts|lecture notes?|texts in|symposium|proceedings|'
    r'subject classification|publication date|project gutenberg|transcriber)\b|'
    r'©|_|;|\b(?:is|are|language:? english)\b|^(?:a|an|and|the|by|edited) |спизж|пизд',
    re.I)
_PUBLISHER = re.compile(
    r'\b(?:springer|birkh\S{1,2}user|wiley|elsevier|press$|crc|de gruyter|world scientific|'
    r'publishers?|publishing (?:company|house|group|co)|pub co|verlag|vieweg|teubner|mcgraw|prentice|'
    r'addison|pearson|routledge|taylor & francis|chapman|a k peters|nova science|north-holland|'
    r'butterworth|heinemann|claypool|createspace|dover|artech|atlantis|eagle hill|humana|'
    r'\w+ical society|society (?:for|of)|association (?:for|of)|'
    r'cambridge e?text|referex)\b',
    re.I)
# A publisher or society with the year of the edition: "Cambridge University Press 2005", "AMS 2005"
_YEAR_END = re.compile(r'^(?:[A-Z]{2,6}|.*\b(?:[Pp]ress|[Uu]niversity|[Ii]nternational|[Pp]ublications?|'
                       r'[Ss]ociety|[Ii]nstitute))\s(?:1[5-9]|20)\d\d$')
_PLACEHOLDERS = {
    'none', 'null', 'nil', 'unknown', 'untitled', 'n/a', 'na', 'image', 'cover', 'fm', 'general',
    'free', 'pdf', 'epub', 'djvu', 'book', 'books', 'misc', 'other', 'default', 'keywords', 'hc',
    'subject', 'new subject', 'tag', 'tags',
}
# Longer than a subject: a sentence, a description or a book title
_MAX_WORDS = 6
_MAX_LENGTH = 50


# arXiv's subject classes, which a paper's PDF and arXiv's own records give as codes ("cs.LG")
_ARXIV_ARCHIVES = {
    'cs': 'Computer Science', 'math': 'Mathematics', 'stat': 'Statistics', 'q-fin': 'Quantitative Finance',
    'econ': 'Economics', 'eess': 'Electrical Engineering and Systems Science', 'astro-ph': 'Astrophysics',
    'cond-mat': 'Condensed Matter', 'gr-qc': 'General Relativity and Quantum Cosmology',
    'hep-ex': 'High Energy Physics - Experiment', 'hep-lat': 'High Energy Physics - Lattice',
    'hep-ph': 'High Energy Physics - Phenomenology', 'hep-th': 'High Energy Physics - Theory',
    'math-ph': 'Mathematical Physics', 'nlin': 'Nonlinear Sciences', 'nucl-ex': 'Nuclear Experiment',
    'nucl-th': 'Nuclear Theory', 'physics': 'Physics', 'quant-ph': 'Quantum Physics',
    'q-bio': 'Quantitative Biology',
}
_ARXIV_CLASSES = {
    'cs': {
        'AI': 'Artificial Intelligence', 'AR': 'Hardware Architecture', 'CC': 'Computational Complexity',
        'CE': 'Computational Engineering, Finance, and Science', 'CG': 'Computational Geometry',
        'CL': 'Computation and Language', 'CR': 'Cryptography and Security',
        'CV': 'Computer Vision and Pattern Recognition', 'CY': 'Computers and Society', 'DB': 'Databases',
        'DC': 'Distributed, Parallel, and Cluster Computing', 'DL': 'Digital Libraries',
        'DM': 'Discrete Mathematics', 'DS': 'Data Structures and Algorithms', 'ET': 'Emerging Technologies',
        'FL': 'Formal Languages and Automata Theory', 'GR': 'Graphics', 'GT': 'Game Theory',
        'HC': 'Human-Computer Interaction', 'IR': 'Information Retrieval', 'IT': 'Information Theory',
        'LG': 'Machine Learning', 'LO': 'Logic in Computer Science', 'MA': 'Multiagent Systems',
        'MM': 'Multimedia', 'MS': 'Mathematical Software', 'NA': 'Numerical Analysis',
        'NE': 'Neural and Evolutionary Computing', 'NI': 'Networking and Internet Architecture',
        'OS': 'Operating Systems', 'PF': 'Performance', 'PL': 'Programming Languages', 'RO': 'Robotics',
        'SC': 'Symbolic Computation', 'SD': 'Sound', 'SE': 'Software Engineering',
        'SI': 'Social and Information Networks', 'SY': 'Systems and Control',
    },
    'math': {
        'AC': 'Commutative Algebra', 'AG': 'Algebraic Geometry', 'AP': 'Analysis of PDEs',
        'AT': 'Algebraic Topology', 'CA': 'Classical Analysis and ODEs', 'CO': 'Combinatorics',
        'CT': 'Category Theory', 'CV': 'Complex Variables', 'DG': 'Differential Geometry',
        'DS': 'Dynamical Systems', 'FA': 'Functional Analysis', 'GM': 'General Mathematics',
        'GN': 'General Topology', 'GR': 'Group Theory', 'GT': 'Geometric Topology',
        'HO': 'History and Overview', 'IT': 'Information Theory', 'KT': 'K-Theory and Homology',
        'LO': 'Logic', 'MG': 'Metric Geometry', 'MP': 'Mathematical Physics', 'NA': 'Numerical Analysis',
        'NT': 'Number Theory', 'OA': 'Operator Algebras', 'OC': 'Optimization and Control',
        'PR': 'Probability', 'QA': 'Quantum Algebra', 'RA': 'Rings and Algebras',
        'RT': 'Representation Theory', 'SG': 'Symplectic Geometry', 'SP': 'Spectral Theory',
        'ST': 'Statistics Theory',
    },
    'stat': {
        'AP': 'Applied Statistics', 'CO': 'Computational Statistics', 'ME': 'Statistical Methodology',
        'ML': 'Machine Learning', 'TH': 'Statistics Theory',
    },
    'q-fin': {
        'CP': 'Computational Finance', 'EC': 'Economics', 'GN': 'General Finance',
        'MF': 'Mathematical Finance', 'PM': 'Portfolio Management', 'PR': 'Pricing of Securities',
        'RM': 'Risk Management', 'ST': 'Statistical Finance', 'TR': 'Trading and Market Microstructure',
    },
    'econ': {'EM': 'Econometrics', 'GN': 'General Economics', 'TH': 'Theoretical Economics'},
    'eess': {'AS': 'Audio and Speech Processing', 'IV': 'Image and Video Processing',
             'SP': 'Signal Processing', 'SY': 'Systems and Control'},
}
_ARXIV_CODE = re.compile(r'^(%s)(?:\.([A-Za-z-]+))?$' % '|'.join(map(re.escape, _ARXIV_ARCHIVES)), re.I)
# The parts of a name that come after the surname
_NAME_SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv'}


def arxiv_subject(tag):
    """arXiv's name for a subject class code: "cs.LG" -> "Machine Learning", "hep-th" ->
    "High Energy Physics - Theory". '' for an unknown class, None for any other tag."""
    match = _ARXIV_CODE.match(tidy_tag(tag))
    if not match:
        return None
    archive, code = match.group(1).lower(), match.group(2)
    if code is None:
        return _ARXIV_ARCHIVES[archive]
    if archive in _ARXIV_CLASSES:
        return _ARXIV_CLASSES[archive].get(code.upper(), '')
    # A physics archive's classes are its own fields: "cond-mat.stat-mech" is condensed matter
    return _ARXIV_ARCHIVES[archive]


def _surname(name):
    """The author's surname as a tag would give it: "Lienhard| John H." -> "lienhard"."""
    name = (name or '').replace('|', ',')
    words = _norm(name.split(',')[0] if ',' in name else name).split()
    while len(words) > 1 and words[-1] in _NAME_SUFFIXES:
        words.pop()
    return words[-1] if words and len(words[-1]) > 2 else ''


def _norm(text):
    return re.sub(r'[^\w]+', ' ', text or '').strip().casefold()


def tidy_tag(name):
    """The keyword with calibre's stray punctuation trimmed, or '' when nothing is left."""
    return (name or '').strip(_EDGE_CHARS).strip()


def is_junk_tag(name):
    """True for a tag that is not a subject (on its own, without knowing the book)."""
    tag = tidy_tag(name)
    if not re.search(r'[^\W\d_]', tag):
        return True
    if tag.casefold() in _PLACEHOLDERS:
        return True
    if len(tag) > _MAX_LENGTH or len(tag.split()) > _MAX_WORDS or '<' in tag:
        return True
    return bool(_ISBN.search(tag) or _ASIN.match(tag) or len(_NUMBER.findall(tag)) > 2
                or _CODE.search(_ORDINAL.sub('', tag)) or _URLISH.search(tag) or _YEAR_END.match(tag)
                or _LABEL.match(tag) or _JUNK_WORDS.search(tag) or _PUBLISHER.search(tag))


# Open Library's faceted subjects: "genre:gothic", "form:novel"
_FACET = re.compile(r'^[a-z_]+:\S')
# Open Library's inverted fiction subjects: "Fiction, psychological", "Married people, fiction"
_FICTION_FIRST = re.compile(r'^fiction,\s*(.+)$', re.I)
_FICTION_LAST = re.compile(r'^(.+?),\s*fiction$', re.I)


def _uninverted(tag):
    """An inverted fiction subject the right way round ("Fiction, psychological" ->
    "Psychological fiction", "Married people, fiction" -> "Married people"); '' for a facet."""
    if _FACET.match(tag):
        return ''
    first = _FICTION_FIRST.match(tag)
    if first:
        rest = first.group(1).strip()
        return rest[:1].upper() + rest[1:] + ' fiction'
    last = _FICTION_LAST.match(tag)
    if last:
        rest = last.group(1).strip()
        return rest[:1].upper() + rest[1:]
    return tag


def clean_tags(names, title='', authors=(), publishers=(), series=()):
    """The subjects among names, in order and without repeats.

    Trims stray punctuation, names arXiv's subject codes, turns Open Library's inverted fiction
    subjects round, and drops its facets ("genre:gothic"), junk and anything that just repeats
    the book's own title, authors (or their surnames), publisher or series."""
    own = {_norm(value) for value in [title, *authors, *publishers, *series] if _norm(value)}
    own |= {surname for surname in map(_surname, authors) if surname}
    own_title = _norm(title)
    kept, seen = [], set()
    for name in names:
        subject = arxiv_subject(name)
        tag = _uninverted(tidy_tag(name)) if subject is None else subject
        key = _norm(tag)
        if not key or key in seen or key in own or is_junk_tag(tag):
            continue
        # "Linear Algebra Done Right: lecture notes" repeats the title; "linear algebra" is its subject
        if len(own_title.split()) > 2 and own_title in key:
            continue
        seen.add(key)
        kept.append(tag)
    return kept


def clean_book_tags(book, session):
    """Swap a book's tags for their cleaned form; True when anything changed. The caller commits."""
    from cps import db
    current = [tag.name for tag in book.tags]
    wanted = clean_tags(current,
                        title=book.title,
                        authors=[author.name for author in book.authors],
                        publishers=[publisher.name for publisher in book.publishers],
                        series=[serie.name for serie in book.series])
    if wanted == current:
        return False
    tags = []
    for name in wanted:
        tag = session.query(db.Tags).filter(db.Tags.name == name).first()
        if tag is None:
            tag = db.Tags(name=name)
            session.add(tag)
        tags.append(tag)
    book.tags = tags
    return True


def delete_unused_tags(session):
    """Remove tags no book uses any more; returns how many. The caller commits."""
    from cps import db
    unused = session.query(db.Tags).filter(~db.Tags.id.in_(
        session.query(db.books_tags_link.c.tag))).all()
    for tag in unused:
        session.delete(tag)
    return len(unused)


def tidy_library_tags(session):
    """Clean every book's tags and drop the leftovers: (books changed, tags removed). Commits."""
    from cps import db
    changed = 0
    for book in session.query(db.Books).filter(db.Books.tags.any()).all():
        changed += clean_book_tags(book, session)
    session.flush()
    removed = delete_unused_tags(session)
    session.commit()
    return changed, removed


def tidy_new_book_tags(book_id):
    """Clean the tags calibre read from a newly imported file; True when anything changed."""
    from cps import db
    cdb = db.CalibreDB(expire_on_commit=False, init=True)
    try:
        book = cdb.get_book(book_id)
        if book is None or not clean_book_tags(book, cdb.session):
            return False
        cdb.session.flush()
        delete_unused_tags(cdb.session)
        cdb.session.commit()
        return True
    except Exception:
        cdb.session.rollback()
        raise
    finally:
        cdb.session.close()
