# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Persistent server configuration stored in app.db, including the encrypted-settings key file."""

import os
import sys
import json

from sqlalchemy import Column, String, Integer, SmallInteger, Boolean, BLOB, JSON
from sqlalchemy.exc import OperationalError
from sqlalchemy.sql.expression import text
from cryptography.fernet import Fernet
import cryptography.exceptions
from base64 import urlsafe_b64decode
try:
    # Compatibility with sqlalchemy 2.0
    from sqlalchemy.orm import declarative_base
except ImportError:
    from sqlalchemy.ext.declarative import declarative_base

from . import constants, logger
from .subproc_wrapper import process_wait
from .string_helper import strip_whitespaces

log = logger.create()
_Base = declarative_base()


class _Flask_Settings(_Base):
    __tablename__ = 'flask_settings'

    id = Column(Integer, primary_key=True)
    flask_session_key = Column(BLOB, default=b"")

    def __init__(self, key):
        super().__init__()
        self.flask_session_key = key


# Baseclass for representing settings in app.db with Calibre database settings
# (application settings)
class _Settings(_Base):
    __tablename__ = 'settings'

    id = Column(Integer, primary_key=True)
    config_calibre_dir = Column(String)
    config_calibre_uuid = Column(String)
    config_calibre_split = Column(Boolean, default=False)
    config_calibre_split_dir = Column(String)
    config_certfile = Column(String)
    config_keyfile = Column(String)
    config_trustedhosts = Column(String, default='')
    config_calibre_web_title = Column(String, default='Lily')
    config_books_per_page = Column(Integer, default=60)
    config_authors_max = Column(Integer, default=0)
    config_read_column = Column(Integer, default=0)
    config_title_regex = Column(String,
                                default=r'^(A|The|An|Der|Die|Das|Den|Ein|Eine'
                                        r'|Einen|Dem|Des|Einem|Eines|Le|La|Les|L\'|Un|Une)\s+')

    config_log_level = Column(SmallInteger, default=logger.DEFAULT_LOG_LEVEL)
    config_logfile = Column(String, default=logger.LOG_TO_STDOUT)
    config_access_log = Column(SmallInteger, default=0)
    config_access_logfile = Column(String, default=logger.DEFAULT_ACCESS_LOG)

    # Enable uploads by default on brand-new instances
    config_uploading = Column(SmallInteger, default=1)
    config_anonbrowse = Column(SmallInteger, default=0)
    config_use_https = Column(Boolean, default=False)

    config_default_role = Column(SmallInteger, default=0)
    config_default_show = Column(SmallInteger, default=constants.ADMIN_USER_SIDEBAR)
    config_default_language = Column(String(3), default="all")
    config_default_locale = Column(String(2), default="en")
    config_columns_to_ignore = Column(String)

    config_denied_tags = Column(String, default="")
    config_allowed_tags = Column(String, default="")
    config_restricted_column = Column(SmallInteger, default=0)
    config_denied_column_value = Column(String, default="")
    config_allowed_column_value = Column(String, default="")

    config_hardcover_token = Column(String)
    config_google_books_api_key = Column(String)

    config_binariesdir = Column(String, default=None)
    config_upload_formats = Column(String, default=','.join(constants.EXTENSIONS_UPLOAD))
    config_unicode_filename = Column(Boolean, default=False)
    config_embed_metadata = Column(Boolean, default=True)

    schedule_start_time = Column(Integer, default=4)
    schedule_duration = Column(Integer, default=10)
    # Controls scheduled thumbnail refresh only - thumbnails are always generated on-demand regardless
    schedule_generate_book_covers = Column(Boolean, default=True)
    schedule_generate_series_covers = Column(Boolean, default=False)
    schedule_reconnect = Column(Boolean, default=False)
    schedule_metadata_backup = Column(Boolean, default=False)

    config_password_policy = Column(Boolean, default=True)
    config_password_min_length = Column(Integer, default=8)
    config_password_number = Column(Boolean, default=True)
    config_password_lower = Column(Boolean, default=True)
    config_password_upper = Column(Boolean, default=True)
    config_password_character = Column(Boolean, default=True)
    config_password_special = Column(Boolean, default=True)
    config_session = Column(Integer, default=1)
    config_ratelimiter = Column(Boolean, default=True)
    config_limiter_uri = Column(String, default="")
    config_limiter_options = Column(String, default="")
    config_check_extensions = Column(Boolean, default=True)

    def __repr__(self):
        return self.__class__.__name__


