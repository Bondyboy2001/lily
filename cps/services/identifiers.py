# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Book and paper identifiers: the patterns the metadata providers share, recognising
an identifier typed into the Fetch Metadata search box, and the page each identifier
is about."""

import re
from urllib.parse import quote

ISBN_RE = re.compile(r"(97[89])?\d{9}[\dX]")
# New style (2301.00001) and old style (hep-th/9901001, math.AG/0309136) arXiv ids
ARXIV_ID = r"\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7}"
ARXIV_ID_RE = re.compile(rf"({ARXIV_ID})(v\d+)?")
DOI = r"10\.\d{4,9}/\S+"
DOI_RE = re.compile(DOI)
# arXiv registers its own DOIs with DataCite, so Crossref doesn't know them
ARXIV_DOI_PREFIX = "10.48550/arXiv."

_TYPED = (
    ("doi", re.compile(rf"(?:doi:\s*|(?:https?://)?(?:dx\.)?doi\.org/)?({DOI})", re.I)),
    ("arxiv", re.compile(rf"(?:arxiv:\s*|(?:https?://)?(?:www\.)?arxiv\.org/(?:abs|pdf)/)?({ARXIV_ID})"
                         r"(?:v\d+)?(?:\.pdf)?/?", re.I)),
    ("hardcover-id", re.compile(r"hardcover-id:\s*(\d+)", re.I)),
)


def compact_isbn(isbn):
    """'978-0-441-17271-9' -> '9780441172719'."""
    return re.sub(r"[\s-]", "", isbn or "").upper()


def normalise_identifiers(identifiers):
    """Lower-case types and trimmed values, blanks dropped, ISBN compacted."""
    clean = {key.strip().lower(): value.strip() for key, value in identifiers.items()
             if isinstance(key, str) and isinstance(value, str) and value.strip()}
    if "isbn" in clean:
        clean["isbn"] = compact_isbn(clean["isbn"])
    return clean


def parse_identifier(text):
    """The identifier a search box holds when it holds nothing else: an ISBN, a DOI,
    an arXiv id or link, or hardcover-id:N. Empty when the text is ordinary text."""
    text = (text or "").strip()
    compact = compact_isbn(text)
    if ISBN_RE.fullmatch(compact):
        return {"isbn": compact}
    for key, pattern in _TYPED:
        match = pattern.fullmatch(text)
        if match:
            return {key: match.group(1)}
    return {}


def arxiv_id_from_doi(doi):
    """'10.48550/arXiv.2301.00001' -> '2301.00001'; empty for any other DOI."""
    doi = (doi or "").strip()
    return doi[len(ARXIV_DOI_PREFIX):] if doi.lower().startswith(ARXIV_DOI_PREFIX.lower()) else ""


# The page an identifier is about, on the service that issued it. A type with no page of
# its own is absent rather than guessed at: Hardcover's ids are numbers, and a book page
# is only addressable by its slug, so the card leaves those as text.
_ID_PAGES = {
    "openlibrary": "https://openlibrary.org/works/{0}",
    "hardcover-slug": "https://hardcover.app/books/{0}",
    "google": "https://books.google.com/books?id={0}",
    "isbn": "https://openlibrary.org/isbn/{0}",
    "doi": "https://doi.org/{0}",
    "arxiv": "https://arxiv.org/abs/{0}",
}


def identifier_url(id_type, val):
    """Where an identifier leads, or '' when its type has no public page. The host is
    ours either way, so only the value is escaped — but slashes stay, as an old-style
    arXiv id (hep-th/9901001) is a path of its own."""
    val = str(val or "").strip()
    page = _ID_PAGES.get((id_type or "").strip().lower())
    return page.format(quote(val, safe="/")) if page and val else ""


def identifier_pages(identifiers):
    """Every identifier with a page, as type -> URL, for a result to link: the ones
    without are left out rather than sent empty."""
    pages = {}
    for id_type, val in (identifiers or {}).items():
        url = identifier_url(id_type, val)
        if url:
            pages[id_type] = url
    return pages
