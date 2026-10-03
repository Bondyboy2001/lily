"""A PDF's cover is a plain picture of page 1, exactly as printed (cps/pdf_cover.py)."""
import hashlib
import sqlite3

import pytest

from cps import pdf_cover

from .lily_env import lily_env


def _fix_cover_world(tmp_path, monkeypatch, cover=None, page=b"page one"):
    """A book folder with a PDF and maybe a cover.jpg; page 1 "renders" as `page`."""
    pdf, path = tmp_path / "book.pdf", tmp_path / "cover.jpg"
    pdf.write_bytes(b"%PDF")
    if cover is not None:
        path.write_bytes(cover)
    renders = []
    monkeypatch.setattr(pdf_cover, "page_cover_jpeg", lambda p: renders.append(p) or page)
    return str(pdf), str(path), renders


@pytest.mark.unit
@pytest.mark.parametrize("has_cover", [True, False])
def test_a_pdf_with_no_cover_file_gets_its_first_page(tmp_path, monkeypatch, has_cover):
    pdf, cover, renders = _fix_cover_world(tmp_path, monkeypatch)
    assert pdf_cover.fix_cover(pdf, cover, has_cover)
    assert renders == [pdf] and (tmp_path / "cover.jpg").read_bytes() == b"page one"


@pytest.mark.unit
def test_the_old_imports_placeholder_card_is_replaced_by_the_first_page(tmp_path, monkeypatch):
    card = b"x" * pdf_cover.PLACEHOLDER_SIZE
    monkeypatch.setattr(pdf_cover, "PLACEHOLDER_MD5", hashlib.md5(card).hexdigest())
    pdf, cover, renders = _fix_cover_world(tmp_path, monkeypatch, cover=card)
    assert pdf_cover.is_placeholder(cover)
    assert pdf_cover.fix_cover(pdf, cover, True)
    assert (tmp_path / "cover.jpg").read_bytes() == b"page one"
    # Same size, other bytes: a real cover
    pdf, cover, renders = _fix_cover_world(tmp_path, monkeypatch, cover=b"y" * pdf_cover.PLACEHOLDER_SIZE)
    assert not pdf_cover.is_placeholder(cover)


@pytest.mark.unit
def test_without_replace_a_cover_is_kept_as_it_is(tmp_path, monkeypatch):
    pdf, cover, renders = _fix_cover_world(tmp_path, monkeypatch, cover=b"provider cover")
    # Unflagged: shown again as it is
    assert pdf_cover.fix_cover(pdf, cover, False)
    # Flagged: nothing to do
    assert not pdf_cover.fix_cover(pdf, cover, True)
    assert renders == [] and (tmp_path / "cover.jpg").read_bytes() == b"provider cover"


@pytest.mark.unit
def test_replace_hands_any_cover_to_page_one_and_leaves_it_once_it_is(tmp_path, monkeypatch):
    pdf, cover, renders = _fix_cover_world(tmp_path, monkeypatch, cover=b"provider cover")
    assert pdf_cover.fix_cover(pdf, cover, True, replace=True)
    assert (tmp_path / "cover.jpg").read_bytes() == b"page one"
    # The same picture again: nothing rewritten, so a second Redo changes nothing
    mtime = (tmp_path / "cover.jpg").stat().st_mtime_ns
    assert not pdf_cover.fix_cover(pdf, cover, True, replace=True)
    assert (tmp_path / "cover.jpg").stat().st_mtime_ns == mtime
    # Unflagged: shown again even when the picture is the same
    assert pdf_cover.fix_cover(pdf, cover, False, replace=True)
    assert not list(tmp_path.glob("*.writing"))


@pytest.mark.unit
def test_a_book_whose_pdf_is_missing_is_left_alone(tmp_path, monkeypatch):
    pdf, cover, renders = _fix_cover_world(tmp_path, monkeypatch)
    (tmp_path / "book.pdf").unlink()
    assert not pdf_cover.fix_cover(pdf, cover, False)
    assert renders == []


