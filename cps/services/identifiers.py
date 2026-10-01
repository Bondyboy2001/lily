# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Book and paper identifiers: the patterns the metadata providers share, and
recognising an identifier typed into the Fetch Metadata search box."""

import re

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
