# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""How often a paper has been cited, from OpenAlex (https://docs.openalex.org).
Semantic Scholar's keyless API answers 429 most of the time, so it isn't used."""

import time

import requests

from cps import logger
from cps.services.identifiers import ARXIV_DOI_PREFIX, arxiv_id_from_doi

log = logger.create()

OPENALEX_URL = "https://api.openalex.org/works"
HEADERS = {"User-Agent": "Lily/1.0 (citation count)"}
CACHE_SECONDS = 24 * 60 * 60
_cache = {}


def paper_ids(identifiers):
    """The (doi, arxiv id) a book's identifiers name. A paper with only an arXiv id
    gets arXiv's own DOI; an arXiv DOI gives the arXiv id."""
    ids = {i.type.lower(): i.val.strip() for i in identifiers if i.val and i.val.strip()}
    doi = ids.get("doi", "")
    arxiv = ids.get("arxiv", "") or arxiv_id_from_doi(doi)
    return doi or (ARXIV_DOI_PREFIX + arxiv if arxiv else ""), arxiv


def _lookup(doi, arxiv):
    params = {"select": "id,cited_by_count", "per_page": 1}
    # A preprint that was later published is merged into the journal's work, which
    # keeps the arXiv page as a location but not the arXiv DOI, so look arXiv up by page.
    if arxiv:
        params["filter"] = "locations.landing_page_url:http://arxiv.org/abs/{0}|https://arxiv.org/abs/{0}".format(arxiv)
        params["sort"] = "cited_by_count:desc"
        response = requests.get(OPENALEX_URL, params=params, headers=HEADERS, timeout=10)
        response.raise_for_status()
        results = response.json().get("results") or []
        work = results[0] if results else None
    else:
        response = requests.get(OPENALEX_URL + "/doi:" + doi, params={"select": params["select"]},
                                headers=HEADERS, timeout=10)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        work = response.json()
    if not work or work.get("cited_by_count") is None:
        return None
    work_id = work["id"].rsplit("/", 1)[-1]
    return {"count": work["cited_by_count"],
            "url": "https://openalex.org/works?filter=cites:" + work_id}


def citation_count(identifiers):
    """{"count", "url"} for a paper, or None when it has no DOI or arXiv id, isn't
    indexed, or OpenAlex can't be reached. Answers are kept for a day."""
    doi, arxiv = paper_ids(identifiers)
    if not doi:
        return None
    key = (doi.lower(), arxiv.lower())
    cached = _cache.get(key)
    if cached and cached[0] > time.time():
        return cached[1]
    try:
        result = _lookup(doi, arxiv)
    except (requests.RequestException, ValueError, KeyError) as e:
        log.warning("Citation count lookup failed for %s: %s", doi, e)
        return None
    _cache[key] = (time.time() + CACHE_SECONDS, result)
    return result
