# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

# Academic paper metadata. Google Scholar has no API and answers server-side
# scraping with a captcha, so this queries arXiv and Crossref instead.
# arXiv API: https://info.arxiv.org/help/api/user-manual.html
# Crossref API: https://api.crossref.org/swagger-ui/index.html
import re
import html
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional
from xml.etree import ElementTree

import requests

from cps import logger
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata

log = logger.create()

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
DOI_RE = re.compile(r"10\.\d{4,9}/\S+")


class google_scholar(Metadata):
    # The id is kept from the old Google Scholar provider so saved
    # provider settings and hierarchies keep working.
    __name__ = "Scholar"
    __id__ = "googlescholar"
    ARXIV_URL = "https://export.arxiv.org/api/query"
    CROSSREF_URL = "https://api.crossref.org/works"
    # arXiv answers 406 when brotli/zstd are offered, which requests does
    # whenever those packages are installed
    HEADERS = {"User-Agent": "Lily/1.0 (metadata lookup)", "Accept-Encoding": "gzip"}
    MAX_RESULTS = 5

    def search(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> Optional[List[MetaRecord]]:
        if not self.active or not query.strip():
            return []
        # A bare arXiv id means nothing to Crossref's text search
        if ARXIV_ID_RE.fullmatch(query.strip()):
            return self._search_arxiv(query)
        with ThreadPoolExecutor(max_workers=2) as pool:
            arxiv = pool.submit(self._search_arxiv, query)
            crossref = pool.submit(self._search_crossref, query)
            results = arxiv.result() + crossref.result()

        # arXiv preprints are often also in Crossref; keep the first of each title
        seen = set()
        val = []
        for record in results:
            key = re.sub(r"\W+", "", record.title.lower())
            if key not in seen:
                seen.add(key)
                val.append(record)
        return val

    def search_identifiers(
        self, identifiers: Dict[str, str], generic_cover: str = "", locale: str = "en"
    ) -> List[MetaRecord]:
        if not self.active:
            return []
        records = []
        arxiv_id = ARXIV_ID_RE.search(identifiers.get("arxiv", ""))
        if arxiv_id:
            records += self._search_arxiv(arxiv_id.group(0))
        doi = DOI_RE.search(identifiers.get("doi", ""))
        if doi:
            records += self._search_crossref(doi.group(0))
        return records

    def _search_arxiv(self, query: str) -> List[MetaRecord]:
        arxiv_id = ARXIV_ID_RE.search(query)
        if arxiv_id:
            params = {"id_list": arxiv_id.group(0)}
        else:
            words = re.findall(r"\w+", query)
            if not words:
                return []
            params = {
                "search_query": " AND ".join("all:" + w for w in words),
                "max_results": self.MAX_RESULTS,
            }
        try:
            response = requests.get(
                self.ARXIV_URL, params=params, headers=self.HEADERS, timeout=15
            )
            response.raise_for_status()
            feed = ElementTree.fromstring(response.content)
        except Exception as e:
            log.warning("arXiv search failed: %s", e)
            return []
        records = []
        for entry in feed.findall(ATOM + "entry"):
            record = self._parse_arxiv_entry(entry)
            if record:
                records.append(record)
        return records

    def _parse_arxiv_entry(self, entry) -> Optional[MetaRecord]:
        title = " ".join((entry.findtext(ATOM + "title") or "").split())
        abs_url = entry.findtext(ATOM + "id") or ""
        arxiv_id = ARXIV_ID_RE.search(abs_url)
        if not title or not arxiv_id:
            return None
        arxiv_id = arxiv_id.group(1)
        match = MetaRecord(
            id=arxiv_id,
            title=title,
            authors=[
                a.findtext(ATOM + "name") for a in entry.findall(ATOM + "author")
            ],
            url="https://arxiv.org/abs/" + arxiv_id,
            source=MetaSourceInfo(
                id=self.__id__, description="arXiv", link="https://arxiv.org/"
            ),
        )
        match.cover = ""
        match.description = " ".join((entry.findtext(ATOM + "summary") or "").split())
        match.publisher = "arXiv"
        match.publishedDate = (entry.findtext(ATOM + "published") or "")[:10]
        match.tags = [
            c.get("term") for c in entry.findall(ATOM + "category") if c.get("term")
        ]
        match.identifiers = {"arxiv": arxiv_id}
        doi = entry.findtext(ARXIV_NS + "doi")
        if doi:
            match.identifiers["doi"] = doi
        return match

    def _search_crossref(self, query: str) -> List[MetaRecord]:
        doi = DOI_RE.search(query)
        params = {
            "rows": self.MAX_RESULTS,
            "select": "DOI,title,author,publisher,container-title,issued,abstract,subject,URL",
        }
        if doi:
            params["filter"] = "doi:" + doi.group(0)
        else:
            params["query.bibliographic"] = query
        try:
            response = requests.get(
                self.CROSSREF_URL, params=params, headers=self.HEADERS, timeout=15
            )
            response.raise_for_status()
            items = response.json().get("message", {}).get("items", [])
        except Exception as e:
            log.warning("Crossref search failed: %s", e)
            return []
        return [r for r in (self._parse_crossref_item(i) for i in items) if r]

    def _parse_crossref_item(self, item: Dict) -> Optional[MetaRecord]:
        title = " ".join((item.get("title") or [""])[0].split())
        doi = item.get("DOI")
        if not title or not doi:
            return None
        authors = []
        for author in item.get("author", []):
            name = " ".join(
                p for p in (author.get("given"), author.get("family")) if p
            ) or author.get("name")
            if name:
                authors.append(name)
        match = MetaRecord(
            id=doi,
            title=title,
            authors=authors,
            url=item.get("URL") or "https://doi.org/" + doi,
            source=MetaSourceInfo(
                id=self.__id__, description="Crossref", link="https://www.crossref.org/"
            ),
        )
        match.cover = ""
        # Abstracts come as JATS XML; strip the tags
        abstract = re.sub(r"<[^>]+>", " ", item.get("abstract") or "")
        match.description = " ".join(html.unescape(abstract).split())
        match.publisher = (item.get("container-title") or [None])[0] or item.get(
            "publisher", ""
        )
        parts = (item.get("issued", {}).get("date-parts") or [[]])[0]
        if parts and parts[0]:
            parts = list(parts) + [1] * (3 - len(parts))
            match.publishedDate = "{:04d}-{:02d}-{:02d}".format(*parts[:3])
        match.tags = item.get("subject", [])
        match.identifiers = {"doi": doi}
        return match
