# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Title-card covers for EPUBs that carry no cover image.

Without a cover image, calibre renders the book's first page as its cover, edge to
edge with no margins, so the text is clipped at the cover's left side. Instead the
ingest processor gives such books a plain typographic card: title, a short accent
rule and the authors on Lily's paper colour, at A4 proportions to fill the cover box.

    python3 title_card.py regenerate [--dry-run] [--backup-dir DIR] (--all | BOOK_ID ...)

replaces the covers of books already in the library, skipping any whose EPUB has a
cover image of its own and any that also have a PDF.
"""

import argparse
import os
import posixpath
import re
import shutil
import sqlite3
import sys
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree

# A4 at 150 dpi, the cover box's 1:1.414.
CARD_SIZE = (1240, 1754)

# Light-theme tokens from cps/static/css/lily.css.
PAPER = "#F4EFE7"
INK = "#2B2326"
MUTED = "#62575B"
ACCENT = "#9E2F55"

# P052 (a Palatino clone) ships with ghostscript; calibre bundles Liberation Serif.
_FONT_DIRS = ("/usr/share/fonts/opentype/urw-base35", "/app/calibre/resources/fonts/liberation")
TITLE_FONTS = ("P052-Bold.otf", "LiberationSerif-Bold.ttf")
AUTHOR_FONTS = ("P052-Italic.otf", "LiberationSerif-Italic.ttf")

_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")
_COVER_REFS = {"cover", "other.ms-coverimage-standard", "other.ms-coverimage"}
_NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def _opf(zf: zipfile.ZipFile):
    """The package document and its folder inside the zip."""
    container = ElementTree.fromstring(zf.read("META-INF/container.xml"))
    rootfile = container.find(".//c:rootfile", _NS)
    if rootfile is None:
        raise ValueError("container.xml names no package document")
    path = rootfile.get("full-path", "")
    return ElementTree.fromstring(zf.read(path)), posixpath.dirname(path)


def _join(base: str, href: str) -> str:
    return posixpath.normpath(posixpath.join(base, href.split("#")[0]))


def epub_has_cover_image(path) -> bool:
    """Whether calibre will find a picture to use as this EPUB's cover.

    True for a declared cover image (EPUB 3 cover-image, EPUB 2 <meta name="cover">,
    or a guide reference to an image) and for a cover or first page that holds an
    image, which is how calibre's own EPUBs wrap their covers. Unreadable files count
    as having one, so a broken EPUB is never given a card by mistake.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            opf, base = _opf(zf)
            items = {}
            for item in opf.iterfind(".//opf:manifest/opf:item", _NS):
                items[item.get("id")] = item
                media = item.get("media-type", "")
                if "cover-image" in item.get("properties", "").split() and media.startswith("image/"):
                    return True

            for meta in opf.iterfind(".//opf:metadata/opf:meta", _NS):
                if meta.get("name") == "cover":
                    item = items.get(meta.get("content"))
                    if item is not None and item.get("media-type", "").startswith("image/"):
                        return True

            page = None
            for ref in opf.iterfind(".//opf:guide/opf:reference", _NS):
                kind = ref.get("type", "").lower()
                href = ref.get("href", "")
                if kind in _COVER_REFS and href.lower().endswith(_IMAGE_EXT):
                    return True
                if kind in ("cover", "title-page") and page is None:
                    page = _join(base, href)

            if page is None:
                first = opf.find(".//opf:spine/opf:itemref", _NS)
                item = items.get(first.get("idref")) if first is not None else None
                if item is None:
                    return False
                page = _join(base, item.get("href", ""))

            markup = zf.read(page).decode("utf-8", errors="replace")
            return re.search(r"<(img|image|svg:image)\b", markup, re.IGNORECASE) is not None
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, ElementTree.ParseError):
        return True


