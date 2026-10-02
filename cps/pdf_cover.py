# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Centres a PDF's cover on what is printed on its first page.

A PDF's cover is a render of page 1, and many papers sit off-centre on the page (set for A4,
printed on US Letter), so the grid showed a wide white band down one side. recentre_book_cover
renders page 1 again, trims the side margins to match (arXiv's stamp down the left margin
counts as print) and saves that as cover.jpg. It only replaces a cover that is still that
plain render: a provider's or an uploaded cover never looks like page 1, so it is kept.

Imports and Rebuild metadata call it. The margin maths is plain Python; Wand only reads,
crops and writes the images."""

import os
import threading
from datetime import datetime, timezone

from cps import logger

log = logger.create()

# Ghostscript resolution of the render: 1275x1650 for US Letter, the size calibre's covers are
RENDER_DPI = 150
# The grid tile is A4 shaped (1 : 1.414)
TILE_RATIO = 1 / 1.414
# Grey levels darker than this are print
INK_LEVEL = 170
# A column is print when this share of its pixels is: one speck of dust is not
INK_SHARE = 0.004
# Margin kept beside the print, as a share of the page width, when the tile allows it
MIN_MARGIN = 0.03
# Less than this share of the width to trim leaves the cover as it is
MIN_TRIM = 0.03
# Width the page is scaled to for measuring; the crop is scaled back up
MEASURE_WIDTH = 400
# Size and closeness for "this cover is still the plain page render"
COMPARE_SIZE = (24, 32)
SAME_PAGE = 18.0
SAME_SHAPE = 0.01
# Ghostscript may run inside ImageMagick's process, so one render at a time
_render_lock = threading.Lock()


def ink_columns(gray, width, height):
    """(first, last) column with print in a row-major 8-bit grey image, or None for a blank page."""
    need = max(2, int(height * INK_SHARE))
    counts = [0] * width
    for y in range(height):
        row = gray[y * width:(y + 1) * width]
        for x, level in enumerate(row):
            if level < INK_LEVEL:
                counts[x] += 1
    columns = [x for x, count in enumerate(counts) if count >= need]
    if not columns:
        return None
    return columns[0], columns[-1]


def balanced_crop(width, height, first, last):
    """(left, right) edges that give the print even side margins, or None to keep the page.

    The margins shrink to the narrower of the two, then further if that still leaves the page
    wider than the A4 grid tile, which would otherwise trim one side again. A print that runs
    to an edge (a scan's dark border) is left alone."""
    if first <= 0 or last >= width - 1:
        return None
    ink = last - first + 1
    margin = min(first, width - 1 - last)
    fit = (height * TILE_RATIO - ink) / 2
    margin = int(min(margin, max(fit, width * MIN_MARGIN)))
    left, right = first - margin, last + 1 + margin
    # A few pixels off is not worth rewriting the cover for
    if width - (right - left) < width * MIN_TRIM:
        return None
    return left, right


def same_page(a, b):
    """True when two equally sized grey thumbnails show the same page."""
    if len(a) != len(b) or not a:
        return False
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a) <= SAME_PAGE


def _gray(img, size):
    from wand.image import Image
    with Image(image=img) as small:
        small.transform_colorspace('gray')
        small.resize(*size)
        return bytes(small.export_pixels(channel_map='R', storage='char'))


def render_first_page(pdf_path):
    """Page 1 of the PDF as a white-backed Wand image (the caller closes it)."""
    from wand.color import Color
    from wand.image import Image
    img = Image()
    try:
        img.options['pdf:use-cropbox'] = 'true'
        with _render_lock:
            img.read(filename=pdf_path + '[0]', resolution=RENDER_DPI)
        if len(img.sequence) > 1:
            img.sequence[1:] = []
        img.background_color = Color('white')
        img.alpha_channel = 'remove'
        img.transform_colorspace('srgb')
        return img
    except Exception:
        img.close()
        raise


def centred_page(page):
    """A copy of the page render cropped to even side margins, or None when it is already even."""
    from wand.image import Image
    width, height = page.width, page.height
    scale = MEASURE_WIDTH / width
    small_size = (MEASURE_WIDTH, max(1, round(height * scale)))
    found = ink_columns(_gray(page, small_size), *small_size)
    if found is None:
        return None
    crop = balanced_crop(*small_size, *found)
    if crop is None:
        return None
    left, right = max(0, int(crop[0] / scale)), min(width, int(round(crop[1] / scale)))
    centred = Image(image=page)
    centred.crop(left=left, top=0, right=right, bottom=height)
    centred.reset_coords()
    return centred


def recentre_cover(pdf_path, cover_path):
    """Replace cover_path with a centred render of the PDF's first page; True when it did.

    Leaves the cover alone when it is not the plain page render (a provider's or an uploaded
    cover, or one already centred) or when the page is already even."""
    from wand.image import Image
    if not (os.path.isfile(pdf_path) and os.path.isfile(cover_path)):
        return False
    with render_first_page(pdf_path) as page, Image(filename=cover_path) as cover:
        # A centred cover is narrower than the page, so a second pass leaves it be
        if abs(cover.width / cover.height - page.width / page.height) > SAME_SHAPE:
            return False
        if not same_page(_gray(page, COMPARE_SIZE), _gray(cover, COMPARE_SIZE)):
            return False
        centred = centred_page(page)
        if centred is None:
            return False
        tmp_path = cover_path + '.centring'
        try:
            with centred:
                centred.format = 'jpeg'
                centred.compression_quality = 88
                centred.save(filename=tmp_path)
            os.replace(tmp_path, cover_path)
        except Exception:
            # No half-written file left in the book's folder
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
    return True


def cover_paths(book, library_path):
    """(PDF, cover.jpg) paths of a PDF book with a cover, or None."""
    if not book.has_cover:
        return None
    pdf = next((d for d in book.data if d.format.upper() == 'PDF'), None)
    if pdf is None:
        return None
    folder = os.path.join(library_path, book.path)
    return os.path.join(folder, pdf.name + '.pdf'), os.path.join(folder, 'cover.jpg')


def try_recentre_cover(paths, book_id=None):
    """recentre_cover that logs a failure instead of raising (one bad PDF must not stop a run)."""
    if not paths:
        return False
    try:
        return recentre_cover(*paths)
    except Exception as ex:
        log.warning("Could not centre the cover of book %s: %s", book_id, ex)
        return False


def mark_cover_changed(book):
    """Bump last_modified: cover URLs and cached thumbnails are keyed on it. The caller commits."""
    book.last_modified = datetime.now(timezone.utc)


def recentre_book_cover(book, library_path):
    """Centre a PDF book's cover in its folder; True when cover.jpg changed. The caller commits."""
    if not try_recentre_cover(cover_paths(book, library_path), book.id):
        return False
    mark_cover_changed(book)
    return True


def available():
    """True when ImageMagick (through Wand) is there to render and crop with."""
    try:
        import wand.image  # noqa: F401
        return True
    except ImportError:
        return False


def recentre_new_book_cover(book_id, library_path):
    """Centre a newly imported PDF's cover; True when cover.jpg changed."""
    from cps import db
    if not available():
        return False
    cdb = db.CalibreDB(expire_on_commit=False, init=True)
    try:
        book = cdb.get_book(book_id)
        if book is None or not recentre_book_cover(book, library_path):
            return False
        cdb.session.commit()
        return True
    except Exception:
        cdb.session.rollback()
        raise
    finally:
        cdb.session.close()
