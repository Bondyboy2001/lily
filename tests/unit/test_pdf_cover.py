"""A PDF's cover is page 1, centred on what it prints (cps/pdf_cover.py)."""
import hashlib
import sqlite3

import pytest

from cps import pdf_cover
from cps.pdf_cover import balanced_crop, ink_columns, left_stamp, same_page

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
    # A Letter page whose print (60 to 920 of 1275) sits left of centre
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


STAMP_PAGE = [(19, 150, 33, 400), (80, 60, 300, 460)]


@pytest.mark.unit
def test_left_stamp_finds_a_tall_thin_band():
    assert left_stamp(_page(400, 518, STAMP_PAGE), 400, 518) == (19, 32)
    # Below-threshold columns inside the glyph band do not split it
    page = _page(400, 518, [(19, 150, 25, 400), (27, 150, 33, 400), (80, 60, 300, 460)])
    assert left_stamp(page, 400, 518) == (19, 32)


@pytest.mark.unit
def test_left_stamp_leaves_other_left_print_alone():
    page = _page(400, 518, [(19, 150, 33, 180), (80, 60, 300, 460)])
    assert left_stamp(page, 400, 518) is None            # a short logo, not a stamp
    page = _page(400, 518, [(19, 150, 45, 400), (80, 60, 300, 460)])
    assert left_stamp(page, 400, 518) is None            # wider than 5% of the page
    page = _page(400, 518, [(19, 150, 33, 400), (33, 60, 300, 460)])
    assert left_stamp(page, 400, 518) is None            # no blank gap before the print
    page = _page(400, 518, [(55, 150, 69, 400), (100, 60, 300, 460)])
    assert left_stamp(page, 400, 518) is None            # starts outside the left zone
    assert left_stamp(_page(400, 518, [(19, 150, 33, 400)]), 400, 518) is None  # nothing right of it
    page = _page(400, 518, [(0, 150, 14, 400), (80, 60, 300, 460)])
    assert left_stamp(page, 400, 518) is None            # a scan border touching the edge


@pytest.mark.unit
def test_ink_columns_with_start_skips_the_stamp():
    assert ink_columns(_page(400, 518, STAMP_PAGE), 400, 518, start=33) == (80, 299)


@pytest.mark.unit
def test_balanced_crop_crops_the_stamp_out():
    left, right = balanced_crop(400, 518, 80, 299, stamp_end=32)
    assert left > 32
    assert 80 - left == right - 300


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


@pytest.mark.unit
def test_recentre_cover_crops_an_arxiv_stamp(tmp_path):
    wand_image = pytest.importorskip("wand.image", exc_type=ImportError)
    from wand.color import Color
    from wand.drawing import Drawing
    from wand.exceptions import WandException
    pdf, cover = str(tmp_path / "stamp.pdf"), str(tmp_path / "cover.jpg")
    with wand_image.Image(width=1275, height=1650, background=Color("white"), resolution=150) as img, \
            Drawing() as draw:
        draw.fill_color = Color("black")
        draw.rectangle(left=60, top=450, right=105, bottom=1250)    # the stamp down the left margin
        draw.rectangle(left=250, top=200, right=920, bottom=1400)   # the body print
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
    # The stamp is cropped out: the margins are those of the body print alone, the left one
    # capped to stay inside the blank gap beside the stamp
    small_h = round(1650 * pdf_cover.MEASURE_WIDTH / 1275)
    first, last = round(250 * pdf_cover.MEASURE_WIDTH / 1275), round(920 * pdf_cover.MEASURE_WIDTH / 1275) - 1
    crop = balanced_crop(pdf_cover.MEASURE_WIDTH, small_h, first, last,
                         stamp_end=round(105 * pdf_cover.MEASURE_WIDTH / 1275))
    with wand_image.Image(filename=cover) as done:
        assert abs(done.width / done.height - (crop[1] - crop[0]) / small_h) < 0.01
    assert not pdf_cover.recentre_cover(pdf, cover)       # already centred


