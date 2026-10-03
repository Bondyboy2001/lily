# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Whether two pictures are the same book cover, so a lookup can tell which record is the book
when its title and authors can't: a book with no author, or the edition among several.

Each picture is shrunk to a 32x32 grey thumbnail and the two are correlated. On the local
library a cover and the provider's copy of it scored 0.997-1.000 and different covers 0.40 at
most, whatever their sizes. A picture with little contrast (a page of text, a plain white
cover) correlates with any other like it, so it is no evidence either way.

Wand only reads and shrinks the images; the comparison is plain Python."""

import functools
import math

import requests

from cps import logger

log = logger.create()

SIZE = 32
# Least contrast a thumbnail needs to say anything (standard deviation of its grey levels, 0-255):
# near-blank pages scored 3-14, covers 18 and up
MIN_DETAIL = 15.0
# Least correlation of two thumbnails of the same cover
SAME = 0.95
# A provider's cover is a few hundred KB; larger is not a cover
MAX_BYTES = 5 * 1024 * 1024


def thumbnail(blob: bytes):
    """The picture as a mean-free 32x32 grey thumbnail and its contrast, or None when it can't
    be read or has too little detail to compare."""
    try:
        from wand.image import Image
        with Image(blob=blob) as img:
            img.transform_colorspace('gray')
            img.depth = 8
            img.resize(SIZE, SIZE)
            pixels = img.make_blob('gray')
    except Exception as e:
        log.debug(f"Could not read a cover to compare: {e}")
        return None
    return from_pixels(pixels)


def from_pixels(pixels):
    """thumbnail() of grey levels already shrunk."""
    if not pixels:
        return None
    mean = sum(pixels) / len(pixels)
    centred = tuple(p - mean for p in pixels)
    detail = math.sqrt(sum(c * c for c in centred) / len(centred))
    return (centred, detail) if detail >= MIN_DETAIL else None


def same_cover(a, b) -> bool:
    """Whether two thumbnails are of the same cover; False when either is missing."""
    if not a or not b or len(a[0]) != len(b[0]):
        return False
    (va, da), (vb, db) = a, b
    return sum(x * y for x, y in zip(va, vb)) / (len(va) * da * db) >= SAME


def file_thumbnail(path):
    """thumbnail() of an image file; None when there is none."""
    try:
        with open(path, 'rb') as f:
            return thumbnail(f.read())
    except (OSError, TypeError):
        return None


@functools.lru_cache(maxsize=256)
def remote_thumbnail(url: str):
    """thumbnail() of the picture at url; None when it can't be had. Kept, as a rebuild weighs
    the same provider covers against several books."""
    if not (url or '').startswith(('http://', 'https://')):
        return None
    try:
        from cps import cli_param, cw_advocate
        fetch = requests.get if cli_param.allow_localhost else cw_advocate.get
        # Redirects followed by hand, so each hop is checked as save_cover_from_url's are
        # (covers.openlibrary.org answers with a 302 to archive.org)
        for _hop in range(6):
            response = fetch(url, timeout=(10, 20), allow_redirects=False, stream=True)
            if not response.is_redirect:
                break
            url = requests.compat.urljoin(url, response.headers["location"])
            response.close()
        else:
            return None
        with response:
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    return None
    except Exception as e:
        log.debug(f"Could not fetch cover {url} to compare: {e}")
        return None
    return thumbnail(bytes(content))
