# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Tasks that generate and clean up cover thumbnails."""

import os
from datetime import datetime, UTC
from dataclasses import dataclass

from .. import constants
from cps import config, db, fs, logger, ub
from cps.services.worker import CalibreTask
from sqlalchemy import or_
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


class TaskGenerateCoverThumbnails(CalibreTask):
    def __init__(self, book_id=-1, task_message='', book_path=None, last_modified=None):
        super().__init__(task_message)
        self.log = logger.create()
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
            if use_IM and not self.stop_requested:
                self.message = 'Scanning Books'
                books_with_covers = self.get_cover_sources()
                count = len(books_with_covers)

                total_generated = 0
                for i, book in enumerate(books_with_covers):

                    # Generate new thumbnails for missing covers
                    generated = self.create_book_cover_thumbnails(book)

                    # Increment the progress
                    self.progress = (1.0 / count) * i

                    if generated > 0:
                        total_generated += generated
                        self.message = N_('Generated %(count)s cover thumbnails', count=total_generated)

                    if self.stop_requested:
                        self.log.info('GenerateCoverThumbnails task has been stopped.')
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

    @staticmethod
    def get_books_with_covers(book_id=-1):
        filter_exp = (db.Books.id == book_id) if book_id != -1 else True
        calibre_db = db.CalibreDB(expire_on_commit=False, init=True)
        books_cover = calibre_db.session.query(db.Books).filter(db.Books.has_cover == 1).filter(filter_exp).all()
        calibre_db.session.close()
        return books_cover

    def get_cover_sources(self):
        if self.book_id != -1 and self.book_path:
            return [
                BookCoverSource(
                    id=int(self.book_id),
                    path=self.book_path,
                    last_modified=self.last_modified or datetime.now(UTC),
                )
            ]
        return self.get_books_with_covers(self.book_id)

    def get_book_cover_thumbnails(self, book_id):
        return self.app_db_session \
            .query(ub.Thumbnail) \
            .filter(ub.Thumbnail.type == constants.THUMBNAIL_TYPE_COVER) \
            .filter(ub.Thumbnail.entity_id == book_id) \
            .filter(or_(ub.Thumbnail.expiration.is_(None), ub.Thumbnail.expiration > datetime.now(UTC))) \
            .all()

    def create_book_cover_thumbnails(self, book):
        generated = 0
        book_cover_thumbnails = self.get_book_cover_thumbnails(book.id)

        # Build a map: (resolution, format) -> thumbnail
        thumb_map = {}
        for t in book_cover_thumbnails:
            thumb_map[(t.resolution, t.format.lower())] = t

        # For each resolution and format, check if thumbnail exists and file is present
        formats = ['webp', 'jpg']
        for resolution in self.resolutions:
            for fmt in formats:
                thumb = thumb_map.get((resolution, fmt))
                file_missing = True
                if thumb:
                    file_missing = not self.cache.get_cache_file_exists(thumb.filename, constants.CACHE_TYPE_THUMBNAILS)
                if not thumb or file_missing:
                    generated += 1
                    self.create_book_cover_single_thumbnail_format(book, resolution, fmt)

        # Replace outdated, legacy, or format-mismatch thumbnails
        for thumbnail in book_cover_thumbnails:
            try:
                legacy_naming = not (thumbnail.filename.startswith('book_') or thumbnail.filename.startswith('series_'))
                wrong_format = thumbnail.format.lower() not in formats
                source_newer = book.last_modified.replace(tzinfo=None) > thumbnail.generated_at

                # If any legacy condition matched, migrate: delete old file & regenerate with deterministic name
                if legacy_naming or wrong_format:
                    old_filename = thumbnail.filename
                    self.app_db_session.delete(thumbnail)
                    self.app_db_session.commit()
                    # Regenerate both formats for this resolution
                    for fmt in formats:
                        self.create_book_cover_single_thumbnail_format(book, thumbnail.resolution, fmt)
                    # remove old file if still present
                    try:
                        self.cache.delete_cache_file(old_filename, constants.CACHE_TYPE_THUMBNAILS)
                    except Exception:
                        pass
                    generated += 1
                    continue

                if source_newer:
                    generated += 1
                    self.update_book_cover_thumbnail(book, thumbnail)
            except Exception as ex:
                self.log.debug(f"Thumbnail migration/update issue for book {book.id}: {ex}")
        return generated

    def create_book_cover_single_thumbnail_format(self, book, resolution, fmt):
        thumbnail = ub.Thumbnail()
        thumbnail.type = constants.THUMBNAIL_TYPE_COVER
        thumbnail.entity_id = book.id
        thumbnail.format = fmt
        thumbnail.resolution = resolution

        self.app_db_session.add(thumbnail)
        try:
            self.app_db_session.commit()
            self.generate_book_thumbnail(book, thumbnail)
        except Exception as ex:
            self.log.debug(f'Error creating {fmt.upper()} book thumbnail: ' + str(ex))
            self._handleError(f'Error creating {fmt.upper()} book thumbnail: ' + str(ex))
            self.app_db_session.rollback()

    def update_book_cover_thumbnail(self, book, thumbnail):
        thumbnail.generated_at = datetime.now(UTC)

        try:
            self.app_db_session.commit()
            self.cache.delete_cache_file(thumbnail.filename, constants.CACHE_TYPE_THUMBNAILS)
            self.generate_book_thumbnail(book, thumbnail)
        except Exception as ex:
            self.log.debug('Error updating book thumbnail: ' + str(ex))
            self._handleError('Error updating book thumbnail: ' + str(ex))
            self.app_db_session.rollback()

    def generate_book_thumbnail(self, book, thumbnail):
        if book and thumbnail:
            book_cover_filepath = os.path.join(config.get_book_path(), book.path, 'cover.jpg')
            if not os.path.isfile(book_cover_filepath):
                raise Exception('Book cover file not found')

            with Image(filename=book_cover_filepath) as img:
                height = get_resize_height(thumbnail.resolution)
                filename = self.cache.get_cache_file_path(thumbnail.filename, constants.CACHE_TYPE_THUMBNAILS)
                if img.height > height:
                    width = get_resize_width(thumbnail.resolution, img.width, img.height)
                    img.resize(width=width, height=height, filter='lanczos')
                # Set format for thumbnail
                img.format = thumbnail.format
                try:
                    img.compression_quality = 82
                except Exception:
                    pass
                img.save(filename=filename)

    @property
    def name(self):
        return N_('Cover Thumbnails')

    def __str__(self):
        if self.book_id > 0:
            return f"Add Cover Thumbnails for Book {self.book_id}"
        return "Generate Cover Thumbnails"

    @property
    def is_cancellable(self):
        return True


