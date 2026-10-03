# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Linearized ("fast web view") copies of large PDFs, for the PDF reader.

pdf.js fetches a PDF in 1 MB ranges, one after another. In most library PDFs the objects
page 1 needs are spread through the whole file, so a 59 MB textbook took ~30 ranges (15 s
on a 20 Mbit link) before page 1 showed. qpdf --linearize puts page 1 first: the same book
opens in under 3 s. The library file is never touched; the copy lives in the config dir,
keyed by the source's mtime and size, and the oldest copies go once the cache passes its cap.
"""

import os
import shutil
import subprocess  # nosec B404 - qpdf runs with a fixed argument list, no shell
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import config, constants, logger

log = logger.create()

# Smaller files load in a few ranges anyway
MIN_SIZE = 8 * 1024 * 1024
CACHE_CAP = 5 * 1024 * 1024 * 1024
QPDF_TIMEOUT = 300
_QPDF = shutil.which("qpdf")

# One qpdf at a time: browsing many book pages queues them instead of loading the NAS.
# A plain thread, never waited on, so the gevent server keeps serving meanwhile.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdf-fast")
_pending = set()
_lock = threading.Lock()


def _cache_dir():
    return os.path.join(constants.CONFIG_DIR, "pdf_fast")


def _cache_path(book_id, st):
    return os.path.join(_cache_dir(), f"{book_id}_{int(st.st_mtime)}_{st.st_size}.pdf")


def source(book):
    """The path of the book's PDF file, or None when it has none."""
    pdf = next((d for d in book.data or [] if (d.format or "").upper() == "PDF"), None)
    return os.path.join(config.get_book_path(), book.path, pdf.name + ".pdf") if pdf else None


def copy_path(book_id, source):
    """The linearized copy's path when it exists, else None. Runs on every range request, so
    it only looks: ready_or_queue, once per reader open, marks the copy as read."""
    try:
        path = _cache_path(book_id, os.stat(source))
    except OSError:
        return None
    return path if os.path.isfile(path) else None


def ready_or_queue(book_id, source):
    """True when the linearized copy exists. Otherwise queue one if it would help: the file
    is big, not already linearized, and qpdf hasn't failed on it before."""
    try:
        st = os.stat(source)
    except OSError:
        return False
    path = _cache_path(book_id, st)
    try:
        # Recently read copies are the last to be pruned. Only the access time: the
        # modification time is in the ETag, and pdf.js's ranges must all match one file.
        os.utime(path, (time.time(), os.stat(path).st_mtime))
        return True
    except OSError:
        pass
    if not _QPDF or st.st_size < MIN_SIZE or os.path.exists(path + ".failed"):
        return False
    try:
        with open(source, "rb") as f:
            if b"/Linearized" in f.read(1024):
                return False
    except OSError:
        return False
    with _lock:
        if path in _pending:
            return False
        _pending.add(path)
    _executor.submit(_linearize, book_id, source, path)
    return False


def _linearize(book_id, source, path):
    tmp = path + ".tmp"
    try:
        os.makedirs(_cache_dir(), exist_ok=True)
        result = subprocess.run([_QPDF, "--linearize", source, tmp],  # nosec B603
                                capture_output=True, timeout=QPDF_TIMEOUT)
        # 3 means it worked with warnings (qpdf repaired something on the way)
        if result.returncode in (0, 3) and os.path.getsize(tmp) > 0:
            os.replace(tmp, path)
            _drop_older(book_id, path)
            _prune()
            log.info("Linearized PDF of book %s for the reader", book_id)
        else:
            open(path + ".failed", "w").close()
            log.warning("qpdf could not linearize book %s: %s", book_id,
                        result.stderr.decode(errors="replace")[-300:])
    except Exception as e:
        log.warning("Linearizing book %s failed: %s", book_id, e)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
        with _lock:
            _pending.discard(path)


def _drop_older(book_id, keep):
    """Copies made from an earlier version of the book's file."""
    prefix = f"{book_id}_"
    for name in os.listdir(_cache_dir()):
        full = os.path.join(_cache_dir(), name)
        if name.startswith(prefix) and full != keep:
            os.remove(full)


def _prune():
    entries = []
    for name in os.listdir(_cache_dir()):
        if name.endswith(".pdf"):
            full = os.path.join(_cache_dir(), name)
            st = os.stat(full)
            entries.append((st.st_atime, st.st_size, full))
    total = sum(size for _, size, _ in entries)
    for _, size, full in sorted(entries):
        if total <= CACHE_CAP:
            break
        os.remove(full)
        total -= size
