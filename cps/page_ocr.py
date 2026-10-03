# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Reads a scanned PDF's pages for a metadata lookup: the text of a page that has no text layer
(Tesseract), and the ISBN barcode on its back cover (zbar).

A scan is a picture of each page, so pypdf finds no text on it, and the lookup had nothing to
check a record's title or author against, nor an ISBN to look up. Pages are rendered as
pdf_cover renders a cover (Wand and Ghostscript), in grey at 200 dpi, about 0.3 s a page to
read. Without tesseract or zbarimg installed, nothing is read."""

import functools
import os
import shutil
import subprocess
import tempfile

from cps import logger

log = logger.create()

DPI = 200
# A page that has this little text on it is a scan with no text layer
MIN_TEXT = 20
# Seconds one page may take: a huge scan must not hold a rebuild up
TIMEOUT = 60


def available() -> bool:
    return shutil.which('tesseract') is not None


def barcodes_available() -> bool:
    return shutil.which('zbarimg') is not None


def needs_ocr(text: str) -> bool:
    """Whether pages' text layer is missing: too little text to be a page's."""
    return len((text or '').strip()) < MIN_TEXT


def _render(pdf_path: str, page: int) -> bytes:
    """The page (from 0; -1 is the last) as a grey PNG; b'' when it can't be rendered."""
    try:
        from wand.image import Image
        if page < 0:
            # pypdf counts the pages from the file's index, without rendering any
            from pypdf import PdfReader
            with open(pdf_path, 'rb') as pdf:
                page = len(PdfReader(pdf).pages) + page
        with Image(filename=f"{pdf_path}[{page}]", resolution=DPI) as img:
            img.background_color = 'white'
            img.alpha_channel = 'remove'
            img.transform_colorspace('gray')
            img.format = 'png'
            return img.make_blob()
    except Exception as e:
        log.debug(f"Could not render page {page} of {pdf_path}: {e}")
        return b''


def _run(args, stdin: bytes = b'') -> str:
    """The command's output; '' when it fails or takes too long."""
    try:
        done = subprocess.run(args, input=stdin, capture_output=True, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        log.debug(f"{args[0]} failed: {e}")
        return ''
    return done.stdout.decode('utf-8', 'replace')


@functools.lru_cache(maxsize=32)
def ocr_pages(pdf_path: str, mtime: float, first: int, last: int) -> str:
    """The text Tesseract reads on pages first to last (from 0, last not included); keyed by
    mtime so a replaced file is read again. '' without Tesseract."""
    if not available():
        return ''
    texts = []
    for page in range(first, last):
        image = _render(pdf_path, page)
        if not image:
            break  # past the last page, or unreadable
        texts.append(_run(['tesseract', 'stdin', 'stdout', '-l', 'eng', '--psm', '3', '--dpi', str(DPI)], image))
    return "\n".join(texts)


def isbns_in(barcodes: str) -> list[str]:
    """The ISBNs among zbarimg's raw output, one barcode a line: a book's EAN-13 starts 978 or
    979. A price add-on and other barcodes are left out."""
    found = []
    for line in barcodes.splitlines():
        code = line.strip()
        if len(code) == 13 and code.isdigit() and code.startswith(('978', '979')) and code not in found:
            found.append(code)
    return found


@functools.lru_cache(maxsize=32)
def back_cover_isbn(pdf_path: str, mtime: float) -> str:
    """The ISBN barcode on the PDF's last page, a scanned book's back cover; '' for none, or
    without zbarimg."""
    if not barcodes_available():
        return ''
    image = _render(pdf_path, -1)
    if not image:
        return ''
    # zbarimg reads files, not its input
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'back.png')
        with open(path, 'wb') as f:
            f.write(image)
        isbns = isbns_in(_run(['zbarimg', '--raw', '-q', '-Sdisable', '-Sean13.enable', path]))
    return isbns[0] if isbns else ''
