# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

# Google Books api document: https://developers.google.com/books/docs/v1/using
from typing import Dict, List, Optional
from datetime import datetime
from os import getenv

import requests

from cps import config, logger
from cps.isoLanguages import get_lang3, get_language_name
from cps.services.Metadata import CoolOff, MetaRecord, MetaSourceInfo, Metadata, ProviderBusy, ProviderError

log = logger.create()


class Google(Metadata):
    __name__ = "Google"
    __id__ = "google"
    identifier_types = frozenset({"isbn"})
    DESCRIPTION = "Google Books"
    META_URL = "https://books.google.com/"
    BOOK_URL = "https://books.google.com/books?id="
    SEARCH_URL = "https://www.googleapis.com/books/v1/volumes"
    ISBN_TYPE = "ISBN_13"
    # _parse_cover asks for a cover within 800x900
    COVER_MAX_PIXELS = 800 * 900

    def __init__(self):
        # Set when Google answers 429: without a key the shared quota is soon used up
        self._busy = CoolOff()

    def search(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> Optional[List[MetaRecord]]:
        title_tokens = list(self.get_title_tokens(query, strip_joiners=False))
        if title_tokens:
            query = " ".join(title_tokens)
        return self._fetch(query, generic_cover, locale)

    def search_identifiers(
        self, identifiers: Dict[str, str], generic_cover: str = "", locale: str = "en"
    ) -> List[MetaRecord]:
        isbn = identifiers.get("isbn")
        if not isbn:
            return []
        return self._fetch("isbn:" + isbn, generic_cover, locale)

    @staticmethod
    def _api_key() -> str:
        # Without a key, Google shares a small anonymous quota per IP and soon answers 429
        return getattr(config, "config_google_books_api_key", None) or getenv("GOOGLE_BOOKS_API_KEY") or ""

    def _fetch(self, q: str, generic_cover: str, locale: str) -> List[MetaRecord]:
        params = {"q": q}
        key = self._api_key()
        if key:
            params["key"] = key
        hint = "" if key else "; set a Google Books API key to avoid the shared quota"
        if self._busy.active():
            raise ProviderBusy("Google Books is out of quota" + hint)
        try:
            results = requests.get(Google.SEARCH_URL, params=params, timeout=15)
            results.raise_for_status()
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 429:
                self._busy.start(e.response)
            # Not the error itself: its text has the URL, which carries the API key
            raise ProviderError("Google Books search failed{}{}".format(
                f" ({status})" if status else "", hint)) from None
        val = []
        for result in results.json().get("items", []):
            mr = self._parse_search_result(result=result, generic_cover=generic_cover, locale=locale)
            if mr:
                val.append(mr)
        return val

    def _parse_search_result(
        self, result: Dict, generic_cover: str, locale: str
    ) -> MetaRecord|None:
        volume_info = result.get("volumeInfo", {})
        if "title" not in volume_info:
            return None

        # Google keeps the subtitle apart; the title in full is what a book is usually called
        subtitle = (volume_info.get("subtitle") or "").strip()
        match = MetaRecord(
            id=result["id"],
            title="{}: {}".format(volume_info["title"], subtitle) if subtitle else volume_info["title"],
            authors=volume_info.get("authors", []),
            url=Google.BOOK_URL + result["id"],
            source=MetaSourceInfo(
                id=self.__id__,
                description=Google.DESCRIPTION,
                link=Google.META_URL,
            ),
        )

        match.subtitle = subtitle
        match.cover_max_pixels = Google.COVER_MAX_PIXELS
        match.cover = self._parse_cover(result=result, generic_cover=generic_cover)
        match.description = volume_info.get("description", "")
        match.languages = self._parse_languages(result=result, locale=locale)
        match.publisher = volume_info.get("publisher", "")
        match.publishedDate = self._parse_date(volume_info.get("publishedDate", ""))
        match.rating = volume_info.get("averageRating", 0)
        match.series, match.series_index = "", 1
        match.tags = volume_info.get("categories", [])

        match.identifiers = {"google": match.id}
        match = self._parse_isbn(result=result, match=match)
        return match

    @staticmethod
    def _parse_date(raw: str) -> str:
        """Google gives "2016-05-03", "2016-05" or "2016"; anything else is dropped."""
        for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
            try:
                datetime.strptime(raw, fmt)
                return raw
            except ValueError:
                continue
        return ""

    @staticmethod
    def _parse_isbn(result: Dict, match: MetaRecord) -> MetaRecord:
        identifiers = result["volumeInfo"].get("industryIdentifiers", [])
        for identifier in identifiers:
            if identifier.get("type") == Google.ISBN_TYPE:
                match.identifiers["isbn"] = identifier.get("identifier")
                break
        return match

    @staticmethod
    def _parse_cover(result: Dict, generic_cover: str) -> str:
        if result["volumeInfo"].get("imageLinks"):
            cover_url = result["volumeInfo"]["imageLinks"]["thumbnail"]

            # strip curl in cover
            cover_url = cover_url.replace("&edge=curl", "")

            # request 800x900 cover image (higher resolution)
            cover_url += "&fife=w800-h900"

            return cover_url.replace("http://", "https://")
        return generic_cover

    @staticmethod
    def _parse_languages(result: Dict, locale: str) -> List[str]:
        language_iso2 = result["volumeInfo"].get("language", "")
        languages = (
            [get_language_name(locale, get_lang3(language_iso2))]
            if language_iso2
            else []
        )
        return languages
