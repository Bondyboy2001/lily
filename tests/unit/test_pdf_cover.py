"""A PDF's cover is centred on what page 1 prints (cps/pdf_cover.py)."""
import sqlite3

import pytest

from cps import pdf_cover
from cps.pdf_cover import balanced_crop, ink_columns, same_page

from .lily_env import lily_env


def _page(width, height, blocks):
    """A white grey-level page with black rectangles (x0, y0, x1, y1)."""
    pixels = bytearray([255]) * (width * height)
    for x0, y0, x1, y1 in blocks:
        for y in range(y0, y1):
            pixels[y * width + x0:y * width + x1] = bytes(x1 - x0)
    return bytes(pixels)


@pytest.mark.unit
def test_ink_columns_finds_the_print_and_ignores_a_speck():
    page = _page(100, 140, [(20, 30, 60, 100), (90, 10, 91, 11)])
    assert ink_columns(page, 100, 140) == (20, 59)
    assert ink_columns(_page(100, 140, []), 100, 140) is None


@pytest.mark.unit
def test_lopsided_page_gets_even_margins():
    # The arXiv paper: Letter page, print from the stamp at 60 to 920 of 1275
    left, right = balanced_crop(1275, 1650, 60, 920)
    assert left == 0 and right == 921 + 60
    # An even page wider than the A4 tile loses a little off both sides instead of one
    left, right = balanced_crop(1275, 1650, 150, 1124)
    assert 150 - left == right - 1125 and right - left <= 1650 / 1.414 + 1


@pytest.mark.unit
def test_pages_that_need_no_crop_are_left_alone():
    assert balanced_crop(1166, 1650, 150, 1015) is None   # already even and A4 shaped
    assert balanced_crop(1275, 1650, 0, 1100) is None     # print runs off the edge: a scan or cover art
    assert balanced_crop(1166, 1650, 140, 1015) is None   # a few pixels out


@pytest.mark.unit
def test_same_page_tells_a_render_from_cover_art():
    page = _page(24, 32, [(4, 6, 18, 26)])
    assert same_page(page, _page(24, 32, [(4, 6, 18, 25)]))
    assert not same_page(page, bytes(24 * 32))
    assert not same_page(page, page[:-1])


@pytest.mark.unit
def test_recentre_cover_with_imagemagick(tmp_path):
    wand_image = pytest.importorskip("wand.image", exc_type=ImportError)
    from wand.color import Color
    from wand.drawing import Drawing
    from wand.exceptions import WandException
    pdf, cover = str(tmp_path / "book.pdf"), str(tmp_path / "cover.jpg")
    with wand_image.Image(width=1275, height=1650, background=Color("white"), resolution=150) as img, \
            Drawing() as draw:
        draw.fill_color = Color("black")
        draw.rectangle(left=60, top=200, right=920, bottom=1400)
        draw(img)
        try:
            img.save(filename=pdf)
        except WandException as ex:
            pytest.skip("ImageMagick cannot write PDFs here: %s" % ex)
        img.format = "jpeg"
        img.save(filename=cover)
    try:
        assert pdf_cover.recentre_cover(pdf, cover)
    except WandException as ex:
        pytest.skip("ImageMagick cannot read PDFs here: %s" % ex)
    with wand_image.Image(filename=cover) as done:
        # The render's pixel size depends on the PDF's page size; the proportions do not
        assert abs(done.width / done.height - 981 / 1650) < 0.01
    assert not pdf_cover.recentre_cover(pdf, cover)       # already centred


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as env:
        yield env


@pytest.mark.unit
def test_rebuild_centres_pdf_covers_after_the_lookup(env, monkeypatch):
    from cps import helper, metadata_helper
    from cps.tasks.metadata_rebuild import TaskRebuildMetadata
    pdf = env.add_book("Paper", fmt="PDF")
    epub = env.add_book("Novel", fmt="EPUB")
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.create_function("title_sort", 1, lambda t: t)
    con.execute("UPDATE books SET has_cover = 1")
    con.commit()
    before = dict(con.execute("SELECT id, last_modified FROM books"))
    order, refreshed = [], []
    monkeypatch.setattr(metadata_helper, "fetch_and_apply_metadata",
                        lambda book_id, force=False: order.append(("lookup", book_id)) or False)
    monkeypatch.setattr(pdf_cover, "available", lambda: True)

    def fake_recentre(pdf_path, cover_path):
        order.append(("cover", pdf_path, cover_path))
        return True
    monkeypatch.setattr(pdf_cover, "recentre_cover", fake_recentre)
    monkeypatch.setattr(helper, "replace_cover_thumbnail_cache", refreshed.append)
    monkeypatch.setattr("cps.duplicate_index.mark_duplicate_index_pending", lambda reason=None: None)

    task = TaskRebuildMetadata(workers=1)
    with env.app.test_request_context():
        task.start(None)

    folder = str(env.library_dir / "Test Author" / "Paper")
    assert order == [("lookup", pdf), ("cover", folder + "/Paper.pdf", folder + "/cover.jpg"), ("lookup", epub)]
    assert refreshed == [pdf] and task.covers == 1
    assert str(task.message) == "Done: 2 books checked, 0 updated, 1 covers centred"
    after = dict(con.execute("SELECT id, last_modified FROM books"))
    con.close()
    assert after[pdf] != before[pdf] and after[epub] == before[epub]
