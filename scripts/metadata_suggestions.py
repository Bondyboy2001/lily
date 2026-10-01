# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""How well a metadata provider record matches a book (title and authors).

The metadata search on the book editor uses match_score() to rank results. Kept
free of Flask/cps imports so the rules can be tested on their own.
"""

import re

_STOPWORDS = frozenset({"a", "an", "the", "of", "and", "in", "on", "to", "for", "by", "with"})
_WORD = re.compile(r"[^\W_]+", re.UNICODE)

HIGH_CONFIDENCE = 0.85   # a score at or above this is treated as the same book
TITLE_WEIGHT = 0.7


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall((text or "").casefold()) if t not in _STOPWORDS}


def _surname(author: str) -> str:
    """'Le Guin, Ursula K.' / 'Ursula K. Le Guin' -> 'guin' (last word of the family name)."""
    author = (author or "").strip()
    if author.casefold() == "unknown":
        return ""  # Calibre's placeholder: no author at all
    if "," in author:
        author = author.split(",", 1)[0]
    words = _WORD.findall(author.casefold())
    return words[-1] if words else ""


def title_similarity(a: str, b: str) -> float:
    """Token overlap of two titles, 0..1. A title fully contained in the other scores
    at least 0.75: that is usually an added subtitle, but can be a sequel ("Dune" vs
    "Dune Messiah"), so it never reaches HIGH_CONFIDENCE on its own."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb)
    jaccard = overlap / len(ta | tb)
    if overlap == min(len(ta), len(tb)):
        return max(jaccard, 0.75)
    return jaccard


def author_similarity(book_authors: list[str], record_authors: list[str]) -> float:
    """Share of the book's authors whose surname appears among the record's."""
    wanted = {s for s in map(_surname, book_authors) if s}
    if not wanted:
        return 0.0
    have = {s for s in map(_surname, record_authors) if s}
    return len(wanted & have) / len(wanted)


def match_score(book_title: str, book_authors: list[str], rec_title: str, rec_authors: list[str]) -> float:
    """0..1 confidence that the record is the same book. Without any author on the
    book the title alone decides, capped so an author-less guess is never 'high'."""
    title = title_similarity(book_title, rec_title)
    if not any(_surname(a) for a in book_authors):
        return min(title, HIGH_CONFIDENCE - 0.01)
    return round(TITLE_WEIGHT * title + (1 - TITLE_WEIGHT) * author_similarity(book_authors, rec_authors), 4)
