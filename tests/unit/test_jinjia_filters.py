# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

from types import SimpleNamespace

from cps.jinjia import named_authors_filter


class TestNamedAuthorsFilter:
    """calibre's "Unknown" stand-in is hidden, so a book with no author shows none"""

    def test_drops_the_unknown_placeholder(self):
        authors = [SimpleNamespace(name="Unknown"), SimpleNamespace(name="Alexander Paulin")]
        assert [a.name for a in named_authors_filter(authors)] == ["Alexander Paulin"]

    def test_only_unknown_leaves_nothing(self):
        assert named_authors_filter([SimpleNamespace(name="unknown")]) == []
        assert named_authors_filter(None) == []
