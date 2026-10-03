# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

# Version from AutoCaliWeb - Optimized by - gelbphoenix & UsamaFoad
# Hardcover API: https://docs.hardcover.app/api/getting-started/
import json
from os import getenv

import requests

from cps import config, constants, logger
from cps.isoLanguages import get_language_name
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata, ProviderError

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
        self, identifiers: dict[str, str], generic_cover: str = "", locale: str = "en"
    ) -> list[MetaRecord]:
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
    ) -> list[MetaRecord] | None:
        data = self._query(Hardcover.SEARCH_QUERY, query)
        if not data:
            return []
        val: list[MetaRecord] = []
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

    def available(self) -> bool:
        return bool(getattr(config, "config_hardcover_token", None) or getenv("HARDCOVER_TOKEN"))

    def _query(self, gql: str, variable) -> dict | None:
        """Runs a GraphQL query. None without a token (Hardcover is off until HARDCOVER_TOKEN
        is set), quietly: a library rebuild asks once a book. A failed request is raised."""
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
        except Exception as e:
            # The token travels in a header, so the error's text is safe to pass on
            raise ProviderError(f"Hardcover request failed: {e}") from None
        if "errors" in response_data:
            raise ProviderError(f"Hardcover answered with errors: {response_data['errors']}")
        if "data" not in response_data:
            raise ProviderError("Hardcover's answer has no data")
        return response_data

    def _parse_title_result(
        self, result: dict, generic_cover: str, locale: str
    ) -> MetaRecord | None:
        try:
            document = self._safe_get(result, "document", default={})
            if not document:
                return None

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
            )

            image_data = self._safe_get(document, "image", default={})
            match.cover = self._safe_get(image_data, "url", default=generic_cover)

            match.description = self._safe_get(document, "description", default="")
            match.publishedDate = self._safe_get(document, "release_date", default="")
            match.identifiers = {
                "hardcover-id": match.id,
                "hardcover-slug": self._safe_get(document, "slug", default=""),
            }
            return match
        except Exception as e:
            log.warning(f"Error parsing title result: {e}")
            return None

    def _parse_edition_results(
        self, result: dict, generic_cover: str, locale: str
    ) -> list[MetaRecord]:
        editions: list[MetaRecord] = []
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
            )
            match.cover = (edition.get("image") or {}).get("url", generic_cover)
            match.description = result.get("description", "")
            match.publisher = (edition.get("publisher") or {}).get("name", "")
            match.publishedDate = edition.get("release_date", "")
            match.languages = self._parse_languages(edition, locale)
            match.identifiers = {
                "hardcover-id": book_id,
                "hardcover-slug": result.get("slug", ""),
                "hardcover-edition": edition.get("id", ""),
            }
            # Both keys always come, null when the edition has no such ISBN
            isbn = edition.get("isbn_13") or edition.get("isbn_10")
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
    def _parse_title_url(result: dict, url: str) -> str:
        document = result.get("document", {})
        hardcover_slug = document.get("slug", "")
        if hardcover_slug:
            return f"https://hardcover.app/books/{hardcover_slug}"
        return url

    @staticmethod
    def _parse_edition_url(result: dict, edition: dict, url: str) -> str:
        edition_id = edition.get("id", "")
        slug = result.get("slug", "")
        if edition_id:
            return f"https://hardcover.app/books/{slug}/editions/{edition_id}"
        return url

    @staticmethod
    def _parse_edition_authors(edition: dict, authors: list[str]) -> list[str]:
        try:
            contributions = edition.get("contributions", [])
            if not isinstance(contributions, list):
                return authors
            result_authors: list[str] = []
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
    def _parse_languages(edition: dict, locale: str) -> list[str]:
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
