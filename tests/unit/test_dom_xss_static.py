"""Server messages and file contents that carry user data reach the page as text."""

import pytest

from tests.unit.lily_env import REPO

pytestmark = pytest.mark.unit

JS = REPO / "cps" / "static" / "js"


def _function(src, name):
    start = src.index("function " + name)
    return src[start:src.index("\n}\n", start)]


def test_table_messages_are_inserted_as_text():
    src = (JS / "table.js").read_text()
    body = _function(src, "handleListServerResponse")
    assert ".text(item.message)" in body
    assert "+ item.message +" not in body
    assert "+ errorMsg +" not in src