def clear_cover_thumbnails(book_ids):
    """Delete these books' cached cover thumbnails, files and rows, in one commit; they are made
    again when next shown (helper.get_book_cover_internal). For many covers changed at once,
    where a TaskClearCoverThumbnailCache and a TaskGenerateCoverThumbnails per book meant a dozen
    commits each and thumbnails nobody had asked for."""
    book_ids = list(book_ids)
    if not book_ids:
        return
    session = ub.get_new_session_instance()
    cache = fs.FileSystem()
    try:
        query = session.query(ub.Thumbnail) \
            .filter(ub.Thumbnail.type == constants.THUMBNAIL_TYPE_COVER) \
            .filter(ub.Thumbnail.entity_id.in_(book_ids))
        for thumbnail in query.all():
            cache.delete_cache_file(thumbnail.filename, constants.CACHE_TYPE_THUMBNAILS)
        query.delete(synchronize_session=False)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.remove()
    from .. import helper
    helper._pending_thumbnail_books.difference_update(book_ids)


class TaskClearCoverThumbnailCache(CalibreTask):
    def __init__(self, book_id, task_message=N_('Clearing cover thumbnail cache')):
        super().__init__(task_message)
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
        return "Delete Thumbnail cache directory"

    @property
    def is_cancellable(self):
        return False