# Class holds all application specific settings in calibre-web automated
class ConfigSQL(object):
    # pylint: disable=no-member
    def __init__(self):
        self.__dict__["dirty"] = list()
        self.cli = None

    def init_config(self, session, secret_key, cli):
        self._session = session
        self._settings = None
        self.db_configured = None
        self.config_calibre_dir = None
        self._fernet = Fernet(secret_key)
        self.cli = cli
        self.load()

        change = False

        # Fallback auto-detect: if calibre library not configured but default metadata.db exists, set it
        if (not self.config_calibre_dir or not os.path.isfile(os.path.join(self.config_calibre_dir, 'metadata.db'))):
            fallback_db = '/calibre-library/metadata.db'
            if os.path.isfile(fallback_db):
                detected_dir = os.path.dirname(fallback_db)
                if not self.config_calibre_dir:
                    log.info("[autoconfig] Detected calibre library at %s (fallback)", detected_dir)
                else:
                    log.info("[autoconfig] Existing configured path invalid, switching to detected library at %s", detected_dir)
                self.config_calibre_dir = detected_dir
                change = True

        # Autodetect Calibre if not configured or empty string
        if not self.config_binariesdir:
            change = True
            self.config_binariesdir = autodetect_calibre_binaries()

        if change:
            self.save()

    def _read_from_storage(self):
        if self._settings is None:
            log.debug("_ConfigSQL._read_from_storage")
            self._settings = self._session.query(_Settings).first()
        return self._settings

    def get_config_certfile(self):
        if self.cli:
            if self.cli.certfilepath:
                return self.cli.certfilepath
            if self.cli.certfilepath == "":
                return None
        return self.config_certfile

    def get_config_keyfile(self):
        if self.cli:
            if self.cli.keyfilepath:
                return self.cli.keyfilepath
            if self.cli.certfilepath == "":
                return None
        return self.config_keyfile

    def get_config_ipaddress(self):
        if self.cli:
            return self.cli.ip_address or ""
        return ""

    def _has_role(self, role_flag):
        return constants.has_flag(self.config_default_role, role_flag)

    def role_admin(self):
        return self._has_role(constants.ROLE_ADMIN)

    def role_download(self):
        return self._has_role(constants.ROLE_DOWNLOAD)

    def role_viewer(self):
        return self._has_role(constants.ROLE_VIEWER)

    def role_upload(self):
        return self._has_role(constants.ROLE_UPLOAD)

    def role_edit(self):
        return self._has_role(constants.ROLE_EDIT)

    def role_passwd(self):
        return self._has_role(constants.ROLE_PASSWD)

    def role_edit_shelfs(self):
        return self._has_role(constants.ROLE_EDIT_SHELFS)

    def role_delete_books(self):
        return self._has_role(constants.ROLE_DELETE_BOOKS)

    def list_denied_tags(self):
        mct = self.config_denied_tags or ""
        return [strip_whitespaces(t) for t in mct.split(",")]

    def list_allowed_tags(self):
        mct = self.config_allowed_tags or ""
        return [strip_whitespaces(t) for t in mct.split(",")]


    def to_dict(self):
        storage = {}
        for k, v in self.__dict__.items():
            if k[0] != '_' and not k.endswith("_e") and k != "cli" and 'api' not in k.lower():
                storage[k] = v
        return storage

    def load(self):
        """Load all configuration values from the underlying storage."""
        s = self._read_from_storage()  # type: _Settings
        for k, v in s.__dict__.items():
            if k[0] != '_':
                if v is None:
                    # if the storage column has no value, apply the (possible) default
                    column = s.__class__.__dict__.get(k)
                    if column.default is not None:
                        v = column.default.arg
                if k.endswith("_e") and v is not None:
                    try:
                        setattr(self, k, self._fernet.decrypt(v).decode())
                    except cryptography.fernet.InvalidToken:
                        setattr(self, k, "")
                else:
                    setattr(self, k, v)

        # Enforce unified logging to stdout for Docker deployments
        if self.config_logfile not in (logger.LOG_TO_STDOUT, logger.LOG_TO_STDERR):
            self.config_logfile = logger.LOG_TO_STDOUT
            s.config_logfile = logger.LOG_TO_STDOUT
            try:
                self._session.merge(s)
                self._session.commit()
            except OperationalError as e:
                log.error('Database error: %s', e)
                self._session.rollback()

        # Rename instances still using an upstream default title to Lily; a custom title is left alone
        if self.config_calibre_web_title in ('Calibre-Web Automated', 'Calibre-Web'):
            self.config_calibre_web_title = 'Lily'
            s.config_calibre_web_title = 'Lily'
            try:
                self._session.merge(s)
                self._session.commit()
            except OperationalError as e:
                log.error('Database error: %s', e)
                self._session.rollback()

        # Only readable books (and audiobooks) are supported; drop any other format saved by an older install
        allowed = [x for x in (self.config_upload_formats or '').split(',') if x in constants.EXTENSIONS_UPLOAD]
        if ','.join(allowed) != self.config_upload_formats:
            self.config_upload_formats = s.config_upload_formats = ','.join(allowed)
            try:
                self._session.merge(s)
                self._session.commit()
            except OperationalError as e:
                log.error('Database error: %s', e)
                self._session.rollback()

        have_metadata_db = bool(self.config_calibre_dir)
        if have_metadata_db:
            db_file = os.path.join(self.config_calibre_dir, 'metadata.db')
            have_metadata_db = os.path.isfile(db_file)
        self.db_configured = have_metadata_db

        from . import cli_param
        if os.environ.get('FLASK_DEBUG'):
            logfile = logger.setup(logger.LOG_TO_STDOUT, logger.logging.DEBUG)
        else:
            # pylint: disable=access-member-before-definition
            logfile = logger.setup(cli_param.logpath or self.config_logfile, self.config_log_level)
        if logfile != os.path.abspath(self.config_logfile):
            if logfile != os.path.abspath(cli_param.logpath):
                log.warning("Log path %s not valid, falling back to default", self.config_logfile)
            self.config_logfile = logfile
            s.config_logfile = logfile
            self._session.merge(s)
            try:
                self._session.commit()
            except OperationalError as e:
                log.error('Database error: %s', e)
                self._session.rollback()
        self.__dict__["dirty"] = list()

    def save(self):
        """Apply all configuration values to the underlying storage."""
        s = self._read_from_storage()  # type: _Settings

        for k in self.dirty:
            if k[0] == '_':
                continue
            if hasattr(s, k):
                if k.endswith("_e"):
                    setattr(s, k, self._fernet.encrypt(self.__dict__[k].encode()))
                else:
                    setattr(s, k, self.__dict__[k])

        log.debug("_ConfigSQL updating storage")
        self._session.merge(s)
        try:
            self._session.commit()
        except OperationalError as e:
            log.error('Database error: %s', e)
            self._session.rollback()
        self.load()

    def invalidate(self, error=None):
        if error:
            log.error(error)
        log.warning("invalidating configuration")
        self.db_configured = False
        self.save()

    def get_book_path(self):
        return self.config_calibre_split_dir if self.config_calibre_split else self.config_calibre_dir

    def store_calibre_uuid(self, calibre_db, Library_table):
        from . import app
        try:
            with app.app_context():
                calibre_uuid = calibre_db.session.query(Library_table).one_or_none()
                if self.config_calibre_uuid != calibre_uuid.uuid:
                    self.config_calibre_uuid = calibre_uuid.uuid
                    self.save()
        except AttributeError:
            pass

    def __setattr__(self, attr_name, attr_value):
        super().__setattr__(attr_name, attr_value)
        self.__dict__["dirty"].append(attr_name)


