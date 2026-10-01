# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Tasks that generate and clean up cover thumbnails."""

import os
from urllib.request import urlopen
from io import BytesIO
from datetime import datetime, timezone
from dataclasses import dataclass

from .. import constants
from cps import config, db, fs, gdriveutils, logger, ub
from cps.services.worker import CalibreTask, STAT_CANCELLED, STAT_ENDED
from sqlalchemy import func, text, or_
from flask_babel import lazy_gettext as N_
try:
    from wand.image import Image
    use_IM = True
except (ImportError, RuntimeError):
    use_IM = False


@dataclass(frozen=True)
class BookCoverSource:
    id: int
    path: str
    last_modified: datetime


def get_resize_height(resolution):
    return int(255 * resolution)


def get_resize_width(resolution, original_width, original_height):
    height = get_resize_height(resolution)
    percent = (height / float(original_height))
    width = int(float(original_width) * float(percent))
    return width if width % 2 == 0 else width + 1


def get_best_fit(width, height, image_width, image_height):
    resize_width = int(width / 2.0)
    resize_height = int(height / 2.0)
    aspect_ratio = image_width / image_height

    # If this image's aspect ratio is different from the first image, then resize this image
    # to fill the width and height of the first image
    if aspect_ratio < width / height:
        resize_width = int(width / 2.0)
        resize_height = image_height * int(width / 2.0) / image_width

    elif aspect_ratio > width / height:
        resize_width = image_width * int(height / 2.0) / image_height
        resize_height = int(height / 2.0)

    return {'width': resize_width, 'height': resize_height}


# A scheduled run with more books than this to (re)generate is a first backfill (new
# library, cleared cache): it continues as an unscheduled task so the nightly window end
# doesn't stop it after a few hundred books, night after night.
BACKFILL_HANDOFF_THRESHOLD = 500
COVER_THUMBNAIL_FORMAT = 'webp'


@dataclass(frozen=True)
class ThumbnailRow:
    id: int
    resolution: int
    format: str
    filename: str
    generated_at: datetime


def cover_thumbnail_filename(book_id, resolution):
    """Same name ub.Thumbnail's column default gives a cover thumbnail."""
    return f"book_{book_id}_r{resolution}.{COVER_THUMBNAIL_FORMAT}"


