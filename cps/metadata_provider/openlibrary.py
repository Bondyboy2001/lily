# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

# Open Library: free, no key. https://openlibrary.org/developers/api
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional

import requests

from cps import logger
from cps.isoLanguages import get_language_name
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata

log = logger.create()

SEARCH_FIELDS = "key,title,subtitle,author_name,first_publish_year,cover_i,subject,language"


class OpenLibrary(Metadata):
    __name__ = "Open Library"
    __id__ = "openlibrary"
    identifier_types = frozenset({"isbn"})
    BASE_URL = "https://openlibrary.org"
    COVER_URL = "https://covers.openlibrary.org/b/id/{}-L.jpg"
    HEADERS = {"User-Agent": "Lily/1.0 (metadata lookup)"}
    MAX_RESULTS = 5

    def search(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> Optional[List[MetaRecord]]:
        if not self.active or not query.strip():
            return []
        docs = self._search_docs({"q": query, "limit": self.MAX_RESULTS})
        records = [self._parse_doc(d, generic_cover, locale) for d in docs]
        records = [r for r in records if r]
        self._add_descriptions(records)
        return records

    def search_identifiers(
        self, identifiers: Dict[str, str], generic_cover: str = "", locale: str = "en"
    ) -> List[MetaRecord]:
        isbn = identifiers.get("isbn")
        if not self.active or not isbn:
            return []
        # The work (authors, subjects) and the edition (publisher, date) come separately
        with ThreadPoolExecutor(max_workers=2) as pool:
            docs = pool.submit(self._search_docs, {"isbn": isbn, "limit": 1})
            edition = pool.submit(self._get_json, "/isbn/{}.json".format(isbn))
            docs, edition = docs.result(), edition.result() or {}
        if not docs:
            return []
        record = self._parse_doc(docs[0], generic_cover, locale)
        if not record:
            return []
        record.identifiers["isbn"] = isbn
        record.publisher = (edition.get("publishers") or [""])[0]
        record.publishedDate = self._parse_date(edition.get("publish_date")) or record.publishedDate
        if edition.get("covers"):
            record.cover = self.COVER_URL.format(edition["covers"][0])
        self._add_descriptions([record])
        return [record]

    def _get_json(self, path: str, params: Optional[Dict] = None) -> Optional[Dict]:
        try:
            for attempt in range(2):
                response = requests.get(
                    self.BASE_URL + path, params=params, headers=self.HEADERS, timeout=15
                )
                # Busy (Rebuild metadata asks for several books at once): ask once more after a pause
                if response.status_code != 429 or attempt:
                    break
                time.sleep(2)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            log.warning("Open Library request %s failed: %s", path, e)
            return None

    def _search_docs(self, params: Dict) -> List[Dict]:
        data = self._get_json("/search.json", dict(params, fields=SEARCH_FIELDS))
        return (data or {}).get("docs", [])

    def _parse_doc(self, doc: Dict, generic_cover: str, locale: str) -> Optional[MetaRecord]:
        title = doc.get("title")
        key = doc.get("key")  # "/works/OL45883W"
        if not title or not key:
            return None
        if doc.get("subtitle"):
            title = "{}: {}".format(title, doc["subtitle"])
        work_id = key.rsplit("/", 1)[-1]
        match = MetaRecord(
            id=work_id,
            title=title,
            authors=doc.get("author_name", []),
            url=self.BASE_URL + key,
            source=MetaSourceInfo(
                id=self.__id__, description=self.__name__, link=self.BASE_URL + "/"
            ),
        )
        match.cover = (
            self.COVER_URL.format(doc["cover_i"]) if doc.get("cover_i") else generic_cover
        )
        year = doc.get("first_publish_year")
        match.publishedDate = "{:04d}-01-01".format(year) if year else ""
        match.tags = doc.get("subject", [])[:10]
        match.languages = self._parse_languages(doc.get("language", []), locale)
        match.identifiers = {"openlibrary": work_id}
        return match

    @staticmethod
    def _parse_languages(codes: List[str], locale) -> List[str]:
        # A work lists every language any edition appeared in; only a single one is telling
        if len(codes) != 1:
            return []
        try:
            name = get_language_name(locale, codes[0])
        except Exception:
            return []
        return [name] if name and name != "Unknown" else []

    def _add_descriptions(self, records: List[MetaRecord]) -> None:
        # Search results carry no description; each work has its own
        def fetch(record):
            work = self._get_json("/works/{}.json".format(record.id)) or {}
            description = work.get("description") or ""
            if isinstance(description, dict):
                description = description.get("value", "")
            record.description = description

        if records:
            with ThreadPoolExecutor(max_workers=len(records)) as pool:
                list(pool.map(fetch, records))

    @staticmethod
    def _parse_date(raw: Optional[str]) -> str:
        if not raw:
            return ""
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%B %Y", "%b %Y", "%Y"):
            try:
                return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        year = re.search(r"\b(1\d|20)\d{2}\b", raw)
        return "{}-01-01".format(year.group(0)) if year else ""
