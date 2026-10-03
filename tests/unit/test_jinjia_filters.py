# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

from types import SimpleNamespace

from cps.jinjia import flatten_breaks_filter as flatten_breaks, named_authors_filter


class TestFlattenBreaksFilter:
    """Tests for the flatten_breaks Jinja2 filter used on book descriptions"""

    def test_br_becomes_space(self):
        assert flatten_breaks('<p>one<br>two<br/>three<br />four</p>') == '<p>one two three four</p>'

    def test_paragraphs_are_joined(self):
        assert flatten_breaks('<p>one</p>\n<p class="x">two</p>') == '<p>one two</p>'

    def test_empty_paragraphs_are_dropped(self):
        assert flatten_breaks('<p>one</p><p>&nbsp;</p><p><br></p><p>two</p>') == '<p>one two</p>'

    def test_newlines_collapse_and_inline_markup_survives(self):
        assert flatten_breaks('a\n\n  <b>b</b>\nc') == 'a <b>b</b> c'

    def test_none_and_empty(self):
        assert flatten_breaks(None) == ''
        assert flatten_breaks('') == ''


class TestNamedAuthorsFilter:
    """calibre's "Unknown" stand-in is hidden, so a book with no author shows none"""

    def test_drops_the_unknown_placeholder(self):
        authors = [SimpleNamespace(name="Unknown"), SimpleNamespace(name="Alexander Paulin")]
        assert [a.name for a in named_authors_filter(authors)] == ["Alexander Paulin"]

    def test_only_unknown_leaves_nothing(self):
        assert named_authors_filter([SimpleNamespace(name="unknown")]) == []
        assert named_authors_filter(None) == []
