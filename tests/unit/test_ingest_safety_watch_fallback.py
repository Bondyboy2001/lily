# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Polling watcher must not fire for still-growing files, even with old mtimes."""

import os
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"

OLD_MTIME = 1_000_000_000  # 2001: what an mtime-preserving copy (cp -p / Finder / SMB) looks like


@pytest.fixture
def watch_fallback(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    import watch_fallback as module
    return module


def _grow(path, chunk):
    with open(path, "ab") as fh:
        fh.write(chunk)
    os.utime(path, (OLD_MTIME, OLD_MTIME))


def test_growing_file_with_old_mtime_does_not_fire(watch_fallback, tmp_path):
    scanner = watch_fallback.PollScanner(str(tmp_path), stable_scans=2, stabilize=0)
    book = tmp_path / "book.epub"
    now = 100.0
    for i in range(6):
        _grow(book, b"x" * 100)
        assert scanner.scan(now=now) == [], f"fired while still growing (scan {i})"
        now += 5

    # Once the copy finishes, it fires after the required number of stable scans
    assert scanner.scan(now=now) == []
    assert scanner.scan(now=now + 5) == [str(book)]
    assert scanner.scan(now=now + 10) == []


def test_file_left_in_place_fires_once_until_it_changes(watch_fallback, tmp_path):
    scanner = watch_fallback.PollScanner(str(tmp_path), stable_scans=2, stabilize=0)
    book = tmp_path / "book.epub"
    book.write_bytes(b"data")
    os.utime(book, (OLD_MTIME, OLD_MTIME))

    fired = []
    for i in range(10):
        fired += scanner.scan(now=float(i * 5))
    assert fired == [str(book)]

    # A real change re-arms it
    _grow(book, b"more")
    fired = []
    for i in range(10, 20):
        fired += scanner.scan(now=float(i * 5))
    assert fired == [str(book)]


def test_stabilize_window_is_respected(watch_fallback, tmp_path):
    scanner = watch_fallback.PollScanner(str(tmp_path), stable_scans=1, stabilize=10)
    book = tmp_path / "book.epub"
    book.write_bytes(b"data")
    assert scanner.scan(now=0.0) == []
    assert scanner.scan(now=1.0) == []  # stable once, but only observed for 1s
    assert scanner.scan(now=11.0) == [str(book)]


def test_default_requires_thirty_seconds_unchanged(watch_fallback, tmp_path, monkeypatch):
    monkeypatch.delenv("CWA_POLL_STABLE_SECONDS", raising=False)
    args = watch_fallback.parse_args(["--path", str(tmp_path)])
    assert args.stabilize == 30
    scanner = watch_fallback.PollScanner(str(tmp_path), stabilize=args.stabilize)
    book = tmp_path / "book.epub"
    book.write_bytes(b"data")
    fired = {t: scanner.scan(now=float(t)) for t in range(0, 40, 5)}
    assert [t for t, ready in fired.items() if ready] == [30]


def test_stable_seconds_configurable_by_env(watch_fallback, tmp_path, monkeypatch):
    monkeypatch.setenv("CWA_POLL_STABLE_SECONDS", "90")
    assert watch_fallback.parse_args(["--path", str(tmp_path)]).stabilize == 90
    # An explicit flag still wins (the metadata change detector passes its own)
    assert watch_fallback.parse_args(["--path", str(tmp_path), "--stabilize", "1.5"]).stabilize == 1.5
    for junk in ("abc", "-5"):
        monkeypatch.setenv("CWA_POLL_STABLE_SECONDS", junk)
        assert watch_fallback.default_stable_seconds() == 30