class TaskGenerateCoverThumbnails(CalibreTask):
    def __init__(self, book_id=-1, task_message='', book_path=None, last_modified=None):
        super(TaskGenerateCoverThumbnails, self).__init__(task_message)
        self.log = logger.create()
        # Only library-wide runs are tracked in job_status, not the per-book ones after an edit
        self.job_name = "thumbnails" if book_id == -1 else None
        self.book_id = book_id
        self.book_path = book_path
        self.last_modified = last_modified
        self.app_db_session = ub.get_new_session_instance()
        self.cache = fs.FileSystem()
        self.resolutions = [
            constants.COVER_THUMBNAIL_SMALL,
            constants.COVER_THUMBNAIL_MEDIUM,
            constants.COVER_THUMBNAIL_LARGE
        ]

    def run(self, worker_thread):
        try:
            if use_IM and self.stat != STAT_CANCELLED and self.stat != STAT_ENDED:
                self.message = 'Scanning Books'
                books_with_covers = self.get_cover_sources()
                rows_by_book = self.get_cover_thumbnail_rows(None if self.book_id == -1 else self.book_id)
                todo = []
                for book in books_with_covers:
                    plan = self.plan_book_cover_thumbnails(book, rows_by_book.get(book.id, []))
                    if plan[0] or plan[1]:
                        todo.append((book, plan))
                del rows_by_book

                if self.scheduled and self.book_id == -1 and len(todo) > BACKFILL_HANDOFF_THRESHOLD:
                    self.hand_off_backfill(len(todo))
                    self._handleSuccess()
                    return

                count = len(todo)
                total_generated = 0
                for i, (book, plan) in enumerate(todo):

                    # Generate new thumbnails for missing covers
                    generated = self.apply_book_cover_plan(book, *plan)

                    # Increment the progress
                    self.progress = (1.0 / count) * i

                    if generated > 0:
                        total_generated += generated
                        self.message = N_('Generated %(count)s cover thumbnails', count=total_generated)

                    # Check if job has been cancelled or ended
                    if self.stat == STAT_CANCELLED:
                        self.log.info('GenerateCoverThumbnails task has been cancelled.')
                        return

                    if self.stat == STAT_ENDED:
                        self.log.info('GenerateCoverThumbnails task has been ended.')
                        return

                if total_generated == 0:
                    self.self_cleanup = True

            self._handleSuccess()
        finally:
            # CRITICAL: Clear book from pending set on ALL exit paths (success, cancel, end, error)
            # This must run even if task is cancelled, ended, or errors out
            if self.book_id != -1:
                try:
                    from .. import helper
                    helper._pending_thumbnail_books.discard(self.book_id)
                except Exception:
                    pass  # Silently fail if helper module not available

            # Always clean up database session
            self.app_db_session.remove()

    def hand_off_backfill(self, pending):
        """Queue the rest as an unscheduled task, which the schedule's end time doesn't stop."""
        from ..services.worker import WorkerThread
        self.log.info('%s books need cover thumbnails; continuing as an unscheduled task', pending)
        self.message = N_('Generating thumbnails for %(count)s books in a separate task', count=pending)
        WorkerThread.add(None, TaskGenerateCoverThumbnails(task_message=N_('Cover thumbnail backfill')))

    @staticmethod
    def get_books_with_covers(book_id=-1):
        """The id, path and last_modified of every book with a cover (not full Books rows)."""
        filter_exp = (db.Books.id == book_id) if book_id != -1 else True
        calibre_db = db.CalibreDB(expire_on_commit=False, init=True)
        rows = (calibre_db.session.query(db.Books.id, db.Books.path, db.Books.last_modified)
                .filter(db.Books.has_cover == 1).filter(filter_exp).all())
        calibre_db.session.close()
        return [BookCoverSource(id=row.id, path=row.path, last_modified=row.last_modified) for row in rows]

    @staticmethod
    def count_books_with_covers():
        calibre_db = db.CalibreDB(expire_on_commit=False, init=True)
        try:
            return calibre_db.session.query(func.count(db.Books.id)).filter(db.Books.has_cover == 1).scalar() or 0
        finally:
            calibre_db.session.close()

    def get_cover_sources(self):
        if self.book_id != -1 and self.book_path:
            return [
                BookCoverSource(
                    id=int(self.book_id),
                    path=self.book_path,
                    last_modified=self.last_modified or datetime.now(timezone.utc),
                )
            ]
        return self.get_books_with_covers(self.book_id)

    def get_cover_thumbnail_rows(self, book_id=None):
        """Current cover thumbnail rows as plain tuples, grouped by book id (one query)."""
        query = (self.app_db_session
                 .query(ub.Thumbnail.id, ub.Thumbnail.entity_id, ub.Thumbnail.resolution, ub.Thumbnail.format,
                        ub.Thumbnail.filename, ub.Thumbnail.generated_at)
                 .filter(ub.Thumbnail.type == constants.THUMBNAIL_TYPE_COVER)
                 .filter(or_(ub.Thumbnail.expiration.is_(None), ub.Thumbnail.expiration > datetime.now(timezone.utc))))
        if book_id is not None:
            query = query.filter(ub.Thumbnail.entity_id == book_id)
        rows_by_book = {}
        for row_id, entity_id, resolution, fmt, filename, generated_at in query.all():
            rows_by_book.setdefault(entity_id, []).append(
                ThumbnailRow(row_id, resolution, (fmt or '').lower(), filename or '', generated_at))
        return rows_by_book

    def plan_book_cover_thumbnails(self, book, rows):
        """What a book needs: ({resolution: row to refresh, or None to add}, [rows to delete]).

        Only WebP thumbnails are kept (browsers and the OPDS cover route are served WebP);
        JPEG and legacy uuid-named rows are deleted with their files.
        """
        to_generate = {}
        to_delete = []
        current = {}
        for row in rows:
            legacy_naming = not (row.filename.startswith('book_') or row.filename.startswith('series_'))
            if legacy_naming or row.format != COVER_THUMBNAIL_FORMAT:
                to_delete.append(row)
            else:
                current.setdefault(row.resolution, row)
        source_modified = book.last_modified.replace(tzinfo=None) if book.last_modified else None
        for resolution in self.resolutions:
            row = current.get(resolution)
            if row is None:
                to_generate[resolution] = None
            elif (source_modified and row.generated_at and source_modified > row.generated_at.replace(tzinfo=None)) \
                    or not self.cache.get_cache_file_exists(row.filename, constants.CACHE_TYPE_THUMBNAILS):
                to_generate[resolution] = row
        return to_generate, to_delete

    def create_book_cover_thumbnails(self, book):
        rows = self.get_cover_thumbnail_rows(book.id).get(book.id, [])
        return self.apply_book_cover_plan(book, *self.plan_book_cover_thumbnails(book, rows))

    def apply_book_cover_plan(self, book, to_generate, to_delete):
        """Write the planned thumbnails, then record them (and the deletions) in one commit."""
        written = []
        if to_generate:
            try:
                written = self.generate_book_thumbnails(book, sorted(to_generate, reverse=True))
            except Exception as ex:
                self.log.warning('Error creating cover thumbnails for book %s: %s', book.id, ex)
        if not written and not to_delete:
            return 0
        now = datetime.now(timezone.utc)
        try:
            for row in to_delete:
                self.app_db_session.query(ub.Thumbnail).filter(ub.Thumbnail.id == row.id).delete()
            for resolution in written:
                row = to_generate[resolution]
                if row is not None:
                    self.app_db_session.query(ub.Thumbnail).filter(ub.Thumbnail.id == row.id) \
                        .update({ub.Thumbnail.generated_at: now})
                else:
                    thumbnail = ub.Thumbnail()
                    thumbnail.type = constants.THUMBNAIL_TYPE_COVER
                    thumbnail.entity_id = book.id
                    thumbnail.format = COVER_THUMBNAIL_FORMAT
                    thumbnail.resolution = resolution
                    thumbnail.filename = cover_thumbnail_filename(book.id, resolution)
                    thumbnail.generated_at = now
                    self.app_db_session.add(thumbnail)
            self.app_db_session.commit()
        except Exception as ex:
            self.log.warning('Error saving cover thumbnails for book %s: %s', book.id, ex)
            self.app_db_session.rollback()
            return 0
        for row in to_delete:
            try:
                self.cache.delete_cache_file(row.filename, constants.CACHE_TYPE_THUMBNAILS)
            except Exception:
                pass
        return len(written)

    def generate_book_thumbnails(self, book, resolutions):
        """Decode the cover once and write each resolution, largest first, resizing the
        same image down step by step. Returns the resolutions written."""
        written = []
        with self.open_cover(book, get_resize_height(max(resolutions))) as img:
            img.format = COVER_THUMBNAIL_FORMAT
            try:
                img.compression_quality = 82
            except Exception:
                pass
            for resolution in resolutions:
                height = get_resize_height(resolution)
                if img.height > height:
                    width = get_resize_width(resolution, img.width, img.height)
                    img.resize(width=width, height=height, filter='lanczos')
                img.save(filename=self.cache.get_cache_file_path(cover_thumbnail_filename(book.id, resolution),
                                                                 constants.CACHE_TYPE_THUMBNAILS))
                written.append(resolution)
        return written

    @staticmethod
    def open_cover(book, max_height):
        if config.config_use_google_drive:
            if not gdriveutils.is_gdrive_ready():
                raise Exception('Google Drive is configured but not ready')
            content = gdriveutils.get_cover_via_gdrive(book.path)
            if not content:
                raise Exception('Google Drive cover url not found')
            return Image(file=BytesIO(content))
        book_cover_filepath = os.path.join(config.get_book_path(), book.path, 'cover.jpg')
        if not os.path.isfile(book_cover_filepath):
            raise Exception('Book cover file not found')
        img = Image()
        try:
            # let libjpeg decode at a reduced scale that is still >= the largest thumbnail
            img.options['jpeg:size'] = '1x{}'.format(max_height)
            img.read(filename=book_cover_filepath)
        except Exception:
            img.close()
            raise
        return img

    @property
    def name(self):
        return N_('Cover Thumbnails')

    def __str__(self):
        if self.book_id > 0:
            return "Add Cover Thumbnails for Book {}".format(self.book_id)
        else:
            return "Generate Cover Thumbnails"

    @property
    def is_cancellable(self):
        return True


