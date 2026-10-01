# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Cover thumbnails are offered with `w` descriptors and a `sizes` per context, so a
phone's list row picks the small thumbnail rather than the largest (`x` descriptors)."""

import re

import pytest

from tests.unit.lily_env import lily_env, ADMIN_PASSWORD

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path):
    with lily_env(tmp_path) as e:
        e.app.jinja_env.globals.setdefault("csrf_token", lambda: "test-token")
        from tests.unit.test_lily_reader_static import _register_remaining_blueprints
        _register_remaining_blueprints(e.app)
        yield e


def _covers(html):
    return re.findall(r"<img\s+srcset=\"([^\"]+)\"\s+sizes=\"([^\"]+)\"([^>]*)>", html)


def _client(env):
    client = env.app.test_client()
    client.post("/login", data={"username": env.admin().name, "password": ADMIN_PASSWORD})
    return client


def test_grid_cards_use_width_descriptors_and_grid_sizes(env):
    env.add_book("Some Book")
    covers = _covers(_client(env).get("/").get_data(as_text=True))
    assert covers, "no cover images rendered"
    srcset, sizes, rest = covers[0]
    assert [c.split()[-1] for c in srcset.split(", ")] == ["170w", "340w", "680w"]
    assert sizes == "(max-width: 767px) 50vw, 220px"
    assert 'data-sizes-list="44px"' in rest and 'data-sizes-grid="(max-width: 767px) 50vw, 220px"' in rest


def test_list_view_starts_with_list_sizes(env):
    from cps import ub
    env.add_book("Some Book")
    admin = env.admin()
    admin.set_view_property("books", "view", "list")
    ub.session.commit()
    covers = _covers(_client(env).get("/").get_data(as_text=True))
    assert covers and covers[0][1] == "44px"