def epub_title_and_authors(path):
    """dc:title and the dc:creator names, falling back to the file name."""
    title, authors = "", []
    try:
        with zipfile.ZipFile(path) as zf:
            opf, _ = _opf(zf)
        meta = opf.find(".//opf:metadata", _NS)
        if meta is not None:
            node = meta.find("dc:title", _NS)
            title = (node.text or "").strip() if node is not None else ""
            authors = [(n.text or "").strip() for n in meta.iterfind("dc:creator", _NS) if (n.text or "").strip()]
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, ElementTree.ParseError):
        pass
    return title or Path(path).stem, authors


def _font(names):
    for folder in _FONT_DIRS:
        for name in names:
            candidate = os.path.join(folder, name)
            if os.path.exists(candidate):
                return candidate
    return None


def _pieces(text):
    """Words split after their hyphens, each tagged with whether a space comes before it."""
    for word in text.split():
        for i, piece in enumerate(re.split(r"(?<=-)", word)):
            if piece:
                yield piece, i == 0


def _wrap(draw, image, text, width):
    """Greedy line breaks at spaces and after hyphens; a piece wider than a line is cut by letter."""
    def wide(s):
        return draw.get_font_metrics(image, s).text_width > width

    lines, line = [], ""
    for piece, spaced in _pieces(text):
        trial = (f"{line} {piece}" if spaced else line + piece) if line else piece
        if line and wide(trial):
            lines.append(line)
            line = piece
        else:
            line = trial
        while wide(line) and len(line) > 1:
            cut = len(line) - 1
            while cut > 1 and wide(line[:cut]):
                cut -= 1
            lines.append(line[:cut])
            line = line[cut:]
    if line:
        lines.append(line)
    return lines


def _fit(draw, image, text, width, sizes, max_lines):
    """The largest size whose wrapped text fits in max_lines, clipped with an ellipsis at the smallest."""
    for size in sizes:
        draw.font_size = size
        lines = _wrap(draw, image, text, width)
        if len(lines) <= max_lines:
            return size, lines
    lines = lines[:max_lines]
    while len(lines[-1]) > 1 and draw.get_font_metrics(image, lines[-1] + "…").text_width > width:
        lines[-1] = lines[-1].rsplit(" ", 1)[0] if " " in lines[-1] else lines[-1][:-1]
    lines[-1] += "…"
    return sizes[-1], lines


def render_title_card(title: str, authors, out_path) -> None:
    """Write a JPEG title card for the book to out_path."""
    from wand.color import Color
    from wand.drawing import Drawing
    from wand.image import Image

    width, height = CARD_SIZE
    margin = 140
    text_width = width - 2 * margin
    author_line = ", ".join(authors)

    with Image(width=width, height=height, background=Color(PAPER)) as image, Drawing() as draw:
        draw.text_antialias = True
        title_font = _font(TITLE_FONTS)
        if title_font:
            draw.font = title_font
        size, lines = _fit(draw, image, title, text_width, (116, 100, 86, 74, 64), 7)
        line_height = round(size * 1.18)
        rule_gap = round(size * 0.65)

        alines, asize, author_font = [], 0, _font(AUTHOR_FONTS)
        if author_line:
            if author_font:
                draw.font = author_font
            asize, alines = _fit(draw, image, author_line, text_width, (60, 52, 46), 3)

        # The block starts 30% down, or higher when a long title would run past the foot.
        block = len(lines) * line_height + rule_gap + 8 + (40 + len(alines) * round(asize * 1.3) if alines else 0)
        y = max(margin, min(round(height * 0.30), height - margin - block))

        if title_font:
            draw.font = title_font
        draw.font_size = size
        draw.fill_color = Color(INK)
        for line in lines:
            y += line_height
            draw.text(margin, y, line)

        y += rule_gap
        draw.fill_color = Color(ACCENT)
        draw.rectangle(left=margin, top=y, width=120, height=8)

        if alines:
            if author_font:
                draw.font = author_font
            draw.font_size = asize
            draw.fill_color = Color(MUTED)
            y += 40
            for line in alines:
                y += round(asize * 1.3)
                draw.text(margin, y, line)

        draw(image)
        image.format = "jpeg"
        image.compression_quality = 90
        image.save(filename=str(out_path))