class TaskGenerateSeriesThumbnails(CalibreTask):
    def __init__(self, task_message=''):
        super(TaskGenerateSeriesThumbnails, self).__init__(task_message)
        self.log = logger.create()
        self.app_db_session = ub.get_new_session_instance()
        self.calibre_db = db.CalibreDB(expire_on_commit=False, init=True)
        self.cache = fs.FileSystem()
        self.resolutions = [
            constants.COVER_THUMBNAIL_SMALL,
            constants.COVER_THUMBNAIL_MEDIUM,
        ]

    def run(self, worker_thread):
        try:
            self.generate_series_thumbnails()
        finally:
            self.app_db_session.remove()

    def generate_series_thumbnails(self):
        if self.calibre_db.session and use_IM and self.stat != STAT_CANCELLED and self.stat != STAT_ENDED:
            self.message = 'Scanning Series'
            all_series = self.get_series_with_four_plus_books()
            count = len(all_series)

            total_generated = 0
            for i, series in enumerate(all_series):
                generated = 0
                series_thumbnails = self.get_series_thumbnails(series.id)
                series_books = self.get_series_books(series.id)

                # Generate new thumbnails for missing covers
                resolutions = list(map(lambda t: t.resolution, series_thumbnails))
                missing_resolutions = list(set(self.resolutions).difference(resolutions))
                for resolution in missing_resolutions:
                    generated += 1
                    self.create_series_thumbnail(series, series_books, resolution)

                # Replace outdated or missing thumbnails
                for thumbnail in series_thumbnails:
                    if any(book.last_modified > thumbnail.generated_at for book in series_books):
                        generated += 1
                        self.update_series_thumbnail(series_books, thumbnail)

                    elif not self.cache.get_cache_file_exists(thumbnail.filename, constants.CACHE_TYPE_THUMBNAILS):
                        generated += 1
                        self.update_series_thumbnail(series_books, thumbnail)

                # Increment the progress
                self.progress = (1.0 / count) * i

                if generated > 0:
                    total_generated += generated
                    self.message = N_('Generated {0} series thumbnails').format(total_generated)

                # Check if job has been cancelled or ended
                if self.stat == STAT_CANCELLED:
                    self.log.info('GenerateSeriesThumbnails task has been cancelled.')
                    return

                if self.stat == STAT_ENDED:
                    self.log.info('GenerateSeriesThumbnails task has been ended.')
                    return

            if total_generated == 0:
                self.self_cleanup = True

        self._handleSuccess()

    def get_series_with_four_plus_books(self):
        return self.calibre_db.session \
            .query(db.Series) \
            .join(db.books_series_link) \
            .join(db.Books) \
            .filter(db.Books.has_cover == 1) \
            .group_by(text('books_series_link.series')) \
            .having(func.count('book_series_link') > 3) \
            .all()

    def get_series_books(self, series_id):
        return self.calibre_db.session \
            .query(db.Books) \
            .join(db.books_series_link) \
            .join(db.Series) \
            .filter(db.Books.has_cover == 1) \
            .filter(db.Series.id == series_id) \
            .all()

    def get_series_thumbnails(self, series_id):
        return (self.app_db_session
            .query(ub.Thumbnail)
            .filter(ub.Thumbnail.type == constants.THUMBNAIL_TYPE_SERIES)
            .filter(ub.Thumbnail.entity_id == series_id)
            .filter(or_(ub.Thumbnail.expiration.is_(None), ub.Thumbnail.expiration > datetime.now(timezone.utc)))
            .all())

    def create_series_thumbnail(self, series, series_books, resolution):
        thumbnail = ub.Thumbnail()
        thumbnail.type = constants.THUMBNAIL_TYPE_SERIES
        thumbnail.entity_id = series.id
        # Store series thumbnails as WebP as well
        thumbnail.format = 'webp'
        thumbnail.resolution = resolution

        self.app_db_session.add(thumbnail)
        try:
            self.app_db_session.commit()
            self.generate_series_thumbnail(series_books, thumbnail)
        except Exception as ex:
            self.log.debug('Error creating book thumbnail: ' + str(ex))
            self._handleError('Error creating book thumbnail: ' + str(ex))
            self.app_db_session.rollback()

    def update_series_thumbnail(self, series_books, thumbnail):
        thumbnail.generated_at = datetime.now(timezone.utc)

        try:
            self.app_db_session.commit()
            self.cache.delete_cache_file(thumbnail.filename, constants.CACHE_TYPE_THUMBNAILS)
            self.generate_series_thumbnail(series_books, thumbnail)
        except Exception as ex:
            self.log.debug('Error updating book thumbnail: ' + str(ex))
            self._handleError('Error updating book thumbnail: ' + str(ex))
            self.app_db_session.rollback()

    def generate_series_thumbnail(self, series_books, thumbnail):
        # Get the last four books in the series based on series_index
        books = sorted(series_books, key=lambda b: float(b.series_index), reverse=True)[:4]

        top = 0
        left = 0
        width = 0
        height = 0
        with Image() as canvas:
            for book in books:
                if config.config_use_google_drive:
                    if not gdriveutils.is_gdrive_ready():
                        raise Exception('Google Drive is configured but not ready')

                    web_content_link = gdriveutils.get_cover_via_gdrive(book.path)
                    if not web_content_link:
                        raise Exception('Google Drive cover url not found')

                    stream = None
                    try:
                        stream = urlopen(web_content_link)
                        with Image(file=stream) as img:
                            # Use the first image in this set to determine the width and height to scale the
                            # other images in this set
                            if width == 0 or height == 0:
                                width = get_resize_width(thumbnail.resolution, img.width, img.height)
                                height = get_resize_height(thumbnail.resolution)
                                canvas.blank(width, height)

                            dimensions = get_best_fit(width, height, img.width, img.height)

                            # resize and crop the image
                            img.resize(width=int(dimensions['width']), height=int(dimensions['height']),
                                       filter='lanczos')
                            img.crop(width=int(width / 2.0), height=int(height / 2.0), gravity='center')

                            # add the image to the canvas
                            canvas.composite(img, left, top)

                    except Exception as ex:
                        self.log.debug('Error generating thumbnail file: ' + str(ex))
                        raise ex
                    finally:
                        if stream is not None:
                            stream.close()

                book_cover_filepath = os.path.join(config.get_book_path(), book.path, 'cover.jpg')
                if not os.path.isfile(book_cover_filepath):
                    raise Exception('Book cover file not found')

                with Image(filename=book_cover_filepath) as img:
                    # Use the first image in this set to determine the width and height to scale the
                    # other images in this set
                    if width == 0 or height == 0:
                        width = get_resize_width(thumbnail.resolution, img.width, img.height)
                        height = get_resize_height(thumbnail.resolution)
                        canvas.blank(width, height)

                    dimensions = get_best_fit(width, height, img.width, img.height)

                    # resize and crop the image
                    img.resize(width=int(dimensions['width']), height=int(dimensions['height']), filter='lanczos')
                    img.crop(width=int(width / 2.0), height=int(height / 2.0), gravity='center')

                    # add the image to the canvas
                    canvas.composite(img, left, top)

                # set the coordinates for the next iteration
                if left == 0 and top == 0:
                    left = int(width / 2.0)
                elif left == int(width / 2.0) and top == 0:
                    left = 0
                    top = int(height / 2.0)
                else:
                    left = int(width / 2.0)

            canvas.format = thumbnail.format
            filename = self.cache.get_cache_file_path(thumbnail.filename, constants.CACHE_TYPE_THUMBNAILS)
            try:
                canvas.compression_quality = 80
            except Exception:
                pass
            canvas.save(filename=filename)

    @property
    def name(self):
        return N_('Cover Thumbnails')

    def __str__(self):
        return "GenerateSeriesThumbnails"

    @property
    def is_cancellable(self):
        return True


