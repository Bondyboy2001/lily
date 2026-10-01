# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""TaskGenerateCoverThumbnails: WebP only, the cover decoded once per book and resized
largest to smallest, one app.db commit per book, old JPEG rows cleaned up, and a large
scheduled backfill handed to an unscheduled task. ImageMagick isn't needed: a fake
wand Image records what the task does."""

import os
import sqlite3
from datetime import datetime, timezone

import pytest

from tests.unit.lily_env import lily_env

pytestmark = pytest.mark.unit


class FakeImage:
    opened = []

    def __init__(self, filename=None, file=None):
        self.width, self.height = 1600, 2400
        self.options = {}
        self.format = None
        self.saved = []
        FakeImage.opened.append(self)

    def read(self, filename=None):
        self.read_from = filename

    def resize(self, width, height, filter=None):
        self.width, self.height = width, height

    def save(self, filename):
        with open(filename, "wb") as f:
            f.write(b"webp")
        self.saved.append((os.path.basename(filename), self.height, self.format))

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    from cps import fs
    from cps.tasks import thumbnail
    FakeImage.opened = []
    monkeypatch.setattr(thumbnail, "Image", FakeImage, raising=False)
    monkeypatch.setattr(thumbnail, "use_IM", True)
    monkeypatch.setattr(fs, "CONFIG_DIR", str(tmp_path / "config"))
    with lily_env(tmp_path) as e:
        yield e


def _book_with_cover(env, title):
    book_id = env.add_book(title)
    con = sqlite3.connect(env.library_dir / "metadata.db")
    con.create_function("title_sort", 1, lambda t: t)
    con.create_function("uuid4", 0, lambda: "uuid")
    con.execute("UPDATE books SET has_cover = 1 WHERE id = ?", (book_id,))
    path = con.execute("SELECT path FROM books WHERE id = ?", (book_id,)).fetchone()[0]
    con.commit()
    con.close()
    os.makedirs(env.library_dir / path, exist_ok=True)
    (env.library_dir / path / "cover.jpg").write_bytes(b"\xff\xd8\xff")
    return book_id


def _rows(env):
    from cps import ub
    session = ub.init_db_thread()
    try:
        return sorted((t.entity_id, t.resolution, t.format, t.filename)
                      for t in session.query(ub.Thumbnail).all())
    finally:
        session.close()


def _run(task):
    task.run(None)
    return task


def test_generates_webp_only_from_one_decode_with_one_commit_per_book(env):
    from cps.tasks.thumbnail import TaskGenerateCoverThumbnails
    first, second = _book_with_cover(env, "One"), _book_with_cover(env, "Two")

    _run(TaskGenerateCoverThumbnails())

    assert len(FakeImage.opened) == 2  # one decode per book
    image = FakeImage.opened[0]
    assert image.options["jpeg:size"] == "1x1020"
    assert [name for name, _h, _f in image.saved] == [f"book_{first}_r4.webp", f"book_{first}_r2.webp",
                                                      f"book_{first}_r1.webp"]
    assert [h for _n, h, _f in image.saved] == [1020, 510, 255]
    assert {f for _n, _h, f in image.saved} == {"webp"}
    assert _rows(env) == sorted((book, res, "webp", f"book_{book}_r{res}.webp")
                                for book in (first, second) for res in (1, 2, 4))

    # nothing left to do: a second run decodes nothing
    FakeImage.opened = []
    _run(TaskGenerateCoverThumbnails())
    assert FakeImage.opened == []


def test_commits_once_per_book(env, monkeypatch):
    from cps.tasks.thumbnail import TaskGenerateCoverThumbnails
    for title in ("A", "B", "C"):
        _book_with_cover(env, title)
    task = TaskGenerateCoverThumbnails()
    session = task.app_db_session
    commits = []
    real_commit = session.commit
    monkeypatch.setattr(session, "commit", lambda: commits.append(1) or real_commit(), raising=False)
    _run(task)
    assert len(commits) == 3


def test_old_jpeg_rows_are_removed_and_do_not_trigger_regeneration(env):
    from cps import constants, fs, ub
    from cps.tasks.thumbnail import TaskGenerateCoverThumbnails
    book = _book_with_cover(env, "Legacy")
    _run(TaskGenerateCoverThumbnails())
    cache = fs.FileSystem()
    for res in (1, 2, 4):
        thumb = ub.Thumbnail(type=constants.THUMBNAIL_TYPE_COVER, entity_id=book, format="jpg", resolution=res,
                             filename=f"book_{book}_r{res}.jpg", generated_at=datetime.now(timezone.utc))
        ub.session.add(thumb)
        with open(cache.get_cache_file_path(thumb.filename, constants.CACHE_TYPE_THUMBNAILS), "wb") as f:
            f.write(b"jpg")
    ub.session.commit()

    FakeImage.opened = []
    _run(TaskGenerateCoverThumbnails())
    assert FakeImage.opened == []
    assert {fmt for _b, _r, fmt, _f in _rows(env)} == {"webp"}
    assert not cache.get_cache_file_exists(f"book_{book}_r1.jpg", constants.CACHE_TYPE_THUMBNAILS)
    assert cache.get_cache_file_exists(f"book_{book}_r1.webp", constants.CACHE_TYPE_THUMBNAILS)


def test_missing_webp_file_is_regenerated(env):
    from cps import constants, fs
    from cps.tasks.thumbnail import TaskGenerateCoverThumbnails
    book = _book_with_cover(env, "Gone")
    _run(TaskGenerateCoverThumbnails())
    fs.FileSystem().delete_cache_file(f"book_{book}_r2.webp", constants.CACHE_TYPE_THUMBNAILS)
    FakeImage.opened = []
    _run(TaskGenerateCoverThumbnails())
    assert [name for name, _h, _f in FakeImage.opened[0].saved] == [f"book_{book}_r2.webp"]
    assert len(_rows(env)) == 3


def test_large_scheduled_backfill_continues_as_an_unscheduled_task(env, monkeypatch):
    from cps.services import worker
    from cps.tasks import thumbnail
    for title in ("A", "B", "C"):
        _book_with_cover(env, title)
    queued = []
    monkeypatch.setattr(worker.WorkerThread, "add", classmethod(lambda cls, user, task, hidden=False: queued.append(task)))
    monkeypatch.setattr(thumbnail, "BACKFILL_HANDOFF_THRESHOLD", 2)

    task = thumbnail.TaskGenerateCoverThumbnails()
    task.scheduled = True
    _run(task)
    assert FakeImage.opened == [] and len(queued) == 1
    assert isinstance(queued[0], thumbnail.TaskGenerateCoverThumbnails) and not queued[0].scheduled

    _run(queued[0])
    assert len(FakeImage.opened) == 3


def test_count_books_with_covers(env):
    from cps.tasks.thumbnail import TaskGenerateCoverThumbnails
    _book_with_cover(env, "A")
    env.add_book("No cover")
    assert TaskGenerateCoverThumbnails.count_books_with_covers() == 1


def test_serving_a_webp_thumbnail_without_a_jpeg_does_not_requeue_generation(env, monkeypatch):
    from cps import calibre_db, constants, helper
    from cps.services import worker
    from cps.tasks.thumbnail import TaskGenerateCoverThumbnails
    book_id = _book_with_cover(env, "Served")
    _run(TaskGenerateCoverThumbnails())
    queued = []
    monkeypatch.setattr(worker.WorkerThread, "add", classmethod(lambda cls, user, task, hidden=False: queued.append(task)))
    monkeypatch.setattr(helper, "use_IM", True)

    with env.app.test_request_context("/"):
        book = calibre_db.get_book(book_id)
        resp = helper.get_book_cover_internal(book, resolution=constants.COVER_THUMBNAIL_MEDIUM)
        resp.direct_passthrough = False
        assert resp.status_code == 200 and resp.get_data() == b"webp"
    assert queued == [] and book_id not in helper._pending_thumbnail_books
