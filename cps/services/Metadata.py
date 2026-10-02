# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import abc
import dataclasses
import os
import re
import time
from typing import Dict, Generator, List, Optional, Union

from cps import constants


@dataclasses.dataclass
class MetaSourceInfo:
    id: str
    description: str
    link: str


@dataclasses.dataclass
class MetaRecord:
    id: Union[str, int]
    # In full, "Title: Subtitle" when the provider knows a subtitle (kept below too, so a
    # book known by the title alone still matches: metadata_suggestions.title_forms)
    title: str
    authors: List[str]
    url: str
    source: MetaSourceInfo
    cover: str = os.path.join(constants.STATIC_DIR, 'generic_cover.svg')
    description: Optional[str] = ""
    series: Optional[str] = None
    series_index: Optional[Union[int, float]] = 0
    identifiers: Dict[str, Union[str, int]] = dataclasses.field(default_factory=dict)
    publisher: Optional[str] = None
    publishedDate: Optional[str] = None
    rating: Optional[int] = 0
    languages: Optional[List[str]] = dataclasses.field(default_factory=list)
    tags: Optional[List[str]] = dataclasses.field(default_factory=list)
    format: Optional[str] = None
    subtitle: Optional[str] = None


class ProviderError(Exception):
    """A provider could not be reached or did not answer properly. Raised rather than returned
    as no results, so a search shows as failed and a rebuild can say who didn't answer. The
    message is safe to log: no keys or request URLs."""


class ProviderBusy(ProviderError):
    """Not asked at all: the provider answered 429 a moment ago (CoolOff)."""


class CoolOff:
    """Leaves a service alone for a while after it answers 429 (too many requests), so a
    library rebuild doesn't ask it again, and wait for the refusal, for every book."""

    def __init__(self, seconds: float = 60, longest: float = 600):
        self.seconds = seconds
        self.longest = longest
        self._until = 0.0

    def active(self) -> bool:
        return time.monotonic() < self._until

    def start(self, response=None) -> None:
        """Begin the pause: as long as the response's Retry-After asks, within `longest`."""
        wait = (getattr(response, "headers", None) or {}).get("Retry-After", "")
        seconds = min(float(wait), self.longest) if str(wait).isdigit() else self.seconds
        self._until = time.monotonic() + seconds


class Metadata:
    __name__ = "Generic"
    __id__ = "generic"
    # Identifier types search_identifiers can look up
    identifier_types: frozenset = frozenset()
    # A provider whose search makes a request per result to fill in details can also offer
    # search_titles(query, generic_cover, locale), the same search without them, and
    # complete(record), which fetches them for one record. Lookups that apply a single exact
    # match (imports, Rebuild metadata) use the pair; see OpenLibrary.

    @abc.abstractmethod
    def search(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> Optional[List[MetaRecord]]:
        pass

    def search_identifiers(
        self, identifiers: Dict[str, str], generic_cover: str = "", locale: str = "en"
    ) -> List[MetaRecord]:
        """Exact lookup by the book's identifiers (lower-case type -> value, e.g.
        {"isbn": ..., "doi": ..., "arxiv": ...}). Providers that can't look up by id
        return nothing."""
        return []

    @staticmethod
    def get_title_tokens(
        title: str, strip_joiners: bool = True
    ) -> Generator[str, None, None]:
        """
        Taken from calibre source code
        It's a simplified (cut out what is unnecessary) version of
        https://github.com/kovidgoyal/calibre/blob/99d85b97918625d172227c8ffb7e0c71794966c0/
        src/calibre/ebooks/metadata/sources/base.py#L363-L367
        (src/calibre/ebooks/metadata/sources/base.py - lines 363-398)
        """
        title_patterns = [
            (re.compile(pat, re.IGNORECASE), repl)
            for pat, repl in [
                # Remove things like: (2010) (Omnibus) etc.
                (
                    r"(?i)[({\[](\d{4}|omnibus|anthology|hardcover|"
                    r"audiobook|audio\scd|paperback|turtleback|"
                    r"mass\s*market|edition|ed\.)[\])}]",
                    "",
                ),
                # Remove any strings that contain the substring edition inside
                # parentheses
                (r"(?i)[({\[].*?(edition|ed.).*?[\]})]", ""),
                # Remove commas used a separators in numbers
                (r"(\d+),(\d+)", r"\1\2"),
                # Remove hyphens only if they have whitespace before them
                (r"(\s-)", " "),
                # Replace other special chars with a space
                (r"""[:,;!@$%^&*(){}.`~"\s\[\]/]《》「」“”""", " "),
            ]
        ]

        for pat, repl in title_patterns:
            title = pat.sub(repl, title)

        tokens = title.split()
        for token in tokens:
            token = token.strip().strip('"').strip("'")
            if token and (
                not strip_joiners or token.lower() not in ("a", "and", "the", "&")
            ):
                yield token