def _migrate_table(session, orm_class, secret_key=None):
    changed = False

    for column_name, column in orm_class.__dict__.items():
        if column_name[0] != '_':
            try:
                session.query(column).first()
            except OperationalError as err:
                log.debug("%s: %s", column_name, err.args[0])
                # Handle default values for new columns
                if column.default is None:
                    # Use NULL for columns with None default (important for autodetection logic)
                    column_default = "DEFAULT NULL"
                else:
                    if isinstance(column.default.arg, bool):
                        column_default = "DEFAULT {}".format(int(column.default.arg))
                    else:
                        column_default = "DEFAULT `{}`".format(column.default.arg)
                if isinstance(column.type, JSON):
                    column_type = "JSON"
                else:
                    column_type = column.type
                alter_table = text("ALTER TABLE %s ADD COLUMN `%s` %s %s" % (orm_class.__tablename__,
                                                                             column_name,
                                                                             column_type,
                                                                             column_default))
                log.debug(alter_table)
                session.execute(alter_table)
                changed = True
            except json.decoder.JSONDecodeError as e:
                log.error("Database corrupt column: {}".format(column_name))
                log.debug(e)

    if changed:
        try:
            session.commit()
        except OperationalError:
            session.rollback()


def autodetect_calibre_binaries():
    if sys.platform == "win32":
        calibre_path = ["C:\\program files\\calibre\\",
                        "C:\\program files(x86)\\calibre\\",
                        "C:\\program files(x86)\\calibre2\\",
                        "C:\\program files\\calibre2\\"]
    elif sys.platform.startswith("freebsd"):
        calibre_path = ["/usr/local/bin/"]
    else:
        calibre_path = ["/opt/calibre/"]
    for element in calibre_path:
        supported_binary_paths = [os.path.join(element, binary)
                                  for binary in constants.SUPPORTED_CALIBRE_BINARIES.values()]
        if all(os.path.isfile(binary_path) and os.access(binary_path, os.X_OK)
               for binary_path in supported_binary_paths):
            values = [process_wait([binary_path, "--version"],
                                   pattern=r'\(calibre (.*)\)') for binary_path in supported_binary_paths]
            if all(values):
                version = values[0].group(1)
                log.debug("calibre version %s", version)
                return element
    return ""


def _migrate_database(session, secret_key):
    # make sure the table is created, if it does not exist
    _Base.metadata.create_all(session.bind)
    _migrate_table(session, _Settings, secret_key)
    _migrate_table(session, _Flask_Settings)


def load_configuration(session, secret_key):
    _migrate_database(session, secret_key)
    if not session.query(_Settings).count():
        session.add(_Settings())
        session.commit()


def get_flask_session_key(_session):
    flask_settings = _session.query(_Flask_Settings).one_or_none()
    if flask_settings is None:
        flask_settings = _Flask_Settings(os.urandom(32))
        _session.add(flask_settings)
        _session.commit()
    return flask_settings.flask_session_key


def get_encryption_key(key_path):
    key_file = os.path.join(key_path, ".key")
    generate = True
    error = ""
    key = None
    if os.path.exists(key_file) and os.path.getsize(key_file) > 32:
        with open(key_file, "rb") as f:
            key = f.read()
        try:
            urlsafe_b64decode(key)
            generate = False
        except ValueError:
            pass
    if generate:
        key = Fernet.generate_key()
        try:
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(key)
            os.chmod(key_file, 0o600)
        except PermissionError as e:
            error = e
    return key, error
