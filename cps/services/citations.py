# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""A paper's DOI and arXiv id, read off a book's identifiers."""

from cps.services.identifiers import ARXIV_DOI_PREFIX, arxiv_id_from_doi


def paper_ids(identifiers):
    """The (doi, arxiv id) a book's identifiers name. A paper with only an arXiv id
    gets arXiv's own DOI; an arXiv DOI gives the arXiv id."""
    ids = {i.type.lower(): i.val.strip() for i in identifiers if i.val and i.val.strip()}
    doi = ids.get("doi", "")
    arxiv = ids.get("arxiv", "") or arxiv_id_from_doi(doi)
    return doi or (ARXIV_DOI_PREFIX + arxiv if arxiv else ""), arxiv
