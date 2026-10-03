#!/usr/bin/env python3
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""
Lightweight polling-based filesystem watcher fallback.

Purpose: When inotify runs out of watches (ENOSPC) on some platforms (e.g., Synology),
this script can be used to monitor a directory tree for new/updated files without
relying on inotify. It emits lines compatible with inotifywait's simple output:

  CLOSE_WRITE /absolute/path/to/file

Usage (mirrors inotifywait pipeline usage):
  python3 scripts/watch_fallback.py --path /watched/dir --interval 5 --exts epub,azw3,mobi,pdf,cbz,cbr

Notes:
  - Uses mtime and size to detect new or finished files. To avoid firing on partially
    written files, it requires the size AND mtime to be unchanged across several
    consecutive scans. File age (mtime) is deliberately NOT used as a shortcut:
    mtime-preserving copies (Finder/SMB, cp -p, rsync -t) give a still-growing file an
    old mtime.
  - Once a file has fired it will not fire again until its size/mtime actually change,
    so files left in place (e.g. queued for retry) are not re-emitted every scan.
  - Keeps a small in-memory index; optionally persists a cache file if requested later.
  - Designed to be simple, low-risk, and only used as a fallback.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from collections.abc import Iterable


@dataclass(frozen=True)
class FileKey:
    path: str


@dataclass
class FileStat:
    size: int
    mtime_ns: int
    stable_count: int = 0  # how many consecutive scans with identical stat
    stable_since: float = 0.0  # monotonic time the current stat was first observed
    fired: bool = False  # already emitted for this exact stat; don't refire until it changes


def iter_files(root: str, recursive: bool = True, extensions: set[str] | None = None) -> Iterable[str]:
    if not recursive:
        try:
            for name in os.listdir(root):
                fp = os.path.join(root, name)
                if os.path.isfile(fp) and _match_ext(fp, extensions):
                    yield fp
        except FileNotFoundError:
            return
        return

    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            if _match_ext(fp, extensions):
                yield fp


def _match_ext(path: str, extensions: set[str] | None) -> bool:
    if not extensions:
        return True
    _, ext = os.path.splitext(path)
    return ext.lower().lstrip('.') in extensions


def get_stat(path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
        return st.st_size, getattr(st, 'st_mtime_ns', int(st.st_mtime * 1e9))
    except FileNotFoundError:
        return None
    except PermissionError:
        return None


def print_event(event: str, path: str) -> None:
    # Emit in a format the shell while-read loop can parse: "EVENT PATH"
    sys.stdout.write(f"{event} {path}\n")
    sys.stdout.flush()


class PollScanner:
    """Tracks file stats across scans and decides when a file is finished.

    A file is emitted once its (size, mtime) has been identical for ``stable_scans``
    consecutive scans and for at least ``stabilize`` seconds of observation time.
    """

    def __init__(self, root: str, recursive: bool = True, extensions: set[str] | None = None,
                 stable_scans: int = 2, stabilize: float = 1.5):
        self.root = root
        self.recursive = recursive
        self.extensions = extensions
        self.stable_scans = max(1, int(stable_scans))
        self.stabilize = max(0.0, float(stabilize))
        self.index: dict[FileKey, FileStat] = {}

    def scan(self, now: float | None = None) -> list[str]:
        """Run one scan and return the paths that became ready during it."""
        if now is None:
            now = time.monotonic()
        ready: list[str] = []
        seen: set[FileKey] = set()
        for fp in iter_files(self.root, self.recursive, self.extensions):
            fk = FileKey(fp)
            seen.add(fk)
            st = get_stat(fp)
            if not st:
                continue
            size, mtime_ns = st
            prev = self.index.get(fk)
            if prev is None:
                # New file observed; require stabilization before emitting
                self.index[fk] = FileStat(size=size, mtime_ns=mtime_ns, stable_count=0, stable_since=now)
                continue

            if prev.size == size and prev.mtime_ns == mtime_ns:
                prev.stable_count += 1
            else:
                # Still being written (or replaced): start counting again and allow a new event
                prev.size = size
                prev.mtime_ns = mtime_ns
                prev.stable_count = 0
                prev.stable_since = now
                prev.fired = False
                continue

            if (not prev.fired
                    and prev.stable_count >= self.stable_scans
                    and now - prev.stable_since >= self.stabilize):
                ready.append(fp)
                prev.fired = True

        # Clean up removed files from index to keep memory small
        if len(seen) < len(self.index):
            for fk in list(self.index.keys()):
                if fk not in seen:
                    self.index.pop(fk, None)
        return ready


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Polling watcher fallback emitting inotify-like events")
    p.add_argument("--path", required=True, help="Directory to watch")
    p.add_argument("--interval", type=float, default=5.0, help="Polling interval in seconds (default: 5)")
    p.add_argument("--recursive", action="store_true", help="Recurse into subdirectories (default: true)")
    p.add_argument("--no-recursive", dest="recursive", action="store_false", help="Disable recursion")
    p.set_defaults(recursive=True)
    p.add_argument("--exts", default="", help="Comma-separated list of file extensions to include (no dots)")
    p.add_argument("--stabilize", type=float, default=1.5, help="Minimum seconds a file must be observed unchanged to fire (default: 1.5)")
    p.add_argument("--stable-scans", type=int, default=2, help="Consecutive scans with identical size+mtime required to fire (default: 2)")

    args = p.parse_args(list(argv) if argv is not None else None)

    root = os.path.abspath(args.path)
    if not os.path.isdir(root):
        sys.stderr.write(f"[watch-fallback] Path is not a directory or does not exist: {root}\n")
        return 2

    exts = {e.strip().lower() for e in args.exts.split(',') if e.strip()} if args.exts else None

    scanner = PollScanner(root, args.recursive, exts, stable_scans=args.stable_scans, stabilize=args.stabilize)
    last_scan_at = 0.0

    # Prime the index once; files already present still have to prove they are stable
    scanner.scan()

    try:
        while True:
            now = time.time()
            # Avoid drift accumulation when the loop body takes time.
            if last_scan_at and now - last_scan_at < args.interval:
                time.sleep(max(0.0, args.interval - (now - last_scan_at)))
            last_scan_at = time.time()

            for fp in scanner.scan():
                # Emit a close_write-style event
                print_event("CLOSE_WRITE", fp)
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        sys.stderr.write(f"[watch-fallback] Unexpected error: {e}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
