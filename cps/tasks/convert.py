# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import os
import re
from time import time
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError
from flask_babel import lazy_gettext as N_

from cps.services.worker import CalibreTask
from cps import db
from cps import logger, config
from cps.subproc_wrapper import process_open
from flask_babel import gettext as _
from cps.file_helper import get_temp_dir

from cps import gdriveutils
from cps.constants import SUPPORTED_CALIBRE_BINARIES
from cps.string_helper import strip_whitespaces

log = logger.create()

current_milli_time = lambda: int(round(time() * 1000))


class TaskConvert(CalibreTask):
    def __init__(self, file_path, book_id, task_message, settings, user=None):
        super(TaskConvert, self).__init__(task_message)
        self.worker_thread = None
        self.file_path = file_path
        self.book_id = book_id
        self.title = ""
        self.settings = settings
        self.user = user

        self.results = dict()

    def run(self, worker_thread):
        self.worker_thread = worker_thread
        if config.config_use_google_drive:
            worker_db = db.CalibreDB(expire_on_commit=False, init=True)
            cur_book = worker_db.get_book(self.book_id)
            self.title = cur_book.title
            data = worker_db.get_book_format(self.book_id, self.settings['old_book_format'])
            df = gdriveutils.getFileFromEbooksFolder(cur_book.path,
                                                     data.name + "." + self.settings['old_book_format'].lower())
            df_cover = gdriveutils.getFileFromEbooksFolder(cur_book.path, "cover.jpg")
            if df:
                datafile_cover = None
                datafile = os.path.join(config.get_book_path(),
                                        cur_book.path,
                                        data.name + "." + self.settings['old_book_format'].lower())
                if df_cover:
                    datafile_cover = os.path.join(config.get_book_path(),
                                                  cur_book.path, "cover.jpg")
                if not os.path.exists(os.path.join(config.get_book_path(), cur_book.path)):
                    os.makedirs(os.path.join(config.get_book_path(), cur_book.path))
                df.GetContentFile(datafile)
                if df_cover:
                    df_cover.GetContentFile(datafile_cover)
                worker_db.session.close()
            else:
                # ToDo Include cover in error handling
                error_message = _("%(format)s not found on Google Drive: %(fn)s",
                                  format=self.settings['old_book_format'],
                                  fn=data.name + "." + self.settings['old_book_format'].lower())
                worker_db.session.close()
                return self._handleError(error_message)

        filename = self._convert_ebook_format()
        if config.config_use_google_drive:
            os.remove(self.file_path + '.' + self.settings['old_book_format'].lower())
            if df_cover:
                os.remove(os.path.join(config.config_calibre_dir, cur_book.path, "cover.jpg"))

        if filename:
            if config.config_use_google_drive:
                # Upload files to gdrive
                gdriveutils.updateGdriveCalibreFromLocal()
                self._handleSuccess()

    def _convert_ebook_format(self):
        error_message = None
        local_db = db.CalibreDB(expire_on_commit=False, init=True)
        file_path = self.file_path
        book_id = self.book_id
        format_old_ext = '.' + self.settings['old_book_format'].lower()
        format_new_ext = '.' + self.settings['new_book_format'].lower()

        # check to see if destination format already exists - or if book is in database
        # if it does - mark the conversion task as complete and return a success
        # this will allow to send to E-Reader workflow to continue to work
        if os.path.isfile(file_path + format_new_ext) or\
                local_db.get_book_format(self.book_id, self.settings['new_book_format']):
            log.info("Book id %d already converted to %s", book_id, format_new_ext)
            cur_book = local_db.get_book(book_id)
            self.title = cur_book.title
            self.results['path'] = cur_book.path
            self.results['title'] = self.title
            new_format = local_db.session.query(db.Data).filter(db.Data.book == book_id)\
                .filter(db.Data.format == self.settings['new_book_format'].upper()).one_or_none()
            if not new_format:
                new_format = db.Data(name=os.path.basename(file_path),
                                     book_format=self.settings['new_book_format'].upper(),
                                     book=book_id, uncompressed_size=os.path.getsize(file_path + format_new_ext))
                try:
                    local_db.session.merge(new_format)
                    local_db.session.commit()
                except SQLAlchemyError as e:
                    local_db.session.rollback()
                    log.error("Database error: %s", e)
                    local_db.session.close()
                    self._handleError(N_("Oops! Database Error: %(error)s.", error=e))
                    return
                self._handleSuccess()
                local_db.session.close()
                return os.path.basename(file_path + format_new_ext)
        else:
            log.info("Book id %d - target format of %s does not exist. Moving forward with convert.",
                     book_id,
                     format_new_ext)

        # check if calibre converter-executable is existing
        if not os.path.exists(config.config_converterpath):
            self._handleError(N_("Calibre ebook-convert %(tool)s not found", tool=config.config_converterpath))
            return
        has_cover = local_db.get_book(book_id).has_cover
        check, error_message = self._convert_calibre(file_path, format_old_ext, format_new_ext, has_cover)

        if check == 0:
            cur_book = local_db.get_book(book_id)
            if os.path.isfile(file_path + format_new_ext):
                new_format = local_db.session.query(db.Data).filter(db.Data.book == book_id) \
                    .filter(db.Data.format == self.settings['new_book_format'].upper()).one_or_none()
                if not new_format:
                    new_format = db.Data(name=cur_book.data[0].name,
                                         book_format=self.settings['new_book_format'].upper(),
                                         book=book_id, uncompressed_size=os.path.getsize(file_path + format_new_ext))
                    try:
                        local_db.session.merge(new_format)
                        local_db.session.commit()
                    except SQLAlchemyError as e:
                        local_db.session.rollback()
                        log.error("Database error: %s", e)
                        local_db.session.close()
                        self._handleError(error_message)
                        return
                self.results['path'] = cur_book.path
                self.title = cur_book.title
                self.results['title'] = self.title
                if not config.config_use_google_drive:
                    self._handleSuccess()
                return os.path.basename(file_path + format_new_ext)
            else:
                error_message = N_('%(format)s format not found on disk', format=format_new_ext.upper())
        local_db.session.close()
        log.info("ebook converter failed with error while converting book")
        if not error_message:
            error_message = N_('Ebook converter failed with unknown error')
        else:
            log.error(error_message)
        self._handleError(error_message)
        return

    def _convert_calibre(self, file_path, format_old_ext, format_new_ext, has_cover):
        path_tmp_opf = None
        try:
            if config.config_embed_metadata:
                quotes = [5]
                tmp_dir = get_temp_dir()
                calibredb_binarypath = os.path.join(config.config_binariesdir, SUPPORTED_CALIBRE_BINARIES["calibredb"])
                my_env = os.environ.copy()
                if config.config_calibre_split:
                    my_env['CALIBRE_OVERRIDE_DATABASE_PATH'] = os.path.join(config.config_calibre_dir, "metadata.db")
                    library_path = config.config_calibre_split_dir
                else:
                    library_path = config.config_calibre_dir

                opf_command = [calibredb_binarypath, 'show_metadata', '--as-opf', str(self.book_id),
                               '--with-library', library_path]
                p = process_open(opf_command, quotes, my_env, newlines=False)
                lines = list()
                while p.poll() is None:
                    lines.append(p.stdout.readline())
                check = p.returncode
                calibre_traceback = p.stderr.readlines()
                if check == 0:
                    path_tmp_opf = os.path.join(tmp_dir, "metadata_" + str(uuid4()) + ".opf")
                    with open(path_tmp_opf, 'wb') as fd:
                        fd.write(b''.join(lines))
                else:
                    error_message = ""
                    for ele in calibre_traceback:
                        if not ele.startswith('Traceback') and not ele.startswith('  File'):
                            error_message = N_("Calibre failed with error: %(error)s", error=ele)
                    return check, error_message
            quotes = [1, 2]
            quotes_index = 3
            command = [config.config_converterpath, (file_path + format_old_ext),
                       (file_path + format_new_ext)]
            if config.config_embed_metadata:
                quotes.append(4)
                quotes_index = 5
                command.extend(['--from-opf', path_tmp_opf])
                if has_cover:
                    quotes.append(6)
                    command.extend(['--cover', os.path.join(os.path.dirname(file_path), 'cover.jpg')])
                    quotes_index = 7
            if config.config_calibre:
                parameters = re.findall(r"(--[\w-]+)(?:(\s(?:(\".+\")|(?:.+?)))(?:\s|$))?",
                                        config.config_calibre, re.IGNORECASE | re.UNICODE)
                if parameters:
                    for param in parameters:
                        command.append(strip_whitespaces(param[0]))
                        quotes_index += 1
                        if param[1] != "":
                            parsed = strip_whitespaces(param[1]).strip("\"")
                            command.append(parsed)
                            quotes.append(quotes_index)
                            quotes_index += 1
            p = process_open(command, quotes, newlines=False)
        except OSError as e:
            return 1, N_("Ebook-converter failed: %(error)s", error=e)

        while p.poll() is None:
            nextline = p.stdout.readline()
            if isinstance(nextline, bytes):
                nextline = nextline.decode('utf-8', errors="ignore").strip('\r\n')
            if nextline:
                log.debug(nextline)
            # parse progress string from calibre-converter
            progress = re.search(r"(\d+)%\s.*", nextline)
            if progress:
                self.progress = int(progress.group(1)) / 100
                if config.config_use_google_drive:
                    self.progress *= 0.9

        # process returncode
        check = p.returncode
        calibre_traceback = p.stderr.readlines()
        error_message = ""
        for ele in calibre_traceback:
            ele = ele.decode('utf-8', errors="ignore").strip('\n')
            log.debug(ele)
            if not ele.startswith('Traceback') and not ele.startswith('  File'):
                error_message = N_("Calibre failed with error: %(error)s", error=ele)
        return check, error_message

    @property
    def name(self):
        return N_("Convert")

    def __str__(self):
        return "Convert Book {}".format(self.book_id)

    @property
    def is_cancellable(self):
        return False