@pytest.mark.unit
def test_replace_cover_with_imagemagick(tmp_path):
    wand_image = pytest.importorskip("wand.image", exc_type=ImportError)
    from wand.color import Color
    from wand.exceptions import WandException
    pdf, cover = str(tmp_path / "book.pdf"), str(tmp_path / "cover.jpg")
    with wand_image.Image(width=1275, height=1650, background=Color("white"), resolution=150) as img:
        try:
            img.save(filename=pdf)
        except WandException as ex:
            pytest.skip("ImageMagick cannot write PDFs here: %s" % ex)
    # A provider's cover: dark and book shaped
    with wand_image.Image(width=400, height=600, background=Color("navy")) as art:
        art.format = "jpeg"
        art.save(filename=cover)
    try:
        assert not pdf_cover.recentre_cover(pdf, cover)        # kept without replace
    except WandException as ex:
        pytest.skip("ImageMagick cannot read PDFs here: %s" % ex)
    assert pdf_cover.recentre_cover(pdf, cover, replace=True)
    with wand_image.Image(filename=cover) as done:
        assert abs(done.width / done.height - 1275 / 1650) < 0.01
    assert not pdf_cover.recentre_cover(pdf, cover, replace=True)   # already page 1


def _fix_cover_world(tmp_path, monkeypatch, cover=None):
    """A book folder with a PDF and maybe a cover.jpg; rendering and centring are recorded."""
    pdf, path = tmp_path / "book.pdf", tmp_path / "cover.jpg"
    pdf.write_bytes(b"%PDF")
    if cover is not None:
        path.write_bytes(cover)
    calls = []
    monkeypatch.setattr(pdf_cover, "save_page_cover", lambda p, c: calls.append("page"))
    monkeypatch.setattr(pdf_cover, "recentre_cover",
                        lambda p, c, replace=False: calls.append("replace" if replace else "centre") or False)
    return str(pdf), str(path), calls


@pytest.mark.unit
@pytest.mark.parametrize("has_cover", [True, False])
def test_a_pdf_with_no_cover_file_gets_its_first_page(tmp_path, monkeypatch, has_cover):
    pdf, cover, calls = _fix_cover_world(tmp_path, monkeypatch)
    assert pdf_cover.fix_cover(pdf, cover, has_cover)
    assert calls == ["page"]


@pytest.mark.unit
def test_the_old_imports_placeholder_card_is_replaced_by_the_first_page(tmp_path, monkeypatch):
    card = b"x" * pdf_cover.PLACEHOLDER_SIZE
    monkeypatch.setattr(pdf_cover, "PLACEHOLDER_MD5", hashlib.md5(card).hexdigest())
    pdf, cover, calls = _fix_cover_world(tmp_path, monkeypatch, cover=card)
    assert pdf_cover.is_placeholder(cover)
    assert pdf_cover.fix_cover(pdf, cover, True)
    assert calls == ["page"]
    # Same size, other bytes: a real cover
    pdf, cover, calls = _fix_cover_world(tmp_path, monkeypatch, cover=b"y" * pdf_cover.PLACEHOLDER_SIZE)
    assert not pdf_cover.is_placeholder(cover)


@pytest.mark.unit
def test_a_cover_file_the_book_lost_its_flag_for_is_shown_again_as_it_is(tmp_path, monkeypatch):
    pdf, cover, calls = _fix_cover_world(tmp_path, monkeypatch, cover=b"real cover")
    assert pdf_cover.fix_cover(pdf, cover, False)
    assert calls == []
    # Flagged already: only centring is tried, and it found nothing to do
    assert not pdf_cover.fix_cover(pdf, cover, True)
    assert calls == ["centre"]


@pytest.mark.unit
def test_replace_hands_any_cover_to_page_one(tmp_path, monkeypatch):
    pdf, cover, calls = _fix_cover_world(tmp_path, monkeypatch, cover=b"provider cover")
    assert not pdf_cover.fix_cover(pdf, cover, True, replace=True)
    assert calls == ["replace"]
    # Unflagged: shown again, whatever the replace found
    assert pdf_cover.fix_cover(pdf, cover, False, replace=True)
    assert calls == ["replace", "replace"]


@pytest.mark.unit
def test_a_book_whose_pdf_is_missing_is_left_alone(tmp_path, monkeypatch):
    pdf, cover, calls = _fix_cover_world(tmp_path, monkeypatch)
    (tmp_path / "book.pdf").unlink()
    assert not pdf_cover.fix_cover(pdf, cover, False)
    assert calls == []


