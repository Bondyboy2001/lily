"""Server messages and file contents that carry user data reach the page as text."""

import re

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


def test_task_table_escapes_cells():
    tasks = (REPO / "cps" / "templates" / "tasks.html").read_text()
    table = re.search(r'<table[^>]*id="tasktable"[^>]*>', tasks).group(0)
    assert 'data-escape="true"' in table


def test_txt_reader_does_not_parse_the_book_as_html():
    src = (JS / "reading" / "txt_reader.js").read_text()
    assert "$(\"#content\").load(" not in src
    assert '"text")' in src and "content.text(textStr)" in src
