"""Which EPUBs get a title card instead of calibre's first-page render (scripts/title_card.py)."""

import sqlite3
import zipfile

import pytest

import title_card

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

TITLE_PAGE = "<html><body><h1>Salt</h1><p>Tomas Brandt</p></body></html>"


def make_epub(path, manifest="", metadata="", guide="", first_page=TITLE_PAGE, extra=None):
    opf = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>A Short History of Salt</dc:title>
    <dc:creator>Tomas Brandt</dc:creator>
    <dc:creator>Ana Ruiz</dc:creator>
    {metadata}
  </metadata>
  <manifest>
    <item id="p1" href="text/p1.xhtml" media-type="application/xhtml+xml"/>
    {manifest}
  </manifest>
  <spine><itemref idref="p1"/></spine>
  {guide}
</package>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", CONTAINER)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/text/p1.xhtml", first_page)
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return path


def test_text_only_first_page_has_no_cover(tmp_path):
    assert not title_card.epub_has_cover_image(make_epub(tmp_path / "a.epub"))


@pytest.mark.parametrize("kwargs", [
    {"manifest": '<item id="c" href="c.jpg" media-type="image/jpeg" properties="cover-image"/>'},
    {"manifest": '<item id="c" href="c.jpg" media-type="image/jpeg"/>', "metadata": '<meta name="cover" content="c"/>'},
    {"guide": '<guide><reference type="cover" href="images/c.png"/></guide>'},
    {"first_page": '<html><body><svg><image href="../c.jpg"/></svg></body></html>'},
    {"first_page": '<html><body><img src="../c.jpg"/></body></html>'},
], ids=["epub3-cover-image", "epub2-meta-cover", "guide-image", "svg-wrapper-page", "img-page"])
def test_declared_or_pictured_covers_count(tmp_path, kwargs):
    assert title_card.epub_has_cover_image(make_epub(tmp_path / "a.epub", **kwargs))


def test_guide_cover_page_is_checked_instead_of_first_page(tmp_path):
    epub = make_epub(
        tmp_path / "a.epub",
        guide='<guide><reference type="cover" href="text/cover.xhtml"/></guide>',
        extra={"OEBPS/text/cover.xhtml": '<html><body><img src="../c.jpg"/></body></html>'},
    )
    assert title_card.epub_has_cover_image(epub)


def test_meta_cover_pointing_at_a_page_does_not_count(tmp_path):
    epub = make_epub(tmp_path / "a.epub", metadata='<meta name="cover" content="p1"/>')
    assert not title_card.epub_has_cover_image(epub)


def test_unreadable_epub_is_left_to_calibre(tmp_path):
    bad = tmp_path / "bad.epub"
    bad.write_bytes(b"not a zip")
    assert title_card.epub_has_cover_image(bad)
    assert title_card.card_for_epub(bad, tmp_path / "card.jpg") is False
    assert not (tmp_path / "card.jpg").exists()


def test_title_and_authors(tmp_path):
    assert title_card.epub_title_and_authors(make_epub(tmp_path / "a.epub")) == (
        "A Short History of Salt", ["Tomas Brandt", "Ana Ruiz"])


def test_title_falls_back_to_file_name(tmp_path):
    bad = tmp_path / "Some Book.epub"
    bad.write_bytes(b"not a zip")
    assert title_card.epub_title_and_authors(bad) == ("Some Book", [])


def test_card_for_epub_skips_books_with_covers(tmp_path, monkeypatch):
    drawn = []
    monkeypatch.setattr(title_card, "render_title_card", lambda *args: drawn.append(args))
    with_cover = make_epub(tmp_path / "c.epub", guide='<guide><reference type="cover" href="c.jpg"/></guide>')
    assert title_card.card_for_epub(with_cover, tmp_path / "x.jpg") is False
    assert title_card.card_for_epub(make_epub(tmp_path / "n.epub"), tmp_path / "y.jpg") is True
    assert drawn == [("A Short History of Salt", ["Tomas Brandt", "Ana Ruiz"], tmp_path / "y.jpg")]


def make_library(tmp_path):
    """Books 1-3: coverless EPUB, coverless EPUB + PDF, EPUB with a cover image."""
    lib = tmp_path / "lib"
    lib.mkdir()
    db = sqlite3.connect(lib / "metadata.db")
    db.executescript("""
        CREATE TABLE books (id INTEGER PRIMARY KEY, path TEXT, has_cover BOOL, last_modified TEXT);
        CREATE TABLE data (book INTEGER, format TEXT, name TEXT);
    """)
    for book_id, formats, guide in [(1, ["EPUB"], ""), (2, ["EPUB", "PDF"], ""),
                                    (3, ["EPUB"], '<guide><reference type="cover" href="c.jpg"/></guide>')]:
        folder = lib / f"A/B ({book_id})"
        folder.mkdir(parents=True)
        make_epub(folder / "b.epub", guide=guide)
        (folder / "cover.jpg").write_bytes(b"old")
        db.execute("INSERT INTO books VALUES (?, ?, 1, 'then')", (book_id, f"A/B ({book_id})"))
        db.executemany("INSERT INTO data VALUES (?, ?, 'b')", [(book_id, f) for f in formats])
    db.commit()
    db.close()
    return lib


def test_regenerate_all_replaces_only_coverless_epub_only_books(tmp_path, monkeypatch):
    monkeypatch.setattr(title_card, "render_title_card", lambda t, a, out: open(out, "wb").write(b"card"))
    lib = make_library(tmp_path)

    assert title_card.regenerate(str(lib), dry_run=True) == 1
    assert (lib / "A/B (1)/cover.jpg").read_bytes() == b"old"

    backups = tmp_path / "backups"
    assert title_card.regenerate(str(lib), backup_dir=str(backups)) == 1
    assert (lib / "A/B (1)/cover.jpg").read_bytes() == b"card"
    assert (lib / "A/B (2)/cover.jpg").read_bytes() == b"old"
    assert (lib / "A/B (3)/cover.jpg").read_bytes() == b"old"
    assert [p.name for p in backups.iterdir()] == ["1.jpg"] and (backups / "1.jpg").read_bytes() == b"old"
    stamps = dict(sqlite3.connect(lib / "metadata.db").execute("SELECT id, last_modified FROM books"))
    assert stamps[1] != "then" and stamps[2] == stamps[3] == "then"