def card_for_epub(epub_path, out_path) -> bool:
    """Render a title card for an EPUB with no cover image. Returns whether it wrote one."""
    if epub_has_cover_image(epub_path):
        return False
    title, authors = epub_title_and_authors(epub_path)
    render_title_card(title, authors, out_path)
    return True


def regenerate(library: str, book_ids=None, dry_run=False, backup_dir=None) -> int:
    """Swap the rendered first-page covers of existing books for title cards.

    Covers only books whose EPUB has no cover image and that have no PDF (a PDF's cover
    is a real page render, margins and all). book_ids=None means every such book.
    Each replaced cover.jpg is first copied to backup_dir as <book id>.jpg.
    """
    db = sqlite3.connect(os.path.join(library, "metadata.db"))
    # Calibre's update trigger calls title_sort(); the title is untouched, so identity will do.
    db.create_function("title_sort", 1, lambda title: title)
    if backup_dir and not dry_run:
        os.makedirs(backup_dir, exist_ok=True)
    if book_ids is None:
        book_ids = [r[0] for r in db.execute("SELECT DISTINCT book FROM data WHERE format = 'EPUB' ORDER BY book")]
    changed = skipped = failed = 0
    try:
        for book_id in book_ids:
            row = db.execute("SELECT path FROM books WHERE id = ?", (book_id,)).fetchone()
            formats = dict(db.execute("SELECT format, name FROM data WHERE book = ?", (book_id,)).fetchall())
            if not row or "EPUB" not in formats or "PDF" in formats:
                skipped += 1
                continue
            folder = os.path.join(library, row[0])
            epub = os.path.join(folder, formats["EPUB"] + ".epub")
            if epub_has_cover_image(epub):
                skipped += 1
                continue
            cover = os.path.join(folder, "cover.jpg")
            if dry_run:
                print(f"[title-card] book {book_id}: would get a title card ({row[0]})")
                changed += 1
                continue
            try:
                if backup_dir and os.path.exists(cover):
                    shutil.copy2(cover, os.path.join(backup_dir, f"{book_id}.jpg"))
                title, authors = epub_title_and_authors(epub)
                tmp = cover + ".title-card.tmp"
                render_title_card(title, authors, tmp)
                os.replace(tmp, cover)
            except Exception as e:
                failed += 1
                print(f"[title-card] book {book_id}: FAILED, cover left as it was: {e}")
                continue
            stamp = time.strftime("%Y-%m-%d %H:%M:%S.000000+00:00", time.gmtime())
            db.execute("UPDATE books SET has_cover = 1, last_modified = ? WHERE id = ?", (stamp, book_id))
            db.commit()
            changed += 1
            print(f"[title-card] book {book_id}: title card written ({row[0]})")
    finally:
        db.close()
    verb = "would get" if dry_run else "got"
    print(f"[title-card] {changed} {verb} a title card, {skipped} skipped (cover image or PDF), {failed} failed")
    return changed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    regen = sub.add_parser("regenerate", help="give existing books with no cover image a title card")
    regen.add_argument("--library", default="/calibre-library")
    regen.add_argument("--all", action="store_true", help="every book with an EPUB, instead of listed ids")
    regen.add_argument("--dry-run", action="store_true", help="list the books, change nothing")
    regen.add_argument("--backup-dir", help="copy each replaced cover.jpg here as <book id>.jpg first")
    regen.add_argument("book_ids", nargs="*", type=int)
    args = parser.parse_args(argv)
    if args.all == bool(args.book_ids):
        parser.error("give book ids or --all, not both")
    regenerate(args.library, None if args.all else args.book_ids, args.dry_run, args.backup_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
