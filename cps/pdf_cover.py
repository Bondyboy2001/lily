# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Gives a PDF book a cover of its first page: a plain picture of page 1, exactly as printed.

Nothing is trimmed or centred, so a cover always looks like the PDF's front page (an arXiv stamp
and uneven margins included). Page 1 is rendered by running Ghostscript straight on the PDF
(ImageMagick, the fallback, copies the whole file to /tmp first and renders one at a time).

A book whose only files are PDFs always shows page 1: a provider's cover on it is replaced.
One chosen by hand (ticked in Fetch metadata, or uploaded) is kept, and so is any cover of a
book with an EPUB or other file beside its PDF.

A PDF book with no cover.jpg, or only the "Cover not available" card the old import wrote
for PDFs it could not render, gets page 1 as its cover (fix_cover), and one whose cover.jpg
is there but not flagged in the library shows it again.

Imports, Rebuild metadata and Redo PDF covers call it. A cover.jpg that already holds the same
picture is left alone, so running it again rewrites nothing."""

import hashlib
import os
import shutil
import subprocess
import threading
from datetime import datetime, UTC

from cps import logger

log = logger.create()

# Ghostscript runs below the web app's priority: a run over the library can take every idle core
# without making pages slow
NICE = 10
# Ghostscript resolution of the render: 850x1100 for US Letter, 827x1169 for A4. The largest
# cover thumbnail is 1020px tall (cps/tasks/thumbnail.py), so this is just over it.
RENDER_DPI = 100
# A PDF Ghostscript is still on after this long is given up on
RENDER_TIMEOUT = 120
# Page 1 as raw RGB on white, with the anti-aliasing and crop box ImageMagick asks for. Messages
# go to stderr so they can't end up in the image on stdout.
GS_ARGS = ['-q', '-sstdout=%stderr', '-dSAFER', '-dBATCH', '-dNOPAUSE', '-dNOPROMPT',
           '-dMaxBitmap=500000000', '-dAlignToPixels=0', '-dGridFitTT=2', '-sDEVICE=ppmraw',
           '-dTextAlphaBits=4', '-dGraphicsAlphaBits=4', f'-r{RENDER_DPI}', '-dPrinted=false',
           '-dUseCropBox', '-dFirstPage=1', '-dLastPage=1', '-sOutputFile=-']
JPEG_QUALITY = 88
# The old import's "Cover not available" card (282x400 JPEG): a book whose cover is this has none
PLACEHOLDER_SIZE = 19501
PLACEHOLDER_MD5 = '9173cbd4f0e3c7757e27fa5ec5a982dd'
# Ghostscript may run inside ImageMagick's process, so one render at a time on that path
_render_lock = threading.Lock()


def _gs_render(pdf_path):
    """Page 1 rendered by Ghostscript itself as a Wand image, or None when there is no gs or it
    could not render the file (ImageMagick then tries). Several can run at once."""
    gs = shutil.which('gs')
    if not gs:
        return None
    nice = shutil.which('nice')
    command = [nice, '-n', str(NICE), gs] if nice else [gs]
    done = subprocess.run([*command, *GS_ARGS, '-f', pdf_path], capture_output=True, timeout=RENDER_TIMEOUT)
    if done.returncode or not done.stdout.startswith(b'P6'):
        log.debug("Ghostscript could not render %s: %s", pdf_path, done.stderr[-300:])
        return None
    from wand.image import Image
    img = Image(blob=done.stdout, format='ppm')
    img.transform_colorspace('srgb')
    return img


def render_first_page(pdf_path):
    """Page 1 of the PDF as a white-backed Wand image (the caller closes it)."""
    from wand.color import Color
    from wand.image import Image
    img = _gs_render(pdf_path)
    if img is not None:
        return img
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


def page_cover_jpeg(pdf_path):
    """Page 1 of the PDF as JPEG bytes."""
    with render_first_page(pdf_path) as page:
        page.format = 'jpeg'
        page.compression_quality = JPEG_QUALITY
        return page.make_blob()


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
    """Write the PDF's first page as cover_path; False when the file already holds that picture.
    No half-written file is left in the book's folder."""
    jpeg = page_cover_jpeg(pdf_path)
    try:
        with open(cover_path, 'rb') as f:
            if f.read() == jpeg:
                return False
    except OSError:
        pass
    tmp_path = cover_path + '.writing'
    try:
        with open(tmp_path, 'wb') as f:
            f.write(jpeg)
        os.replace(tmp_path, cover_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    return True


def fix_cover(pdf_path, cover_path, has_cover, replace=False):
    """Make sure a PDF book has a cover it shows; True when cover.jpg or the book's flag changed.

    No cover.jpg, or only the placeholder card: page 1 becomes the cover. With `replace`, any
    other cover becomes page 1 too. Without it, the cover is kept, and one the book isn't
    flagged as having is shown again as it is."""
    if not os.path.isfile(pdf_path):
        return False
    if not os.path.isfile(cover_path) or is_placeholder(cover_path):
        save_page_cover(pdf_path, cover_path)
        return True
    if replace:
        return save_page_cover(pdf_path, cover_path) or not has_cover
    return not has_cover


def _hand_cover(book_id, store=None):
    """True when the book's cover was chosen by hand, or when that cannot be told (keep it).
    A caller doing many books passes its open CWA_DB as store."""
    try:
        if store is None:
            from cwa_db import CWA_DB
            store = CWA_DB()
        return store.has_hand_cover(book_id)
    except Exception as e:
        log.debug("Could not tell whether book %s has a hand-picked cover: %s", book_id, e)
        return True


def cover_job(book, library_path, store=None, replace=True):
    """(PDF path, cover.jpg path, has a cover, replace its cover) of a PDF book, or None for a
    book with no PDF. The cover is replaced by page 1 when PDFs are the book's only files and
    nobody chose its cover by hand (asked of store, a CWA_DB, when given). With replace False,
    a cover the book has is kept, and nobody is asked."""
    pdf = next((d for d in book.data if d.format.upper() == 'PDF'), None)
    if pdf is None:
        return None
    folder = os.path.join(library_path, book.path)
    only_pdf = all(d.format.upper() == 'PDF' for d in book.data)
    replace = replace and only_pdf and not _hand_cover(book.id, store)
    return (os.path.join(folder, pdf.name + '.pdf'), os.path.join(folder, 'cover.jpg'),
            bool(book.has_cover), replace)


def try_fix_cover(job, book_id=None):
    """fix_cover that logs a failure instead of raising (one bad PDF must not stop a run)."""
    if not job:
        return False
    try:
        return fix_cover(*job)
    except Exception as ex:
        log.warning("Could not make the cover of book %s: %s", book_id, ex)
        return False


def mark_cover_changed(book):
    """Flag the book as having a cover and bump last_modified: cover URLs and cached thumbnails
    are keyed on it. The caller commits."""
    book.has_cover = 1
    book.last_modified = datetime.now(UTC)


def fix_book_cover(book, library_path):
    """Make a PDF book's page 1 cover in its folder; True when it changed. The caller commits."""
    if not try_fix_cover(cover_job(book, library_path), book.id):
        return False
    mark_cover_changed(book)
    return True


def available():
    """True when ImageMagick (through Wand) is there to read and write the images with."""
    try:
        import wand.image  # noqa: F401
        return True
    except ImportError:
        return False


def make_new_book_cover(book_id, library_path):
    """Give a newly imported PDF its page 1 cover; True when it changed."""
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
