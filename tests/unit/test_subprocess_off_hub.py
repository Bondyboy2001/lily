# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Subprocess waits on the gevent hub's thread go to the hub's thread pool, so other
greenlets (requests) keep running; downloads skip `calibredb export` where the metadata
enforcer already keeps the file's metadata current."""

import sys
import threading
import types

import gevent
import pytest

from cps import embed_helper, subproc_wrapper

pytestmark = pytest.mark.unit


def test_process_communicate_lets_other_greenlets_run():
    ticks = []

    def ticker():
        while True:
            ticks.append(1)
            gevent.sleep(0.01)

    other = gevent.spawn(ticker)
    try:
        code, out, err = subproc_wrapper.process_communicate(
            [sys.executable, "-c", "import time; time.sleep(0.3); print('done')"])
    finally:
        other.kill()
    assert code == 0 and out.strip() == "done"
    assert len(ticks) >= 5, "the hub was blocked while the child process ran"


def test_run_off_hub_is_a_plain_call_off_the_main_thread():
    seen = []
    worker = threading.Thread(target=lambda: seen.append(
        subproc_wrapper.run_off_hub(lambda: threading.current_thread())))
    worker.start()
    worker.join()
    assert seen == [worker]


@pytest.mark.parametrize("fmt, enforcement, expected", [
    ("epub", 1, False),
    ("AZW3", 1, False),
    ("epub", 0, True),
    ("pdf", 1, True),
    ("mobi", 1, True),
])
def test_download_needs_calibre_export(monkeypatch, fmt, enforcement, expected):
    from cps import render_template
    fake = types.SimpleNamespace(cwa_settings={"auto_metadata_enforcement": enforcement})
    monkeypatch.setattr(render_template, "get_request_cwa_db", lambda: fake)
    assert embed_helper.download_needs_calibre_export(fmt) is expected
