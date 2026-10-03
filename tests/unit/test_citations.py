"""Book page: arXiv and DOI links."""

import pytest

from cps import db

pytestmark = pytest.mark.unit


def ids(**values):
    return [db.Identifiers(val, key, 1) for key, val in values.items()]


def test_arxiv_identifier_links_to_its_abstract_page():
    arxiv = ids(arxiv="2601.22106")[0]
    assert arxiv.format_type() == "arXiv"
    assert repr(arxiv) == "https://arxiv.org/abs/2601.22106"

