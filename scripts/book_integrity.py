# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Cheap structural checks on ebook files.

Used by the ingest processor (is a dropped file complete yet?) and by the metadata
enforcer (did ebook-polish write a sane book before we replace the original?).
Kept free of Flask/cps imports so both processes, and the tests, can use it.
"""

import os
import zipfile

# Formats that are zip containers
ZIP_FORMATS = frozenset({"epub", "kepub", "cbz"})
# Formats stored in a Palm database whose type/creator is BOOKMOBI
MOBI_FORMATS = frozenset({"azw3", "azw", "mobi"})
# Readers accept a few KB of junk after the final %%EOF; Acrobat looks at the last 1 KB
PDF_EOF_TAIL_BYTES = 2048


def book_format(path: str) -> str:
    """Lower-case extension without the dot ('Book.kepub' -> 'kepub')."""
    return os.path.splitext(path)[1].lstrip(".").lower()


def zip_opens(path: str) -> bool:
    """True if the zip's central directory can be read. A truncated or still-growing
    zip fails this, because the directory is written last, at the end of the file."""
    try:
        with zipfile.ZipFile(path) as zf:
            zf.infolist()
        return True
    except (zipfile.BadZipFile, OSError, ValueError, EOFError):
        return False


def zip_is_intact(path: str) -> bool:
    """Stricter than zip_opens: every member's data must also pass its CRC check."""
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.testzip() is None
    except (zipfile.BadZipFile, OSError, ValueError, EOFError, RuntimeError, NotImplementedError):
        # RuntimeError: encrypted member; NotImplementedError: unsupported compression
        return False


def pdf_has_eof(path: str, tail_bytes: int = PDF_EOF_TAIL_BYTES) -> bool:
    """True if '%%EOF' appears in the last tail_bytes of the file."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - tail_bytes))
            return b"%%EOF" in fh.read()
    except OSError:
        return False


def has_mobi_header(path: str) -> bool:
    """True if the Palm database header names the file a BOOKMOBI book."""
    try:
        with open(path, "rb") as fh:
            header = fh.read(68)
    except OSError:
        return False
    return len(header) == 68 and header[60:68] == b"BOOKMOBI"


def incomplete_reason(path: str) -> str | None:
    """Why a file dropped for import looks unfinished, or None if it looks complete.

    Only formats whose completeness can be judged cheaply are checked; everything
    else passes. Deliberately lenient: a zip only has to open (not pass CRC checks),
    so a slightly broken but complete book is still handed to calibre."""
    fmt = book_format(path)
    try:
        if os.path.getsize(path) == 0:
            return "file is empty"
    except OSError as e:
        return f"cannot read file ({e})"
    if fmt in ZIP_FORMATS and not zip_opens(path):
        return "not a readable zip yet (truncated or still being copied)"
    if fmt == "pdf" and not pdf_has_eof(path):
        return "PDF has no %%EOF marker near the end (truncated or still being copied)"
    return None


def polished_output_problem(original: str, output: str, min_size_ratio: float) -> str | None:
    """Why a rewritten book must not replace the original, or None if it looks sane.

    The output must exist, be non-empty, not be much smaller than the original, and
    keep the container structure the original had (an intact zip, a BOOKMOBI header)."""
    try:
        original_size = os.path.getsize(original)
        output_size = os.path.getsize(output)
    except OSError as e:
        return f"cannot stat output ({e})"
    if output_size == 0:
        return "output is empty"
    if output_size < original_size * min_size_ratio:
        return (f"output is suspiciously small ({output_size} bytes vs {original_size} bytes, "
                f"below {min_size_ratio:.0%} of the original)")
    fmt = book_format(original)
    if fmt in ZIP_FORMATS and not zip_is_intact(output):
        return "output is not an intact zip"
    if fmt in MOBI_FORMATS and has_mobi_header(original) and not has_mobi_header(output):
        return "output has lost its BOOKMOBI header"
    return None