class TaskClearCoverThumbnailCache(CalibreTask):
    def __init__(self, book_id, task_message=N_('Clearing cover thumbnail cache')):
        super(TaskClearCoverThumbnailCache, self).__init__(task_message)
        self.log = logger.create()
        self.book_id = book_id
        self.app_db_session = ub.get_new_session_instance()
        self.cache = fs.FileSystem()

    def run(self, worker_thread):
        if self.app_db_session:
            if self.book_id == 0:  # delete superfluous thumbnails
                calibre_db = db.CalibreDB(expire_on_commit=False, init=True)
                thumbnails = (calibre_db.session.query(ub.Thumbnail)
                              .join(db.Books, ub.Thumbnail.entity_id == db.Books.id, isouter=True)
                              .filter(db.Books.id==None)
                              .all())
                calibre_db.session.close()
            elif self.book_id > 0:  # make sure single book is selected
                thumbnails = self.get_thumbnails_for_book(self.book_id)
            if self.book_id < 0:
                self.delete_all_thumbnails()
            else:
                for thumbnail in thumbnails:
                    self.delete_thumbnail(thumbnail)
        self._handleSuccess()
        self.app_db_session.remove()

    def get_thumbnails_for_book(self, book_id):
        return self.app_db_session \
            .query(ub.Thumbnail) \
            .filter(ub.Thumbnail.type == constants.THUMBNAIL_TYPE_COVER) \
            .filter(ub.Thumbnail.entity_id == book_id) \
            .all()

    def delete_thumbnail(self, thumbnail):
        try:
            self.cache.delete_cache_file(thumbnail.filename, constants.CACHE_TYPE_THUMBNAILS)
            self.app_db_session \
                .query(ub.Thumbnail) \
                .filter(ub.Thumbnail.type == constants.THUMBNAIL_TYPE_COVER) \
                .filter(ub.Thumbnail.entity_id == thumbnail.entity_id) \
                .delete()
            self.app_db_session.commit()
        except Exception as ex:
            self.log.debug('Error deleting book thumbnail: ' + str(ex))
            self._handleError('Error deleting book thumbnail: ' + str(ex))

    def delete_all_thumbnails(self):
        try:
            self.app_db_session.query(ub.Thumbnail).filter(ub.Thumbnail.type == constants.THUMBNAIL_TYPE_COVER).delete()
            self.app_db_session.commit()
            self.cache.delete_cache_dir(constants.CACHE_TYPE_THUMBNAILS)
        except Exception as ex:
            self.log.debug('Error deleting thumbnail directory: ' + str(ex))
            self._handleError('Error deleting thumbnail directory: ' + str(ex))

    @property
    def name(self):
        return N_('Cover Thumbnails')

    # needed for logging
    def __str__(self):
        if self.book_id > 0:
            return "Replace/Delete Cover Thumbnails for book " + str(self.book_id)
        else:
            return "Delete Thumbnail cache directory"

    @property
    def is_cancellable(self):
        return False
