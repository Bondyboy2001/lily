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
# Crossref also has books and their chapters, with ISBNs but no covers: those
# come from Open Library by the ISBN, else Google Books.
# arXiv API: https://info.arxiv.org/help/api/user-manual.html
# DataCite API: https://support.datacite.org/docs/api-queries
# Semantic Scholar API: https://api.semanticscholar.org/api-docs/graph
# Crossref API: https://api.crossref.org/swagger-ui/index.html
import hashlib
import re
import html
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from os import getenv
from xml.etree import ElementTree


from cps import logger
from cps.services.Metadata import CoolOff, MetaRecord, MetaSourceInfo, Metadata, get_patiently, http_session
from cps.services.identifiers import ARXIV_DOI_PREFIX, ARXIV_ID_RE, DOI_RE, arxiv_id_from_doi, compact_isbn

log = logger.create()

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


class google_scholar(Metadata):
    # The old Google Scholar provider's id: the edit page knows papers by it (get_meta.js)
    __name__ = "Scholar"
    __id__ = "googlescholar"
    identifier_types = frozenset({"doi", "arxiv"})
    ARXIV_URL = "https://export.arxiv.org/api/query"
    ARXIV_ABS_URL = "https://arxiv.org/abs/"
    DATACITE_URL = "https://api.datacite.org/dois"
    S2_MATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search/match"
    S2_FIELDS = "title,authors,abstract,publicationDate,year,externalIds,url"
    CROSSREF_URL = "https://api.crossref.org/works"
    # Crossref's types for a whole book, whose ISBN is the record's own; a chapter's
    # ISBN is its book's, good for a cover but not an identifier
    CROSSREF_BOOKS = frozenset({"book", "monograph", "edited-book", "reference-book"})
    # The edition with that ISBN, whose cover is its own, not its work's (often another edition's)
    OPENLIBRARY_EDITION_URL = "https://openlibrary.org/isbn/{}.json"
    OPENLIBRARY_COVER_URL = "https://covers.openlibrary.org/b/id/{}-L.jpg"
    # Google Books' cover of the book with an ISBN, served apart from its API and its daily quota
    GOOGLE_COVER_URL = "https://books.google.com/books/content?vid=ISBN{}&printsec=frontcover&img=1&zoom=3"
    # The "image not available" picture it serves for a book it has no cover of
    GOOGLE_NO_COVER_MD5 = "a64fa89d7ebc97075c1d363fc5fea71f"
    # arXiv answers 406 when brotli/zstd are offered, which requests does
    # whenever those packages are installed
    HEADERS = {"User-Agent": "Lily/1.0 (metadata lookup)", "Accept-Encoding": "gzip"}
    MAX_RESULTS = 5

    def __init__(self):
        # Set when Semantic Scholar stays busy: its keyless pool often is
        self._s2_busy = CoolOff()

    def search(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> list[MetaRecord] | None:
        return self._search(query, covers=True)

    def search_titles(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> list[MetaRecord] | None:
        # A lookup applies one record: complete() fetches its book's cover, not every result's
        return self._search(query, covers=False)

    def complete(self, record: MetaRecord) -> MetaRecord:
        isbn = getattr(record, "_cover_isbn", "")
        if isbn:
            record.cover = self._book_cover(isbn)
            record._cover_isbn = ""
        return record

    def _search(self, query: str, covers: bool) -> list[MetaRecord]:
        if not query.strip():
            return []
        # A bare arXiv id means nothing to Crossref's text search
        if ARXIV_ID_RE.fullmatch(query.strip()):
            return self._search_arxiv(query)
        results = self._run([lambda: self._search_arxiv(query),
                             lambda: self._search_semantic_scholar(query),
                             lambda: self._search_crossref(query, covers)])

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
        self, identifiers: dict[str, str], generic_cover: str = "", locale: str = "en"
    ) -> list[MetaRecord]:
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
    def _run(lookups) -> list[MetaRecord]:
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

    def _search_arxiv(self, query: str) -> list[MetaRecord]:
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
        response = http_session.get(
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

    def _fetch_datacite_doi(self, arxiv_id: str) -> list[MetaRecord]:
        """The paper from DataCite's record of arXiv's DOI; empty when it has none."""
        response = get_patiently(self.DATACITE_URL + "/" + ARXIV_DOI_PREFIX + arxiv_id,
                                 headers=self.HEADERS, timeout=10)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        record = self._parse_datacite_hit(response.json().get("data") or {})
        return [record] if record else []

    def _fetch_arxiv_abs(self, arxiv_id) -> list[MetaRecord]:
        """The paper from its abstract page's citation_* meta tags (the ones Google
        Scholar reads); empty when arXiv has no such paper."""
        response = http_session.get(
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
        match.publishedDate = tags.first("citation_date").replace("/", "-")[:10]
        match.identifiers = {"arxiv": arxiv_id}
        # The journal's DOI once published, otherwise arXiv's own
        match.identifiers["doi"] = tags.first("citation_doi") or ARXIV_DOI_PREFIX + arxiv_id
        return match

    def _parse_arxiv_entry(self, entry) -> MetaRecord | None:
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
        match.publishedDate = (entry.findtext(ATOM + "published") or "")[:10]
        match.identifiers = {"arxiv": arxiv_id}
        # The journal's DOI once published, otherwise arXiv's own
        match.identifiers["doi"] = entry.findtext(ARXIV_NS + "doi") or ARXIV_DOI_PREFIX + arxiv_id
        return match

    def _search_datacite(self, query: str) -> list[MetaRecord]:
        """arXiv papers whose title matches the text, best first."""
        if not re.search(r"\w", query):
            return []
        params = {
            "query": self._datacite_query(query),
            "client-id": "arxiv.content",
            "sort": "relevance",
            "page[size]": self.MAX_RESULTS,
        }
        response = get_patiently(self.DATACITE_URL, params=params, headers=self.HEADERS, timeout=15)
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
        return f'titles.title:"{phrase}"^5 OR titles.title:({words})'

    def _parse_datacite_hit(self, hit: dict) -> MetaRecord | None:
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
        # The first version's submission, as the arXiv API's "published"
        submitted = sorted(d.get("date") or "" for d in attrs.get("dates", [])
                           if d.get("dateType") == "Submitted")
        match.publishedDate = submitted[0][:10] if submitted else str(attrs.get("publicationYear") or "")
        match.identifiers = {"arxiv": arxiv_id}
        # The journal's DOI once published, otherwise arXiv's own
        journal_doi = next((r.get("relatedIdentifier") for r in attrs.get("relatedIdentifiers", [])
                            if r.get("relationType") == "IsVersionOf"
                            and r.get("relatedIdentifierType") == "DOI"), None)
        match.identifiers["doi"] = journal_doi or ARXIV_DOI_PREFIX + arxiv_id
        return match

    def _search_semantic_scholar(self, query: str) -> list[MetaRecord]:
        """Semantic Scholar's closest title match, if it has one. Still busy after the second
        try, it is left out of the searches of the next minute."""
        if self._s2_busy.active():
            return []
        headers = dict(self.HEADERS)
        # Without a key every caller shares one pool, which is often busy
        key = getenv("SEMANTIC_SCHOLAR_API_KEY")
        if key:
            headers["x-api-key"] = key
        params = {"query": query, "fields": self.S2_FIELDS}
        response = get_patiently(self.S2_MATCH_URL, params=params, headers=headers, timeout=10)
        if response.status_code == 404:
            return []
        if response.status_code == 429:
            self._s2_busy.start(response)
        response.raise_for_status()
        hits = response.json().get("data", [])
        return [r for r in (self._parse_semantic_scholar(h) for h in hits) if r]

    def _parse_semantic_scholar(self, hit: dict) -> MetaRecord | None:
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
        match.publishedDate = hit.get("publicationDate") or str(hit.get("year") or "")
        ids = hit.get("externalIds") or {}
        match.identifiers = {}
        if ids.get("ArXiv"):
            match.identifiers["arxiv"] = ids["ArXiv"]
        # The journal's DOI once published, otherwise arXiv's own
        doi = ids.get("DOI") or (ARXIV_DOI_PREFIX + ids["ArXiv"] if ids.get("ArXiv") else "")
        if doi:
            match.identifiers["doi"] = doi
        return match

    def _search_crossref(self, query: str, covers: bool = True) -> list[MetaRecord]:
        doi = DOI_RE.search(query)
        params = {
            "rows": self.MAX_RESULTS,
            "select": "DOI,title,author,issued,abstract,URL,type,ISBN",
        }
        if doi:
            params["filter"] = "doi:" + doi.group(0)
        else:
            params["query.bibliographic"] = query
        # A contact address moves the requests to Crossref's less crowded "polite" pool
        if getenv("CROSSREF_MAILTO"):
            params["mailto"] = getenv("CROSSREF_MAILTO")
        response = get_patiently(self.CROSSREF_URL, params=params, headers=self.HEADERS, timeout=15)
        response.raise_for_status()
        items = response.json().get("message", {}).get("items", [])
        parsed = [(self._parse_crossref_item(item), _crossref_isbn(item)) for item in items]
        # Books and chapters have their book's ISBN, and so a cover; without covers, complete()
        # looks up the one of the record a lookup applies
        wanted = [(record, isbn) for record, isbn in parsed if record and isbn]
        if covers:
            self._add_book_covers(wanted)
        else:
            for record, isbn in wanted:
                record._cover_isbn = isbn
        return [record for record, __ in parsed if record]

    def _add_book_covers(self, wanted) -> None:
        """Give each (record, ISBN) the cover of the book with that ISBN, looked up at once."""
        if not wanted:
            return
        with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
            covers = pool.map(lambda pair: self._book_cover(pair[1]), wanted)
            for (record, __), cover in zip(wanted, covers):
                record.cover = cover

    def _book_cover(self, isbn: str) -> str:
        """The cover of the book with this ISBN: Open Library's, else Google Books' (from its
        API, or by the cover's own address when the API has used the day's quota); '' for none.
        A failure is no cover, never a failed search."""
        try:
            response = http_session.get(self.OPENLIBRARY_EDITION_URL.format(isbn), headers=self.HEADERS, timeout=10)
            if response.status_code != 404:  # Open Library doesn't have that edition
                response.raise_for_status()
                covers = [c for c in response.json().get("covers") or [] if isinstance(c, int) and c > 0]
                if covers:
                    return self.OPENLIBRARY_COVER_URL.format(covers[0])
        except Exception as e:
            log.debug("No Open Library cover for ISBN %s: %s", isbn, e)
        try:
            # The shared provider, so its API key and its pause after a 429 apply
            from cps.search_metadata import cl
            google = next(p for p in cl if p.__id__ == "google")
            for record in google.search_identifiers({"isbn": isbn}, "", "en") or []:
                if record.cover:
                    return record.cover
        except Exception as e:
            log.debug("No Google Books cover for ISBN %s: %s", isbn, e)
        try:
            url = self.GOOGLE_COVER_URL.format(isbn)
            response = http_session.get(url, headers=self.HEADERS, timeout=10)
            response.raise_for_status()
            if response.content and hashlib.md5(response.content).hexdigest() != self.GOOGLE_NO_COVER_MD5:
                return url
        except Exception as e:
            log.debug("No Google Books cover at its address for ISBN %s: %s", isbn, e)
        return ""

    def _parse_crossref_item(self, item: dict) -> MetaRecord | None:
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
        parts = (item.get("issued", {}).get("date-parts") or [[]])[0]
        if parts and parts[0]:
            parts = list(parts) + [1] * (3 - len(parts))
            match.publishedDate = "{:04d}-{:02d}-{:02d}".format(*parts[:3])
        match.identifiers = {"doi": doi}
        isbn = _crossref_isbn(item)
        if isbn and item.get("type") in self.CROSSREF_BOOKS:
            match.identifiers["isbn"] = isbn
        return match


def _crossref_isbn(item: dict) -> str:
    """The first ISBN Crossref gives for a work, compacted; '' for none (a journal's article)."""
    return compact_isbn((item.get("ISBN") or [""])[0])


class _CitationTags(HTMLParser):
    """Collects a page's <meta name="citation_*" content="..."> tags."""

    def __init__(self):
        super().__init__()
        self.tags: dict[str, list[str]] = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        name = attrs.get("name") or ""
        if tag == "meta" and name.startswith("citation_"):
            self.tags.setdefault(name, []).append(attrs.get("content") or "")

    def get(self, name, default=None):
        return self.tags.get(name, default)

    def first(self, name) -> str:
        return (self.tags.get(name) or [""])[0]