@pytest.mark.unit
def test_save_page_cover_with_imagemagick(tmp_path):
    wand_image = pytest.importorskip("wand.image", exc_type=ImportError)
    from wand.color import Color
    from wand.exceptions import WandException
    pdf, cover = str(tmp_path / "book.pdf"), str(tmp_path / "cover.jpg")
    with wand_image.Image(width=1275, height=1650, background=Color("white"), resolution=150) as img:
        try:
            img.save(filename=pdf)
        except WandException as ex:
            pytest.skip("ImageMagick cannot write PDFs here: %s" % ex)
    try:
        pdf_cover.save_page_cover(pdf, cover)
    except WandException as ex:
        pytest.skip("ImageMagick cannot read PDFs here: %s" % ex)
    with wand_image.Image(filename=cover) as done:
        assert done.format == "JPEG" and abs(done.width / done.height - 1275 / 1650) < 0.01


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
                        lambda book_id, force=False, unanswered=None: order.append(("lookup", book_id)) or False)
    monkeypatch.setattr(pdf_cover, "available", lambda: True)

    def fake_fix(pdf_path, cover_path, has_cover, replace=False):
        order.append(("cover", pdf_path, cover_path))
        return True
    monkeypatch.setattr(pdf_cover, "fix_cover", fake_fix)
    monkeypatch.setattr(helper, "replace_cover_thumbnail_cache", refreshed.append)
    monkeypatch.setattr("cps.duplicate_index.mark_duplicate_index_pending", lambda reason=None: None)

    task = TaskRebuildMetadata(workers=1)
    with env.app.test_request_context():
        task.start(None)

    folder = str(env.library_dir / "Test Author" / "Paper")
    assert order == [("lookup", pdf), ("cover", folder + "/Paper.pdf", folder + "/cover.jpg"), ("lookup", epub)]
    assert refreshed == [pdf] and task.covers == 1
    assert str(task.message) == "Done: 2 books checked, 0 updated, 1 covers made or centred"
    after = dict(con.execute("SELECT id, last_modified FROM books"))
    con.close()
    assert after[pdf] != before[pdf] and after[epub] == before[epub]


@pytest.mark.unit
def test_rebuild_flags_a_pdf_that_had_no_cover(env, monkeypatch):
    from cps import helper, metadata_helper
    from cps.tasks.metadata_rebuild import TaskRebuildMetadata
    pdf = env.add_book("Paper", fmt="PDF")
    seen, refreshed = [], []
    monkeypatch.setattr(metadata_helper, "fetch_and_apply_metadata",
                        lambda book_id, force=False, unanswered=None: False)
    monkeypatch.setattr(pdf_cover, "available", lambda: True)
    monkeypatch.setattr(pdf_cover, "fix_cover", lambda p, c, has_cover, replace=False: seen.append(has_cover) or True)
    monkeypatch.setattr(helper, "replace_cover_thumbnail_cache", refreshed.append)
    monkeypatch.setattr("cps.duplicate_index.mark_duplicate_index_pending", lambda reason=None: None)

    task = TaskRebuildMetadata(workers=1)
    with env.app.test_request_context():
        task.start(None)

    con = sqlite3.connect(env.library_dir / "metadata.db")
    has_cover = con.execute("SELECT has_cover FROM books WHERE id = ?", (pdf,)).fetchone()[0]
    con.close()
    assert seen == [False] and has_cover == 1 and refreshed == [pdf] and task.covers == 1


@pytest.mark.unit
def test_cover_job_replaces_only_a_pdf_only_books_cover_not_picked_by_hand(env):
    from cps import db
    from cwa_db import CWA_DB
    paper = env.add_book("Paper", fmt="PDF")
    picked = env.add_book("Picked", fmt="PDF")
    novel = env.add_book("Novel", fmt="EPUB")
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.execute("INSERT INTO data (book, format, uncompressed_size, name) VALUES (?, 'PDF', 1, 'Novel')", (novel,))
    con.commit()
    con.close()
    CWA_DB().save_hand_cover(picked)
    cdb = db.CalibreDB(expire_on_commit=False, init=True)
    try:
        jobs = {i: pdf_cover.cover_job(cdb.get_book(i), str(env.library_dir)) for i in (paper, picked, novel)}
    finally:
        cdb.session.close()
    assert jobs[paper][3] and not jobs[picked][3] and not jobs[novel][3]
