# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

# Version from AutoCaliWeb - Optimized by - gelbphoenix & UsamaFoad
# Hardcover API: https://docs.hardcover.app/api/getting-started/
import json
from os import getenv
from typing import Dict, List, Optional

import requests

from cps import config, constants, logger
from cps.isoLanguages import get_language_name
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata

log = logger.create()


class Hardcover(Metadata):
    __name__ = "Hardcover"
    __id__ = "hardcover"
    identifier_types = frozenset({"hardcover-id"})
    DESCRIPTION = "Hardcover"
    META_URL = "https://hardcover.app/"
    BASE_URL = "https://api.hardcover.app/v1/graphql"
    HEADERS = {
        "Content-Type": "application/json",
        "User-Agent": constants.USER_AGENT,
    }
    # reading_format_id mapping (indices observed in API). Leave blanks for unknowns to keep indices aligned.
    FORMATS = ["", "Physical Book", "", "", "E-Book"]

    # Results is a scalar JSON string; we decode client-side.
    SEARCH_QUERY = (
        "query SearchBooks($query: String!) { "
        "search(query: $query, query_type: \"Book\", per_page: 50) { results } "
        "}"
    )

    EDITION_QUERY = (
        "query BookEditions($query: Int!) { "
        "books(where: {id: {_eq: $query}}) { "
        "  id slug description "
        "  book_series { position series { name } } "
        "  cached_tags(path: \"Genre\") "
        "  editions { "
        "    id title release_date isbn_13 isbn_10 reading_format_id "
        "    image { url } "
        "    language { code3 } "
        "    publisher { name } "
        "    contributions { author { name } } "
        "  } "
        "} }"
    )

    def search_identifiers(
        self, identifiers: Dict[str, str], generic_cover: str = "", locale: str = "en"
    ) -> List[MetaRecord]:
        """A Hardcover book id: that book's editions."""
        book_id = str(identifiers.get("hardcover-id", "")).strip()
        if not book_id.isdigit():
            return []
        data = self._query(Hardcover.EDITION_QUERY, int(book_id))
        try:
            books = self._safe_get(data, "data", "books", default=[]) if data else []
            return self._parse_edition_results(result=books[0], generic_cover=generic_cover,
                                               locale=locale) if books else []
        except Exception as e:
            log.warning(f"Error processing results: {e}")
            return []

    def search(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> Optional[List[MetaRecord]]:
        data = self._query(Hardcover.SEARCH_QUERY, query)
        if not data:
            return []
        val: List[MetaRecord] = []
        try:
            raw_results = self._safe_get(data, "data", "search", "results", default=[])
            if isinstance(raw_results, str):
                try:
                    parsed = json.loads(raw_results)
                except Exception:
                    parsed = []
            else:
                parsed = raw_results

            for hit in self._safe_get(parsed, "hits", default=[]):
                match = self._parse_title_result(result=hit, generic_cover=generic_cover, locale=locale)
                if match:
                    val.append(match)
        except Exception as e:
            log.warning(f"Error processing results: {e}")
            return []
        return val

    def _query(self, gql: str, variable) -> Optional[Dict]:
        """Runs a GraphQL query. None on any failure, and without a token (Hardcover
        is off until HARDCOVER_TOKEN is set), quietly: a library rebuild asks once a book."""
        token = getattr(config, "config_hardcover_token", None) or getenv("HARDCOVER_TOKEN")
        if not token:
            log.debug("Hardcover skipped: no HARDCOVER_TOKEN")
            return None
        # Its own headers: the class's are shared by every search running at once
        headers = dict(Hardcover.HEADERS, Authorization="Bearer " + token.replace("Bearer ", ""))
        try:
            resp = requests.post(
                Hardcover.BASE_URL,
                json={"query": gql, "variables": {"query": variable}},
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            response_data = resp.json()
        except requests.exceptions.RequestException as e:
            log.warning(f"HTTP request failed: {e}")
            return None
        except ValueError as e:
            log.warning(f"JSON parsing failed: {e}")
            return None
        except Exception as e:
            log.warning(f"Unexpected error: {e}")
            return None
        if "errors" in response_data:
            log.error(f"GraphQL errors: {response_data['errors']}")
            return None
        if "data" not in response_data:
            log.warning("Invalid response structure: missing 'data' field")
            return None
        return response_data

    def _parse_title_result(
        self, result: Dict, generic_cover: str, locale: str
    ) -> Optional[MetaRecord]:
        try:
            document = self._safe_get(result, "document", default={})
            if not document:
                return None

            series_info = self._safe_get(document, "featured_series", default={})
            series = self._safe_get(document, "featured_series", "series", "name", default="")
            series_index = self._safe_get(series_info, "position", default="")

            match = MetaRecord(
                id=self._safe_get(document, "id", default=""),
                title=self._safe_get(document, "title", default=""),
                authors=self._safe_get(document, "author_names", default=[]),
                url=self._parse_title_url(result, ""),
                source=MetaSourceInfo(
                    id=self.__id__,
                    description=Hardcover.DESCRIPTION,
                    link=Hardcover.META_URL,
                ),
                series=series,
            )

            image_data = self._safe_get(document, "image", default={})
            match.cover = self._safe_get(image_data, "url", default=generic_cover)

            match.description = self._safe_get(document, "description", default="")
            match.publishedDate = self._safe_get(document, "release_date", default="")
            match.series_index = series_index
            match.tags = self._safe_get(document, "genres", default=[])
            match.identifiers = {
                "hardcover-id": match.id,
                "hardcover-slug": self._safe_get(document, "slug", default=""),
            }
            return match
        except Exception as e:
            log.warning(f"Error parsing title result: {e}")
            return None

    def _parse_edition_results(
        self, result: Dict, generic_cover: str, locale: str
    ) -> List[MetaRecord]:
        editions: List[MetaRecord] = []
        book_id = result.get("id", "")
        for edition in result.get("editions", []) or []:
            match = MetaRecord(
                id=book_id,
                title=edition.get("title", ""),
                authors=self._parse_edition_authors(edition, []),
                url=self._parse_edition_url(result, edition, ""),
                source=MetaSourceInfo(
                    id=self.__id__,
                    description=Hardcover.DESCRIPTION,
                    link=Hardcover.META_URL,
                ),
                series=(result.get("book_series") or [{}])[0].get("series", {}).get("name", ""),
            )
            match.cover = (edition.get("image") or {}).get("url", generic_cover)
            match.description = result.get("description", "")
            match.publisher = (edition.get("publisher") or {}).get("name", "")
            match.publishedDate = edition.get("release_date", "")
            match.series_index = (result.get("book_series") or [{}])[0].get("position", "")
            match.tags = self._parse_tags(result, [])
            match.languages = self._parse_languages(edition, locale)
            match.identifiers = {
                "hardcover-id": book_id,
                "hardcover-slug": result.get("slug", ""),
                "hardcover-edition": edition.get("id", ""),
            }
            isbn = edition.get("isbn_13", edition.get("isbn_10"))
            if isbn:
                match.identifiers["isbn"] = isbn
            rf_id = edition.get("reading_format_id")
            if isinstance(rf_id, int) and 0 <= rf_id < len(Hardcover.FORMATS):
                match.format = Hardcover.FORMATS[rf_id]
            else:
                match.format = ""
            editions.append(match)
        return editions

    @staticmethod
    def _parse_title_url(result: Dict, url: str) -> str:
        document = result.get("document", {})
        hardcover_slug = document.get("slug", "")
        if hardcover_slug:
            return f"https://hardcover.app/books/{hardcover_slug}"
        return url

    @staticmethod
    def _parse_edition_url(result: Dict, edition: Dict, url: str) -> str:
        edition_id = edition.get("id", "")
        slug = result.get("slug", "")
        if edition_id:
            return f"https://hardcover.app/books/{slug}/editions/{edition_id}"
        return url

    @staticmethod
    def _parse_edition_authors(edition: Dict, authors: List[str]) -> List[str]:
        try:
            contributions = edition.get("contributions", [])
            if not isinstance(contributions, list):
                return authors
            result_authors: List[str] = []
            for contrib in contributions:
                if isinstance(contrib, dict) and "author" in contrib:
                    author_data = contrib.get("author")
                    if isinstance(author_data, dict) and "name" in author_data:
                        result_authors.append(author_data["name"])
            return result_authors if result_authors else authors
        except Exception as e:
            log.warning(f"Error parsing edition authors: {e}")
            return authors

    @staticmethod
    def _parse_tags(result: Dict, tags: List[str]) -> List[str]:
        try:
            cached_tags = result.get("cached_tags", [])
            # Hardcover GraphQL now returns cached_tags as a scalar JSON value (!json)
            # It may be:
            # - a list of dicts: [{"tag": "..."}] (old shape)
            # - a list of strings: ["..."] (possible shape)
            # - a JSON-encoded string: "[\"...\"]" (defensive handling)
            # - a single string: "..."
            parsed: List[str] = []
            # If it's a string, try to json-decode, otherwise treat as single tag
            if isinstance(cached_tags, str):
                try:
                    decoded = json.loads(cached_tags)
                    cached_tags = decoded
                except Exception:
                    cached_tags = [cached_tags] if cached_tags else []

            if isinstance(cached_tags, list):
                for item in cached_tags:
                    if isinstance(item, dict):
                        val = item.get("tag") or item.get("name")
                        if val:
                            parsed.append(str(val))
                    elif isinstance(item, str):
                        if item:
                            parsed.append(item)
            elif isinstance(cached_tags, dict):
                # Some backends might return an object; try common keys
                val = cached_tags.get("tag") or cached_tags.get("name")
                if isinstance(val, str) and val:
                    parsed.append(val)

            return parsed if parsed else tags
        except Exception as e:
            log.warning(f"Error parsing tags: {e}")
            return tags

    @staticmethod
    def _parse_languages(edition: Dict, locale: str) -> List[str]:
        language_iso = (edition.get("language") or {}).get("code3", "")
        languages = [get_language_name(locale, language_iso)] if language_iso else []
        return languages

    @staticmethod
    def _safe_get(data, *keys, default=None):
        """Safely get nested dictionary values"""
        try:
            for key in keys:
                if isinstance(data, dict) and key in data:
                    data = data[key]
                else:
                    return default
            return data
        except (TypeError, KeyError):
            return default
