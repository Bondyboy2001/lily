# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

# Open Library: free, no key. https://openlibrary.org/developers/api
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime


from cps import logger
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata, get_patiently

log = logger.create()

SEARCH_FIELDS = "key,title,subtitle,author_name,first_publish_year,cover_i"


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
    ) -> list[MetaRecord] | None:
        records = self.search_titles(query, generic_cover, locale)
        self._add_descriptions(records)
        return records

    def search_titles(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> list[MetaRecord]:
        """search() without the descriptions, which are a request each: a lookup that applies
        one exact match finds it by title and authors, then calls complete() on it alone."""
        if not query.strip():
            return []
        docs = self._search_docs({"q": query, "limit": self.MAX_RESULTS})
        records = [self._parse_doc(d, generic_cover, locale) for d in docs]
        return [r for r in records if r]

    def complete(self, record: MetaRecord) -> MetaRecord:
        self._add_descriptions([record])
        return record

    def search_identifiers(
        self, identifiers: dict[str, str], generic_cover: str = "", locale: str = "en"
    ) -> list[MetaRecord]:
        isbn = identifiers.get("isbn")
        if not isbn:
            return []
        # The work (authors) and the edition (date, its own cover) come separately
        with ThreadPoolExecutor(max_workers=2) as pool:
            docs = pool.submit(self._search_docs, {"isbn": isbn, "limit": 1})
            edition = pool.submit(self._get_json, f"/isbn/{isbn}.json")
            docs, edition = docs.result(), edition.result() or {}
        if not docs:
            return []
        record = self._parse_doc(docs[0], generic_cover, locale)
        if not record:
            return []
        record.identifiers["isbn"] = isbn
        record.publishedDate = self._parse_date(edition.get("publish_date")) or record.publishedDate
        if edition.get("covers"):
            record.cover = self.COVER_URL.format(edition["covers"][0])
        self._add_descriptions([record])
        return [record]

    def _request(self, path: str, params: dict | None = None) -> dict:
        # Busy is likely: Rebuild metadata asks for several books at once
        response = get_patiently(self.BASE_URL + path, pause=2, params=params, headers=self.HEADERS, timeout=15)
        response.raise_for_status()
        return response.json()

    def _get_json(self, path: str, params: dict | None = None) -> dict | None:
        """A detail of a record already found (edition, description): None when it can't be had."""
        try:
            return self._request(path, params)
        except Exception as e:
            log.warning("Open Library request %s failed: %s", path, e)
            return None

    def _search_docs(self, params: dict) -> list[dict]:
        """The search itself: a failure is raised, not passed off as no results."""
        return self._request("/search.json", dict(params, fields=SEARCH_FIELDS)).get("docs", [])

    def _parse_doc(self, doc: dict, generic_cover: str, locale: str) -> MetaRecord | None:
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
        match.subtitle = doc.get("subtitle") or ""
        match.cover = (
            self.COVER_URL.format(doc["cover_i"]) if doc.get("cover_i") else generic_cover
        )
        year = doc.get("first_publish_year")
        match.publishedDate = f"{year:04d}-01-01" if year else ""
        match.identifiers = {"openlibrary": work_id}
        return match

    def _add_descriptions(self, records: list[MetaRecord]) -> None:
        # Search results carry no description; each work has its own
        def fetch(record):
            work = self._get_json(f"/works/{record.id}.json") or {}
            description = work.get("description") or ""
            if isinstance(description, dict):
                description = description.get("value", "")
            record.description = description

        if records:
            with ThreadPoolExecutor(max_workers=len(records)) as pool:
                list(pool.map(fetch, records))

    @staticmethod
    def _parse_date(raw: str | None) -> str:
        if not raw:
            return ""
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%B %Y", "%b %Y", "%Y"):
            try:
                return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        year = re.search(r"\b(1\d|20)\d{2}\b", raw)
        return f"{year.group(0)}-01-01" if year else ""
