# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

# Academic paper metadata. Google Scholar has no API and answers server-side
# scraping with a captcha, so this queries arXiv and Crossref instead. arXiv
# papers are searched by title through DataCite, which registers arXiv's DOIs:
# the arXiv API rate-limits and times out, and Crossref doesn't index preprints.
# Semantic Scholar adds its best title match from journals and conferences.
# arXiv API: https://info.arxiv.org/help/api/user-manual.html
# DataCite API: https://support.datacite.org/docs/api-queries
# Semantic Scholar API: https://api.semanticscholar.org/api-docs/graph
# Crossref API: https://api.crossref.org/swagger-ui/index.html
import re
import html
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from os import getenv
from typing import Dict, List, Optional
from xml.etree import ElementTree

import requests

from cps import logger
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata
from cps.services.identifiers import ARXIV_DOI_PREFIX, ARXIV_ID_RE, DOI_RE, arxiv_id_from_doi

log = logger.create()

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


class google_scholar(Metadata):
    # The id is kept from the old Google Scholar provider so saved
    # provider settings and hierarchies keep working.
    __name__ = "Scholar"
    __id__ = "googlescholar"
    identifier_types = frozenset({"doi", "arxiv"})
    ARXIV_URL = "https://export.arxiv.org/api/query"
    ARXIV_ABS_URL = "https://arxiv.org/abs/"
    DATACITE_URL = "https://api.datacite.org/dois"
    S2_MATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search/match"
    S2_FIELDS = "title,authors,abstract,publicationDate,year,venue,journal,externalIds,fieldsOfStudy,url"
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
        results = self._run([lambda: self._search_arxiv(query),
                             lambda: self._search_semantic_scholar(query),
                             lambda: self._search_crossref(query)])

        # A paper is often in several sources; keep the first of each title
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
        lookups = []
        doi = DOI_RE.search(identifiers.get("doi", ""))
        doi = doi.group(0) if doi else ""
        # An arXiv DOI names the arXiv id; Crossref doesn't know it
        doi_arxiv = arxiv_id_from_doi(doi)
        arxiv_id = ARXIV_ID_RE.search(identifiers.get("arxiv", "") or doi_arxiv)
        if arxiv_id:
            lookups.append(lambda: self._search_arxiv(arxiv_id.group(0)))
        if doi and not doi_arxiv:
            lookups.append(lambda: self._search_crossref(doi))
        return self._run(lookups)

    @staticmethod
    def _run(lookups) -> List[MetaRecord]:
        """Runs the lookups at once and joins their records. Raises only when every
        one failed, so one source being down still shows the other's results and
        an outage is reported as a failed search rather than as no results."""
        if not lookups:
            return []
        records, errors = [], []
        with ThreadPoolExecutor(max_workers=len(lookups)) as pool:
            for future in [pool.submit(lookup) for lookup in lookups]:
                try:
                    records += future.result()
                except Exception as e:
                    errors.append(e)
        if len(errors) == len(lookups):
            raise errors[0]
        for e in errors:
            log.warning("Scholar source failed: %s", e)
        return records

    def _search_arxiv(self, query: str) -> List[MetaRecord]:
        """arXiv papers for an id or text; raises when arXiv can't be reached."""
        arxiv_id = ARXIV_ID_RE.search(query)
        if not arxiv_id:
            return self._search_datacite(query)
        # DataCite answers fastest; a paper from the last day or two isn't there yet
        try:
            records = self._fetch_datacite_doi(arxiv_id.group(1))
            if records:
                return records
        except Exception as e:
            log.warning("DataCite lookup of arXiv %s failed: %s", arxiv_id.group(1), e)
        # The abstract page answers even when the API is slow or down
        try:
            return self._fetch_arxiv_abs(arxiv_id)
        except Exception as e:
            log.warning("arXiv abstract page lookup failed, trying the API: %s", e)
        response = requests.get(
            self.ARXIV_URL, params={"id_list": arxiv_id.group(0)}, headers=self.HEADERS, timeout=15
        )
        response.raise_for_status()
        feed = ElementTree.fromstring(response.content)
        records = []
        for entry in feed.findall(ATOM + "entry"):
            record = self._parse_arxiv_entry(entry)
            if record:
                records.append(record)
        return records

    def _fetch_datacite_doi(self, arxiv_id: str) -> List[MetaRecord]:
        """The paper from DataCite's record of arXiv's DOI; empty when it has none."""
        response = _get(self.DATACITE_URL + "/" + ARXIV_DOI_PREFIX + arxiv_id,
                        headers=self.HEADERS, timeout=10)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        record = self._parse_datacite_hit(response.json().get("data") or {})
        return [record] if record else []

    def _fetch_arxiv_abs(self, arxiv_id) -> List[MetaRecord]:
        """The paper from its abstract page's citation_* meta tags (the ones Google
        Scholar reads); empty when arXiv has no such paper."""
        response = requests.get(
            self.ARXIV_ABS_URL + arxiv_id.group(0), headers=self.HEADERS, timeout=10
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return [self._parse_arxiv_abs(arxiv_id.group(1), response.text)]

    def _parse_arxiv_abs(self, arxiv_id: str, page: str) -> MetaRecord:
        tags = _CitationTags()
        tags.feed(page)
        title = " ".join(tags.first("citation_title").split())
        if not title:
            raise ValueError("no citation_title on the arXiv page for " + arxiv_id)
        # Authors are "Last, First"; the API and other providers give "First Last"
        authors = [" ".join(reversed(a.split(", ", 1))) for a in tags.get("citation_author", [])]
        match = MetaRecord(
            id=arxiv_id,
            title=title,
            authors=authors,
            url="https://arxiv.org/abs/" + arxiv_id,
            source=MetaSourceInfo(
                id=self.__id__, description="arXiv", link="https://arxiv.org/"
            ),
        )
        match.cover = ""
        match.description = " ".join(tags.first("citation_abstract").split())
        match.publisher = "arXiv"
        match.publishedDate = tags.first("citation_date").replace("/", "-")[:10]
        subjects = re.search(r'class="tablecell subjects">(.*?)</td>', page, re.S)
        subjects = re.sub(r"<[^>]+>", "", subjects.group(1)) if subjects else ""
        match.tags = _subject_names(html.unescape(subjects).split(";"))
        match.identifiers = {"arxiv": arxiv_id}
        # The journal's DOI once published, otherwise arXiv's own
        match.identifiers["doi"] = tags.first("citation_doi") or ARXIV_DOI_PREFIX + arxiv_id
        return match

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
        # The journal's DOI once published, otherwise arXiv's own
        match.identifiers["doi"] = entry.findtext(ARXIV_NS + "doi") or ARXIV_DOI_PREFIX + arxiv_id
        return match

    def _search_datacite(self, query: str) -> List[MetaRecord]:
        """arXiv papers whose title matches the text, best first."""
        if not re.search(r"\w", query):
            return []
        params = {
            "query": self._datacite_query(query),
            "client-id": "arxiv.content",
            "sort": "relevance",
            "page[size]": self.MAX_RESULTS,
        }
        response = _get(self.DATACITE_URL, params=params, headers=self.HEADERS, timeout=15)
        response.raise_for_status()
        hits = response.json().get("data", [])
        return [r for r in (self._parse_datacite_hit(h) for h in hits) if r]

    @staticmethod
    def _datacite_query(query: str) -> str:
        """A title search ranking the typed title as a phrase first, then titles
        holding every word in any order."""
        phrase = " ".join(query.split()).replace("\\", "\\\\").replace('"', '\\"')
        # Lower case, so a typed "and" or "not" isn't read as an operator
        words = " AND ".join(w.lower() for w in re.findall(r"\w+", query))
        return 'titles.title:"{}"^5 OR titles.title:({})'.format(phrase, words)

    def _parse_datacite_hit(self, hit: Dict) -> Optional[MetaRecord]:
        attrs = hit.get("attributes", {})
        title = " ".join(((attrs.get("titles") or [{}])[0].get("title") or "").split())
        arxiv_id = next((i.get("identifier") for i in attrs.get("identifiers", [])
                         if i.get("identifierType") == "arXiv"), None)
        arxiv_id = arxiv_id or arxiv_id_from_doi(attrs.get("doi"))
        if not title or not arxiv_id:
            return None
        authors = []
        for creator in attrs.get("creators", []):
            name = " ".join(
                p for p in (creator.get("givenName"), creator.get("familyName")) if p
            ) or creator.get("name")
            if name:
                authors.append(name)
        match = MetaRecord(
            id=arxiv_id,
            title=title,
            authors=authors,
            url="https://arxiv.org/abs/" + arxiv_id,
            source=MetaSourceInfo(
                id=self.__id__, description="arXiv", link="https://arxiv.org/"
            ),
        )
        match.cover = ""
        abstract = next((d.get("description") or "" for d in attrs.get("descriptions", [])
                         if d.get("descriptionType") == "Abstract"), "")
        match.description = " ".join(abstract.split())
        match.publisher = "arXiv"
        # The first version's submission, as the arXiv API's "published"
        submitted = sorted(d.get("date") or "" for d in attrs.get("dates", [])
                           if d.get("dateType") == "Submitted")
        match.publishedDate = submitted[0][:10] if submitted else str(attrs.get("publicationYear") or "")
        match.tags = _subject_names(s.get("subject") or "" for s in attrs.get("subjects", [])
                                    if s.get("subjectScheme") == "arXiv")
        match.identifiers = {"arxiv": arxiv_id}
        # The journal's DOI once published, otherwise arXiv's own
        journal_doi = next((r.get("relatedIdentifier") for r in attrs.get("relatedIdentifiers", [])
                            if r.get("relationType") == "IsVersionOf"
                            and r.get("relatedIdentifierType") == "DOI"), None)
        match.identifiers["doi"] = journal_doi or ARXIV_DOI_PREFIX + arxiv_id
        return match

    def _search_semantic_scholar(self, query: str) -> List[MetaRecord]:
        """Semantic Scholar's closest title match, if it has one."""
        headers = dict(self.HEADERS)
        # Without a key every caller shares one pool, which is often busy
        key = getenv("SEMANTIC_SCHOLAR_API_KEY")
        if key:
            headers["x-api-key"] = key
        params = {"query": query, "fields": self.S2_FIELDS}
        response = _get(self.S2_MATCH_URL, params=params, headers=headers, timeout=10)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        hits = response.json().get("data", [])
        return [r for r in (self._parse_semantic_scholar(h) for h in hits) if r]

    def _parse_semantic_scholar(self, hit: Dict) -> Optional[MetaRecord]:
        title = " ".join((hit.get("title") or "").split())
        if not title or not hit.get("paperId"):
            return None
        match = MetaRecord(
            id=hit["paperId"],
            title=title,
            authors=[a.get("name") for a in hit.get("authors") or [] if a.get("name")],
            url=hit.get("url") or "https://www.semanticscholar.org/paper/" + hit["paperId"],
            source=MetaSourceInfo(
                id=self.__id__, description="Semantic Scholar",
                link="https://www.semanticscholar.org/",
            ),
        )
        match.cover = ""
        match.description = " ".join((hit.get("abstract") or "").split())
        match.publisher = (hit.get("journal") or {}).get("name") or hit.get("venue") or ""
        match.publishedDate = hit.get("publicationDate") or str(hit.get("year") or "")
        match.tags = hit.get("fieldsOfStudy") or []
        ids = hit.get("externalIds") or {}
        match.identifiers = {}
        if ids.get("ArXiv"):
            match.identifiers["arxiv"] = ids["ArXiv"]
        # The journal's DOI once published, otherwise arXiv's own
        doi = ids.get("DOI") or (ARXIV_DOI_PREFIX + ids["ArXiv"] if ids.get("ArXiv") else "")
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
        # A contact address moves the requests to Crossref's less crowded "polite" pool
        if getenv("CROSSREF_MAILTO"):
            params["mailto"] = getenv("CROSSREF_MAILTO")
        response = _get(self.CROSSREF_URL, params=params, headers=self.HEADERS, timeout=15)
        response.raise_for_status()
        items = response.json().get("message", {}).get("items", [])
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


def _get(url, **kwargs):
    """requests.get, asking once more after a pause when the service says it's busy (429)."""
    response = requests.get(url, **kwargs)
    if response.status_code == 429:
        time.sleep(1.5)
        response = requests.get(url, **kwargs)
    return response


def _subject_names(subjects) -> List[str]:
    """arXiv's "Computation and Language (cs.CL)" subjects as their names, once each."""
    names: List[str] = []
    for subject in subjects:
        name = re.sub(r"\s*\([^()]*\)\s*$", "", " ".join(subject.split()))
        if name and name not in names:
            names.append(name)
    return names


class _CitationTags(HTMLParser):
    """Collects a page's <meta name="citation_*" content="..."> tags."""

    def __init__(self):
        super().__init__()
        self.tags: Dict[str, List[str]] = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        name = attrs.get("name") or ""
        if tag == "meta" and name.startswith("citation_"):
            self.tags.setdefault(name, []).append(attrs.get("content") or "")

    def get(self, name, default=None):
        return self.tags.get(name, default)

    def first(self, name) -> str:
        return (self.tags.get(name) or [""])[0]