@pytest.mark.unit
def test_save_page_cover_with_imagemagick(tmp_path):
    # Page 1 whole: a page with print off to one side keeps its full width and margins
    wand_image = pytest.importorskip("wand.image", exc_type=ImportError)
    from wand.color import Color
    from wand.drawing import Drawing
    from wand.exceptions import WandException
    pdf, cover = str(tmp_path / "book.pdf"), str(tmp_path / "cover.jpg")
    with wand_image.Image(width=1275, height=1650, background=Color("white"), resolution=150) as img, \
            Drawing() as draw:
        draw.fill_color = Color("black")
        draw.rectangle(left=60, top=450, right=105, bottom=1250)    # a stamp down the left margin
        draw.rectangle(left=250, top=200, right=920, bottom=1400)   # the body print, off centre
        draw(img)
        try:
            img.save(filename=pdf)
        except WandException as ex:
            pytest.skip("ImageMagick cannot write PDFs here: %s" % ex)
    try:
        assert pdf_cover.save_page_cover(pdf, cover)
    except WandException as ex:
        pytest.skip("ImageMagick cannot read PDFs here: %s" % ex)
    with wand_image.Image(filename=cover) as done:
        assert done.format == "JPEG" and abs(done.width / done.height - 1275 / 1650) < 0.01
        assert done.height >= 1020    # the largest thumbnail
    assert not pdf_cover.save_page_cover(pdf, cover)    # the same picture: left alone


@pytest.fixture
def env(tmp_path, temp_cwa_db):
    with lily_env(tmp_path) as env:
        yield env


@pytest.mark.unit
def test_rebuild_makes_pdf_covers_after_the_lookup(env, monkeypatch):
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
    assert str(task.message) == "Done: 2 books checked, 0 updated, 1 covers made"
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
def test_rebuild_keeps_a_pdfs_cover_without_rendering_page_one(env, monkeypatch):
    # The lookup takes no provider cover for a PDF-only book, so its cover needs no re-render
    from cps import metadata_helper
    from cps.tasks.metadata_rebuild import TaskRebuildMetadata
    env.add_book("Paper", fmt="PDF")
    replaced = []
    monkeypatch.setattr(metadata_helper, "fetch_and_apply_metadata",
                        lambda book_id, force=False, unanswered=None: False)
    monkeypatch.setattr(pdf_cover, "available", lambda: True)
    monkeypatch.setattr(pdf_cover, "_hand_cover", lambda *a: pytest.fail("no hand-cover check needed"))
    monkeypatch.setattr(pdf_cover, "fix_cover", lambda p, c, has_cover, replace=False: replaced.append(replace) or False)
    monkeypatch.setattr("cps.duplicate_index.mark_duplicate_index_pending", lambda reason=None: None)

    task = TaskRebuildMetadata(workers=1)
    with env.app.test_request_context():
        task.start(None)
    assert replaced == [False] and task.covers == 0


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


def test_page_one_is_rendered_by_ghostscript_on_the_pdf_itself(monkeypatch):
    # Not through ImageMagick, which copies the whole PDF to /tmp and renders one at a time
    import subprocess
    from types import SimpleNamespace
    ran = []
    monkeypatch.setattr(pdf_cover.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: ran.append((args, kw)) or
                        SimpleNamespace(returncode=1, stdout=b"", stderr=b"broken"))
    assert pdf_cover._gs_render("/books/A Paper.pdf") is None
    args, kw = ran[0]
    # Below the web app's priority, so a library-wide run can use every idle core
    assert args[:4] == ["/usr/bin/nice", "-n", "10", "/usr/bin/gs"]
    assert args[-2:] == ["-f", "/books/A Paper.pdf"]
    # Same page box and anti-aliasing as ImageMagick's render; messages kept out of the image
    for flag in ("-dUseCropBox", "-dFirstPage=1", "-dLastPage=1", "-r100", "-dTextAlphaBits=4",
                 "-dGraphicsAlphaBits=4", "-sstdout=%stderr", "-sOutputFile=-", "-dSAFER"):
        assert flag in args, flag
    assert kw["timeout"] == pdf_cover.RENDER_TIMEOUT and kw["capture_output"]


def test_without_nice_ghostscript_still_renders(monkeypatch):
    import subprocess
    from types import SimpleNamespace
    ran = []
    monkeypatch.setattr(pdf_cover.shutil, "which", lambda name: "/usr/bin/gs" if name == "gs" else None)
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: ran.append(args) or
                        SimpleNamespace(returncode=1, stdout=b"", stderr=b"broken"))
    pdf_cover._gs_render("/books/A Paper.pdf")
    assert ran[0][0] == "/usr/bin/gs"


def test_without_ghostscript_imagemagick_renders(monkeypatch):
    monkeypatch.setattr(pdf_cover.shutil, "which", lambda name: None)
    assert pdf_cover._gs_render("/books/A Paper.pdf") is None
