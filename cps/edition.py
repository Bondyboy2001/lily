# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""A book's edition in its title: providers often name a book "Probability and Statistical
Inference (9th Edition)". split_edition takes the bracketed edition off and gives its number,
for the book's Edition field (cwa.db book_editions). get_meta.js does the same in the editor."""

import re

_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13,
    "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20,
}

# "(9th Edition)", "[2nd ed.]", "(Ninth edition)", "(3rd edn)", "(Edition 4)"
_EDITION = re.compile(
    r"\s*[(\[]\s*(?:"
    r"(?P<num>\d{1,3})(?:st|nd|rd|th)?\.?|(?P<word>" + "|".join(_WORDS) + r")"
    r")\s+(?:edition|edn\.?|ed\.?)\s*[)\]]"
    r"|\s*[(\[]\s*edition\s+(?P<num2>\d{1,3})\s*[)\]]",
    re.IGNORECASE,
)


def split_edition(title):
    """(title without its bracketed edition, the edition number) or (title, None) when it
    names none. Only the first such bracket is taken; the rest of the title is kept as it is."""
    match = _EDITION.search(title or "")
    if not match:
        return title, None
    number = match.group("num") or match.group("num2")
    edition = int(number) if number else _WORDS[match.group("word").lower()]
    if not 1 <= edition <= 999:
        return title, None
    rest = (title[:match.start()] + title[match.end():]).strip()
    return (rest, edition) if rest else (title, None)
