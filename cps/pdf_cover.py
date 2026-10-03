# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Gives a PDF book a cover of its first page, centred on what is printed there.

A PDF's cover is a render of page 1, and many papers sit off-centre on the page (set for A4,
printed on US Letter), so the grid showed a wide white band down one side. recentre_cover
renders page 1 again, trims the side margins to match (arXiv's stamp down the left margin
is cropped off first) and saves that as cover.jpg.

A book whose only files are PDFs always shows page 1: a provider's cover on it is replaced.
One chosen by hand (ticked in Fetch metadata, or uploaded) is kept, and so is any cover of a
book with an EPUB or other file beside its PDF.

A PDF book with no cover.jpg, or only the "Cover not available" card the old import wrote
for PDFs it could not render, gets page 1 as its cover (fix_cover), and one whose cover.jpg
is there but not flagged in the library shows it again.

Imports and Rebuild metadata call it. The margin maths is plain Python; Wand only reads,
crops and writes the images."""

import hashlib
import os
import threading
from datetime import datetime, UTC

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
# A left-margin stamp must start in this share of the width, and not on the edge itself
STAMP_ZONE = 0.12
# ...be no wider than this share of the width
STAMP_WIDTH = 0.05
# ...have this much blank page between it and the print
STAMP_GAP = 0.03
# ...and run down at least this share of the height (a small logo is not a stamp)
STAMP_HEIGHT = 0.25
# The crop edge lands this share of the width into the blank gap, off the stamp
STAMP_PAD = 0.01
# Width the page is scaled to for measuring; the crop is scaled back up
MEASURE_WIDTH = 400
# Size and closeness for "this cover is still the plain page render"
COMPARE_SIZE = (24, 32)
SAME_PAGE = 18.0
SAME_SHAPE = 0.01
# The old import's "Cover not available" card (282x400 JPEG): a book whose cover is this has none
PLACEHOLDER_SIZE = 19501
PLACEHOLDER_MD5 = '9173cbd4f0e3c7757e27fa5ec5a982dd'
# Ghostscript may run inside ImageMagick's process, so one render at a time
_render_lock = threading.Lock()


def _ink_counts(gray, width, height):
    """Per-column print pixel counts of a row-major 8-bit grey image."""
    counts = [0] * width
    for y in range(height):
        row = gray[y * width:(y + 1) * width]
        for x, level in enumerate(row):
            if level < INK_LEVEL:
                counts[x] += 1
    return counts


def ink_columns(gray, width, height, start=0):
    """(first, last) column with print in a row-major 8-bit grey image, or None for a blank
    page. Columns before start are ignored."""
    need = max(2, int(height * INK_SHARE))
    counts = _ink_counts(gray, width, height)
    columns = [x for x in range(start, width) if counts[x] >= need]
    if not columns:
        return None
    return columns[0], columns[-1]


def left_stamp(gray, width, height):
    """(start, end) columns of a stamp down the left margin, or None.

    arXiv prints its id down the left edge of every page: a tall, thin band of print set off
    from the body by a blank gap. The leftmost cluster of ink columns (gaps under STAMP_GAP
    joined, as thin glyph strokes can leave a column below the threshold) is a stamp when it
    sits in the left zone (but off the very edge, where a scan's border runs), is narrow, has
    more print beyond it, and is tall."""
    need = max(2, int(height * INK_SHARE))
    counts = _ink_counts(gray, width, height)
    columns = [x for x, count in enumerate(counts) if count >= need]
    if not columns or columns[0] <= 0 or columns[0] > width * STAMP_ZONE:
        return None
    start = columns[0]
    end = start
    i = 0
    while i + 1 < len(columns) and columns[i + 1] - end - 1 < width * STAMP_GAP:
        i += 1
        end = columns[i]
    if end - start + 1 > width * STAMP_WIDTH:
        return None
    rest = columns[i + 1:]
    if not rest:
        return None
    rows = [y for y in range(height)
            if min(gray[y * width + start:y * width + end + 1]) < INK_LEVEL]
    if rows[-1] - rows[0] + 1 < height * STAMP_HEIGHT:
        return None
    return start, end


def balanced_crop(width, height, first, last, stamp_end=None):
    """(left, right) edges that give the print even side margins, or None to keep the page.

    The margins shrink to the narrower of the two, then further if that still leaves the page
    wider than the A4 grid tile, which would otherwise trim one side again. With stamp_end
    (the last column of a left-margin stamp) the left edge stays off it, in the blank gap.
    A print that runs to an edge (a scan's dark border) is left alone."""
    if first <= 0 or last >= width - 1:
        return None
    ink = last - first + 1
    margin = min(first, width - 1 - last)
    fit = (height * TILE_RATIO - ink) / 2
    margin = int(min(margin, max(fit, width * MIN_MARGIN)))
    if stamp_end is not None:
        margin = min(margin, first - stamp_end - 1 - int(width * STAMP_PAD))
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
    gray = _gray(page, small_size)
    stamp = left_stamp(gray, *small_size)
    if stamp is not None:
        found = ink_columns(gray, *small_size, start=stamp[1] + 1)
    else:
        found = ink_columns(gray, *small_size)
    if found is None:
        return None
    crop = balanced_crop(*small_size, *found, stamp_end=stamp[1] if stamp else None)
    if crop is None:
        return None
    left, right = max(0, int(crop[0] / scale)), min(width, int(round(crop[1] / scale)))
    centred = Image(image=page)
    centred.crop(left=left, top=0, right=right, bottom=height)
    centred.reset_coords()
    return centred


def _save_jpeg(img, path):
    """Write img as a JPEG at path, leaving no half-written file in the book's folder."""
    tmp_path = path + '.centring'
    try:
        img.format = 'jpeg'
        img.compression_quality = 88
        img.save(filename=tmp_path)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def is_placeholder(cover_path):
    """True when the file is the old import's "Cover not available" card."""
    try:
        if os.path.getsize(cover_path) != PLACEHOLDER_SIZE:
            return False
        with open(cover_path, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest() == PLACEHOLDER_MD5
    except OSError:
        return False


def save_page_cover(pdf_path, cover_path):
    """Write the PDF's first page, centred on its print, as cover_path."""
    with render_first_page(pdf_path) as page:
        centred = centred_page(page)
        if centred is None:
            _save_jpeg(page, cover_path)
        else:
            with centred:
                _save_jpeg(centred, cover_path)


def _looks_like(cover, page):
    """True when the cover image shows the page render, at its proportions."""
    if abs(cover.width / cover.height - page.width / page.height) > SAME_SHAPE:
        return False
    return same_page(_gray(page, COMPARE_SIZE), _gray(cover, COMPARE_SIZE))


def recentre_cover(pdf_path, cover_path, replace=False):
    """Make cover_path the PDF's first page, centred on its print; True when it changed.

    A cover that is still the plain page render is centred. Any other cover (a provider's or an
    uploaded one) is replaced only with `replace`; one already centred is left as it is."""
    from wand.image import Image
    if not (os.path.isfile(pdf_path) and os.path.isfile(cover_path)):
        return False
    with render_first_page(pdf_path) as page, Image(filename=cover_path) as cover:
        centred = centred_page(page)
        try:
            target = centred if centred is not None else page
            # A centred cover is narrower than the page, so a second pass leaves it be
            if _looks_like(cover, target):
                return False
            if not (replace or _looks_like(cover, page)):
                return False
            _save_jpeg(target, cover_path)
        finally:
            if centred is not None:
                centred.close()
    return True


def fix_cover(pdf_path, cover_path, has_cover, replace=False):
    """Make sure a PDF book has a cover it shows; True when cover.jpg or the book's flag must change.

    No cover.jpg, or only the placeholder card: page 1 becomes the cover. With `replace`, any
    other cover becomes page 1 too. Without it, a cover.jpg the book isn't flagged as having is
    shown again as it is, and a flagged one is centred when it is still the plain page render
    (recentre_cover)."""
    if not os.path.isfile(pdf_path):
        return False
    if not os.path.isfile(cover_path) or is_placeholder(cover_path):
        save_page_cover(pdf_path, cover_path)
        return True
    if not (has_cover or replace):
        return True
    return recentre_cover(pdf_path, cover_path, replace) or not has_cover


def _hand_cover(book_id):
    """True when the book's cover was chosen by hand, or when that cannot be told (keep it)."""
    try:
        from cwa_db import CWA_DB
        return CWA_DB().has_hand_cover(book_id)
    except Exception as e:
        log.debug("Could not tell whether book %s has a hand-picked cover: %s", book_id, e)
        return True


def cover_job(book, library_path):
    """(PDF path, cover.jpg path, has a cover, replace its cover) of a PDF book, or None for a
    book with no PDF. The cover is replaced by page 1 when PDFs are the book's only files and
    nobody chose its cover by hand."""
    pdf = next((d for d in book.data if d.format.upper() == 'PDF'), None)
    if pdf is None:
        return None
    folder = os.path.join(library_path, book.path)
    only_pdf = all(d.format.upper() == 'PDF' for d in book.data)
    replace = only_pdf and not _hand_cover(book.id)
    return (os.path.join(folder, pdf.name + '.pdf'), os.path.join(folder, 'cover.jpg'),
            bool(book.has_cover), replace)


def try_fix_cover(job, book_id=None):
    """fix_cover that logs a failure instead of raising (one bad PDF must not stop a run)."""
    if not job:
        return False
    try:
        return fix_cover(*job)
    except Exception as ex:
        log.warning("Could not make or centre the cover of book %s: %s", book_id, ex)
        return False


def mark_cover_changed(book):
    """Flag the book as having a cover and bump last_modified: cover URLs and cached thumbnails
    are keyed on it. The caller commits."""
    book.has_cover = 1
    book.last_modified = datetime.now(UTC)


def fix_book_cover(book, library_path):
    """Make or centre a PDF book's cover in its folder; True when it changed. The caller commits."""
    if not try_fix_cover(cover_job(book, library_path), book.id):
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
    """Make or centre a newly imported PDF's cover; True when it changed."""
    from cps import db
    if not available():
        return False
    cdb = db.CalibreDB(expire_on_commit=False, init=True)
    try:
        book = cdb.get_book(book_id)
        if book is None or not fix_book_cover(book, library_path):
            return False
        cdb.session.commit()
        return True
    except Exception:
        cdb.session.rollback()
        raise
    finally:
        cdb.session.close()
