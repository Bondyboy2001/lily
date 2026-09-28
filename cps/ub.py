# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import atexit
import os
import sys
import sqlite3
import time
from datetime import datetime, timezone, timedelta
import itertools
import uuid
from flask import session as flask_session

from .cw_login import AnonymousUserMixin, current_user
from .cw_login import user_logged_in

from sqlalchemy import create_engine, exc, exists, event, text
from sqlalchemy import Column, ForeignKey, Index, UniqueConstraint
from sqlalchemy import String, Integer, SmallInteger, Boolean, DateTime, Float, JSON
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.sql.expression import func
try:
    # Compatibility with sqlalchemy 2.0
    from sqlalchemy.orm import declarative_base
except ImportError:
    from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker, Session, scoped_session
from werkzeug.security import generate_password_hash, check_password_hash

from . import constants, logger
from .string_helper import strip_whitespaces

log = logger.create()

session: Session | None = None
app_DB_path = None
Base = declarative_base()
searched_ids = {}

logged_in = dict()


def _safe_session_rollback(_session, label=""):
    try:
        _session.rollback()
    except Exception as e:
        if label:
            log.debug("Failed to rollback session after %s migration check: %s", label, e)


def _run_ddl_with_retry(engine, statements, retries=5, base_delay=0.25):
    if isinstance(statements, str):
        statements = [statements]

    last_error = None
    for attempt in range(retries):
        try:
            with engine.begin() as conn:
                conn.execute(text("PRAGMA busy_timeout=5000"))
                for stmt in statements:
                    conn.execute(text(stmt))
            return True
        except exc.OperationalError as e:
            last_error = e
            if "database is locked" in str(e).lower() and attempt < retries - 1:
                time.sleep(base_delay * (2 ** attempt))
                continue
            raise
    if last_error:
        raise last_error
    return False


def signal_store_user_session(object, user):
    store_user_session()


def store_user_session():
    _user = flask_session.get('_user_id', "")
    _id = flask_session.get('_id', "")
    _random = flask_session.get('_random', "")
    if flask_session.get('_user_id', ""):
        try:
            if not check_user_session(_user, _id, _random):
                expiry = int((datetime.now()  + timedelta(days=31)).timestamp())
                user_session = User_Sessions(_user, _id, _random, expiry)
                session.add(user_session)
                session.commit()
                log.debug("Login and store session : " + _id)
            else:
                log.debug("Found stored session: " + _id)
        except (exc.OperationalError, exc.InvalidRequestError) as e:
            session.rollback()
            log.exception(e)
    else:
        log.error("No user id in session")


def delete_user_session(user_id, session_key):
    try:
        log.debug("Deleted session_key: " + session_key)
        session.query(User_Sessions).filter(User_Sessions.user_id == user_id,
                                            User_Sessions.session_key == session_key).delete()
        session.commit()
    except (exc.OperationalError, exc.InvalidRequestError) as ex:
        session.rollback()
        log.exception(ex)


def check_user_session(user_id, session_key, random):
    try:
        found = session.query(User_Sessions).filter(User_Sessions.user_id==user_id,
                                                    User_Sessions.session_key==session_key,
                                                    User_Sessions.random == random,
                                                    ).one_or_none()
        if found is not None:
            new_expiry = int((datetime.now()  + timedelta(days=31)).timestamp())
            if new_expiry - found.expiry > 86400:
                found.expiry = new_expiry
                session.merge(found)
                session.commit()
        return bool(found)
    except (exc.OperationalError, exc.InvalidRequestError) as e:
        session.rollback()
        log.exception(e)
        return False


user_logged_in.connect(signal_store_user_session)

def store_combo_ids(result):
    ids = list()
    for element in result:
        ids.append(element[0].id)
    searched_ids[current_user.id] = ids


class UserBase:

    @property
    def is_authenticated(self):
        return self.is_active

    def _has_role(self, role_flag):
        return constants.has_flag(self.role, role_flag)

    def role_admin(self):
        return self._has_role(constants.ROLE_ADMIN)

    def role_download(self):
        return self._has_role(constants.ROLE_DOWNLOAD)

    def role_upload(self):
        return self._has_role(constants.ROLE_UPLOAD)

    def role_edit(self):
        return self._has_role(constants.ROLE_EDIT)

    def role_passwd(self):
        return self._has_role(constants.ROLE_PASSWD)

    def role_anonymous(self):
        return self._has_role(constants.ROLE_ANONYMOUS)

    def role_edit_shelfs(self):
        return self._has_role(constants.ROLE_EDIT_SHELFS)

    def role_delete_books(self):
        return self._has_role(constants.ROLE_DELETE_BOOKS)

    def role_viewer(self):
        return self._has_role(constants.ROLE_VIEWER)

    @property
    def is_active(self):
        return True

    @property
    def is_anonymous(self):
        return self.role_anonymous()

    def get_id(self):
        return str(self.id)

    def filter_language(self):
        return self.default_language

    def check_visibility(self, value):
        if value == constants.SIDEBAR_RECENT:
            return True
        return constants.has_flag(self.sidebar_view, value)

    def show_detail_random(self):
        return self.check_visibility(constants.DETAIL_RANDOM)

    def list_denied_tags(self):
        mct = self.denied_tags or ""
        return [strip_whitespaces(t) for t in mct.split(",")]

    def list_allowed_tags(self):
        mct = self.allowed_tags or ""
        return [strip_whitespaces(t) for t in mct.split(",")]

    def list_denied_column_values(self):
        mct = self.denied_column_value or ""
        return [strip_whitespaces(t) for t in mct.split(",")]

    def list_allowed_column_values(self):
        mct = self.allowed_column_value or ""
        return [strip_whitespaces(t) for t in mct.split(",")]

    def get_view_property(self, page, prop):
        if not self.view_settings.get(page):
            return None
        return self.view_settings[page].get(prop)

    def set_view_property(self, page, prop, value):
        if not self.view_settings.get(page):
            self.view_settings[page] = dict()
        self.view_settings[page][prop] = value
        try:
            flag_modified(self, "view_settings")
        except AttributeError:
            pass
        try:
            session.commit()
        except (exc.OperationalError, exc.InvalidRequestError) as e:
            session.rollback()
            log.error_or_exception(e)

    def __repr__(self):
        return '<User %r>' % self.name


# Baseclass for Users in Calibre-Web, settings which are depending on certain users are stored here. It is derived from
# User Base (all access methods are declared there)
class User(UserBase, Base):
    __tablename__ = 'user'
    __table_args__ = {'sqlite_autoincrement': True}

    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True)
    email = Column(String(120), unique=True, default="")
    role = Column(SmallInteger, default=constants.ROLE_USER)
    password = Column(String)
    shelf = relationship('Shelf', backref='user', lazy='dynamic', order_by='Shelf.name')
    downloads = relationship('Downloads', backref='user', lazy='dynamic')
    locale = Column(String(2), default="en")
    sidebar_view = Column(Integer, default=1)
    default_language = Column(String(3), default="all")
    denied_tags = Column(String, default="")
    allowed_tags = Column(String, default="")
    denied_column_value = Column(String, default="")
    allowed_column_value = Column(String, default="")
    view_settings = Column(JSON, default={})
    opds_only_shelves_sync = Column(Integer, default=0)
    hardcover_token = Column(String, unique=True, default=None)
    # Unused since Lily has a single look; kept so existing databases still match the model
    theme = Column(Integer, default=1)
    # Set for accounts still using the shipped default password; the web UI forces a
    # password change before anything else can be used. Cleared whenever the password
    # is assigned (see _clear_force_password_change below).
    force_password_change = Column(Boolean, default=False)


@event.listens_for(User.password, 'set')
def _clear_force_password_change(target, value, oldvalue, initiator):
    # Any explicit password assignment (profile, admin edit, reset, CLI) satisfies the
    # forced change. Attribute 'set' events are not fired when rows are loaded.
    target.force_password_change = False


# Class for anonymous user is derived from User base and completely overrides methods and properties for the
# anonymous user
class Anonymous(AnonymousUserMixin, UserBase):
    def __init__(self):
        self.hardcover_token = None
        self.opds_only_shelves_sync = None
        self.view_settings = None
        self.allowed_column_value = None
        self.allowed_tags = None
        self.denied_tags = None
        self.locale = None
        self.default_language = None
        self.sidebar_view = None
        self.id = None
        self.role = None
        self.name = None
        self.force_password_change = False
        self.loadSettings()

    def loadSettings(self):
        data = session.query(User).filter(User.role.op('&')(constants.ROLE_ANONYMOUS) == constants.ROLE_ANONYMOUS)\
            .first()  # type: User
        self.name = data.name
        self.role = data.role
        self.id=data.id
        self.sidebar_view = data.sidebar_view
        self.default_language = data.default_language
        self.locale = data.locale
        self.denied_tags = data.denied_tags
        self.allowed_tags = data.allowed_tags
        self.denied_column_value = data.denied_column_value
        self.allowed_column_value = data.allowed_column_value
        self.view_settings = data.view_settings
        self.opds_only_shelves_sync = data.opds_only_shelves_sync
        self.hardcover_token = data.hardcover_token
    def role_admin(self):
        return False

    @property
    def is_active(self):
        return False

    @property
    def is_anonymous(self):
        return True

    @property
    def is_authenticated(self):
        return False

    def get_view_property(self, page, prop):
        if 'view' in flask_session:
            if not flask_session['view'].get(page):
                return None
            return flask_session['view'][page].get(prop)
        return None

    def set_view_property(self, page, prop, value):
        if not 'view' in flask_session:
            flask_session['view'] = dict()
        if not flask_session['view'].get(page):
            flask_session['view'][page] = dict()
        flask_session['view'][page][prop] = value

class User_Sessions(Base):
    __tablename__ = 'user_session'
    __table_args__ = (Index('ix_user_session_random_session_key', 'random', 'session_key'),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id'))
    session_key = Column(String, default="")
    random = Column(String, default="")
    expiry = Column(Integer)


    def __init__(self, user_id, session_key, random, expiry):
        super().__init__()
        self.user_id = user_id
        self.session_key = session_key
        self.random = random
        self.expiry = expiry


# Baseclass representing Shelfs in calibre-web in app.db
class Shelf(Base):
    __tablename__ = 'shelf'

    id = Column(Integer, primary_key=True)
    uuid = Column(String, default=lambda: str(uuid.uuid4()))
    name = Column(String)
    is_public = Column(Integer, default=0)
    user_id = Column(Integer, ForeignKey('user.id'))
    books = relationship("BookShelf", backref="ub_shelf", cascade="all, delete-orphan", lazy="dynamic")
    created = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_modified = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return '<Shelf %d:%r>' % (self.id, self.name)


class OpdsShelfExposure(Base):
    __tablename__ = 'opds_shelf_exposure'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id'), nullable=False, index=True)
    shelf_id = Column(Integer, ForeignKey('shelf.id'), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint('user_id', 'shelf_id', name='unique_user_opds_shelf_exposure'),
    )


class DismissedDuplicateGroup(Base):
    __tablename__ = 'dismissed_duplicate_groups'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id'), nullable=False)
    group_hash = Column(String(32), nullable=False)  # MD5 hash of title+author combo
    dismissed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        # User can only dismiss the same duplicate group once
        UniqueConstraint('user_id', 'group_hash', name='unique_user_duplicate_dismissed'),
    )

    def __repr__(self):
        return '<DismissedDuplicateGroup %d: user=%d hash=%s>' % (self.id, self.user_id, self.group_hash)


# Baseclass representing Relationship between books and Shelfs in Calibre-Web in app.db (N:M)
class BookShelf(Base):
    __tablename__ = 'book_shelf_link'
    __table_args__ = (Index('ix_book_shelf_link_shelf', 'shelf'),)

    id = Column(Integer, primary_key=True)
    book_id = Column(Integer)
    order = Column(Integer)
    shelf = Column(Integer, ForeignKey('shelf.id'))
    date_added = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return '<Book %r>' % self.id


class ReadBook(Base):
    __tablename__ = 'book_read_link'
    __table_args__ = (Index('ix_book_read_link_user_book', 'user_id', 'book_id'),)

    STATUS_UNREAD = 0
    STATUS_FINISHED = 1
    STATUS_IN_PROGRESS = 2

    id = Column(Integer, primary_key=True)
    book_id = Column(Integer, unique=False)
    user_id = Column(Integer, ForeignKey('user.id'), unique=False)
    read_status = Column(Integer, unique=False, default=STATUS_UNREAD, nullable=False)
    last_modified = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    last_time_started_reading = Column(DateTime, nullable=True)
    times_started_reading = Column(Integer, default=0, nullable=False)


class Bookmark(Base):
    __tablename__ = 'bookmark'
    __table_args__ = (Index('ix_bookmark_user_book', 'user_id', 'book_id'),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id'))
    book_id = Column(Integer)
    format = Column(String(collation='NOCASE'))
    bookmark_key = Column(String)


# Reading position saved by the built-in web reader (one row per user and book)
class WebReaderProgress(Base):
    __tablename__ = 'web_reader_progress'
    __table_args__ = (UniqueConstraint('user_id', 'book_id', name='uq_web_reader_progress_user_book'),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id'), nullable=False)
    book_id = Column(Integer, nullable=False)
    cfi = Column(String)
    percent = Column(Float)
    last_modified = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))


# Books a user has archived (hidden from their library views).
class ArchivedBook(Base):
    __tablename__ = 'archived_book'
    __table_args__ = (Index('ix_archived_book_user_book', 'user_id', 'book_id'),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('user.id'))
    book_id = Column(Integer)
    is_archived = Column(Boolean, unique=False)
    last_modified = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class HardcoverMatchQueue(Base):
    """Queue for ambiguous Hardcover metadata matches requiring manual review."""
    __tablename__ = 'hardcover_match_queue'

    id = Column(Integer, primary_key=True, autoincrement=True)
    book_id = Column(Integer, nullable=False)
    book_title = Column(String, nullable=False)
    book_authors = Column(String, nullable=False)
    search_query = Column(String, nullable=False)
    hardcover_results = Column(String, nullable=False)  # JSON array of MetaRecord candidates
    confidence_scores = Column(String, nullable=False)  # JSON array of [score, reason] tuples
    created_at = Column(String, nullable=False)
    reviewed = Column(Integer, default=0, nullable=False)  # 0=pending, 1=reviewed
    selected_result_id = Column(String, default=None)  # Hardcover ID if manually selected
    review_action = Column(String, default=None)  # 'accept', 'reject', 'skip'
    reviewed_at = Column(String, default=None)
    reviewed_by = Column(String, default=None)

    def __repr__(self):
        return f'<HardcoverMatchQueue book_id={self.book_id} title="{self.book_title}" reviewed={bool(self.reviewed)}>'


@event.listens_for(Session, 'before_flush')
def receive_before_flush(session, flush_context, instances):
    # Maintain the last_modified_bit for the Shelf table.
    for change in itertools.chain(session.new, session.deleted):
        if isinstance(change, BookShelf):
            change.ub_shelf.last_modified = datetime.now(timezone.utc)


# Baseclass representing Downloads from calibre-web in app.db
class Downloads(Base):
    __tablename__ = 'downloads'
    __table_args__ = (Index('ix_downloads_user_id', 'user_id'),)

    id = Column(Integer, primary_key=True)
    book_id = Column(Integer)
    user_id = Column(Integer, ForeignKey('user.id'))

    def __repr__(self):
        return '<Download %r' % self.book_id


def filename(context):
    """Generate deterministic filename for thumbnails.

    Prefer the pattern:
        cover thumbnails:  book_<entity_id>_r<resolution>.<ext>
        series thumbnails: series_<entity_id>_r<resolution>.<ext>

    Fallback to legacy uuid-based naming if required fields are missing.
    This keeps previously generated files valid while making new ones easier
    to reason about and purge selectively.
    """
    params = context.get_current_parameters()
    file_format = params.get('format', 'jpeg')
    entity_id = params.get('entity_id')
    resolution = params.get('resolution')
    thumb_type = params.get('type')  # cover or series
    uuid_val = params.get('uuid')

    # map format 'jpeg' -> extension jpg
    if file_format == 'jpeg':
        ext = 'jpg'
    else:
        ext = file_format

    try:
        if entity_id is not None and resolution is not None and thumb_type is not None:
            if thumb_type == constants.THUMBNAIL_TYPE_COVER:
                return f"book_{entity_id}_r{resolution}.{ext}"
            elif thumb_type == constants.THUMBNAIL_TYPE_SERIES:
                return f"series_{entity_id}_r{resolution}.{ext}"
    except Exception:
        # fall back to uuid naming if anything unexpected occurs
        pass

    # legacy fallback
    return f"{uuid_val}.{ext}" if uuid_val else f"legacy_unknown.{ext}"


class Thumbnail(Base):
    __tablename__ = 'thumbnail'
    __table_args__ = (Index('ix_thumbnail_type_entity_resolution', 'type', 'entity_id', 'resolution'),)

    id = Column(Integer, primary_key=True)
    entity_id = Column(Integer)
    uuid = Column(String, default=lambda: str(uuid.uuid4()), unique=True)
    format = Column(String, default='jpeg')
    type = Column(SmallInteger, default=constants.THUMBNAIL_TYPE_COVER)
    resolution = Column(SmallInteger, default=constants.COVER_THUMBNAIL_SMALL)
    filename = Column(String, default=filename)
    generated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expiration = Column(DateTime, nullable=True)


# Add missing tables during migration of database
def add_missing_tables(engine, _session):
    if not engine.dialect.has_table(engine.connect(), "archived_book"):
        ArchivedBook.__table__.create(bind=engine, checkfirst=True)
    if not engine.dialect.has_table(engine.connect(), "thumbnail"):
        Thumbnail.__table__.create(bind=engine, checkfirst=True)
    if not engine.dialect.has_table(engine.connect(), "opds_shelf_exposure"):
        OpdsShelfExposure.__table__.create(bind=engine, checkfirst=True)
    if not engine.dialect.has_table(engine.connect(), "web_reader_progress"):
        WebReaderProgress.__table__.create(bind=engine, checkfirst=True)


def migrate_user_session_table(engine, _session):
    try:
        _session.query(exists().where(User_Sessions.random)).scalar()
        _session.commit()
    except exc.OperationalError:  # Database is not compatible, some columns are missing
        _safe_session_rollback(_session, "user_session")
        _run_ddl_with_retry(
            engine,
            [
                "ALTER TABLE user_session ADD column 'random' String",
                "ALTER TABLE user_session ADD column 'expiry' Integer",
            ],
        )

def migrate_user_table(engine, _session):
    try:
        _session.query(exists().where(User.hardcover_token)).scalar()
        _session.commit()
    except exc.OperationalError:  # Database is not compatible, some columns are missing
        _safe_session_rollback(_session, "user.hardcover_token")
        _run_ddl_with_retry(engine, "ALTER TABLE user ADD column 'hardcover_token' String")

    try:
        _session.query(exists().where(User.opds_only_shelves_sync)).scalar()
        _session.commit()
    except exc.OperationalError:
        _safe_session_rollback(_session, "user.opds_only_shelves_sync")
        _run_ddl_with_retry(engine, "ALTER TABLE user ADD column 'opds_only_shelves_sync' Integer DEFAULT 0")
    # Migration for per-user theme column
    try:
        _session.query(exists().where(User.theme)).scalar()
        _session.commit()
    except exc.OperationalError:
        _safe_session_rollback(_session, "user.theme")
        _run_ddl_with_retry(engine, "ALTER TABLE user ADD column 'theme' Integer DEFAULT 0")


    # Migration for forced password change flag (default admin password)
    try:
        _session.query(exists().where(User.force_password_change)).scalar()
        _session.commit()
    except exc.OperationalError:
        _safe_session_rollback(_session, "user.force_password_change")
        _run_ddl_with_retry(engine, "ALTER TABLE user ADD column 'force_password_change' Boolean DEFAULT 0")

    # Migration to enable duplicates sidebar for existing admin users (one-time)
    try:
        from . import constants
        SIDEBAR_DUPLICATES = constants.SIDEBAR_DUPLICATES

        migration_dir = os.path.join(constants.CONFIG_DIR, ".cwa_migrations")
        migration_marker = os.path.join(migration_dir, "duplicates_sidebar_v1")

        if not os.path.isfile(migration_marker):
            # Check if any admin users don't have duplicates enabled
            admin_users = _session.query(User).filter(
                User.role.op('&')(constants.ROLE_ADMIN) == constants.ROLE_ADMIN
            ).all()
            for user in admin_users:
                if not (user.sidebar_view & SIDEBAR_DUPLICATES):
                    user.sidebar_view |= SIDEBAR_DUPLICATES
                    print(f"[Migration] Enabled duplicates sidebar for admin user: {user.name}")

            _session.commit()
            try:
                os.makedirs(migration_dir, exist_ok=True)
                with open(migration_marker, "w", encoding="utf-8") as marker:
                    marker.write(datetime.now(timezone.utc).isoformat())
            except Exception as marker_error:
                print(
                    f"[Migration] Warning: Could not persist duplicates sidebar migration marker: {marker_error}",
                    flush=True,
                )
    except Exception as e:
        print(f"[Migration] Warning: Could not update duplicates sidebar setting: {e}")
        _session.rollback()

# Migrate database to current version, has to be updated after every database change. Currently migration from
# maybe 4/5 versions back to current should work.
# Migration is done by checking if relevant columns are existing, and then adding rows with SQL commands
# Lookup indexes declared via __table_args__ above. metadata.create_all() does not add
# indexes to tables that already exist, so existing app.db files get them here.
_PERFORMANCE_INDEXES = (
    ('ix_book_read_link_user_book', 'book_read_link', ('user_id', 'book_id')),
    ('ix_archived_book_user_book', 'archived_book', ('user_id', 'book_id')),
    ('ix_thumbnail_type_entity_resolution', 'thumbnail', ('type', 'entity_id', 'resolution')),
    ('ix_user_session_random_session_key', 'user_session', ('random', 'session_key')),
    ('ix_book_shelf_link_shelf', 'book_shelf_link', ('shelf',)),
    ('ix_downloads_user_id', 'downloads', ('user_id',)),
    ('ix_bookmark_user_book', 'bookmark', ('user_id', 'book_id')),
)


def migrate_performance_indexes(engine):
    for index_name, table_name, columns in _PERFORMANCE_INDEXES:
        try:
            _run_ddl_with_retry(engine, 'CREATE INDEX IF NOT EXISTS "{}" ON "{}" ({})'.format(
                index_name, table_name, ', '.join('"{}"'.format(c) for c in columns)))
        except Exception as e:
            log.warning("Could not create index %s on %s: %s", index_name, table_name, e)


def flag_users_with_default_password(_session):
    """Flag admin accounts whose password is still the shipped default so they must change it.

    Runs on every start (an app.db shipped with an image also contains the default admin).
    Only admin accounts are checked to keep start-up cheap.
    """
    try:
        candidates = _session.query(User).filter(
            User.role.op('&')(constants.ROLE_ADMIN) == constants.ROLE_ADMIN,
            User.force_password_change.isnot(True),
        ).all()
        flagged = []
        for user in candidates:
            if user.password and check_password_hash(str(user.password), constants.DEFAULT_PASSWORD):
                user.force_password_change = True
                flagged.append(user.name)
        if flagged:
            _session.commit()
            log.warning("User(s) %s still use the default password; they will be required to change it "
                        "at next web login", ", ".join(flagged))
    except Exception as e:
        log.error("Could not check for accounts using the default password: %s", e)
        _safe_session_rollback(_session, "default password check")


def migrate_default_sidebar(_session):
    """One-time trim of every user's sidebar (and the new-user default) to constants.DEFAULT_SIDEBAR.

    A marker file next to app.db records that it ran, so entries users switch back on later stay on.
    """
    db_path = _session.bind.url.database
    if not db_path or db_path == ":memory:":
        return
    marker = os.path.join(os.path.dirname(os.path.abspath(db_path)), ".lily_sidebar_trimmed")
    if os.path.exists(marker):
        return
    keep = constants.DEFAULT_SIDEBAR | constants.DETAIL_RANDOM
    try:
        for user in _session.query(User).all():
            user.sidebar_view = (user.sidebar_view or 0) & keep
        # On a fresh install the settings table doesn't exist yet and its column default applies
        if _session.bind.dialect.has_table(_session.connection(), "settings"):
            _session.execute(text("UPDATE settings SET config_default_show = config_default_show & :keep"),
                             {"keep": keep})
        _session.commit()
        with open(marker, "w") as f:
            f.write("sidebar trimmed to the Lily defaults\n")
        log.info("Trimmed the sidebar to the Lily defaults")
    except Exception as e:
        log.error("Could not trim sidebars to the Lily defaults: %s", e)
        _safe_session_rollback(_session, "default sidebar")


def migrate_Database(_session):
    engine = _session.bind
    add_missing_tables(engine, _session)
    migrate_user_session_table(engine, _session)
    migrate_user_table(engine, _session)
    migrate_default_sidebar(_session)
    # Runs after every user column migration so the full User model can be queried
    flag_users_with_default_password(_session)
    _safe_session_rollback(_session, "performance indexes")  # release any read lock before DDL
    migrate_performance_indexes(engine)


# Save downloaded books per user in calibre-web's own database
def update_download(book_id, user_id):
    check = session.query(Downloads).filter(Downloads.user_id == user_id).filter(Downloads.book_id == book_id).first()

    if not check:
        new_download = Downloads(user_id=user_id, book_id=book_id)
        session.add(new_download)
        try:
            session.commit()
        except exc.OperationalError:
            session.rollback()


# Delete non existing downloaded books in calibre-web's own database
def delete_download(book_id):
    session.query(Downloads).filter(book_id == Downloads.book_id).delete()
    try:
        session.commit()
    except exc.OperationalError:
        session.rollback()

# Generate user Guest (translated text), as anonymous user, no rights
def create_anonymous_user(_session):
    user = User()
    user.name = "Guest"
    user.email = 'no@email'
    user.role = constants.ROLE_ANONYMOUS
    user.password = ''

    _session.add(user)
    try:
        _session.commit()
        # Note: Anonymous users don't get system shelves
        # They will be created if/when the user registers
    except Exception:
        _session.rollback()


# Generate the default admin user (DEFAULT_ADMIN_NAME / DEFAULT_PASSWORD) with access to everything
def create_admin_user(_session):
    user = User()
    user.name = constants.DEFAULT_ADMIN_NAME
    user.email = "harry@example.org"
    user.role = constants.ADMIN_USER_ROLES
    user.sidebar_view = constants.ADMIN_USER_SIDEBAR

    user.password = generate_password_hash(constants.DEFAULT_PASSWORD)
    # Must come after the password assignment (which clears the flag)
    user.force_password_change = True

    _session.add(user)
    try:
        _session.commit()
    except Exception:
        _session.rollback()


def _set_app_db_pragmas(dbapi_connection, connection_record):
    """Per-connection pragmas for app.db.

    busy_timeout matches the 30s sqlite3 connect timeout. synchronous=NORMAL is only
    safe (and only applied) when the database is actually in WAL mode, which db.py
    enables unless NETWORK_SHARE_MODE is set.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout=30000")
        if os.environ.get("NETWORK_SHARE_MODE", "false").lower() not in ("1", "true", "yes", "on"):
            cursor.execute("PRAGMA journal_mode")
            row = cursor.fetchone()
            if row and str(row[0]).lower() == "wal":
                cursor.execute("PRAGMA synchronous=NORMAL")
    except Exception as e:
        log.debug("Could not set app.db pragmas: %s", e)
    finally:
        cursor.close()


def _create_app_db_engine(db_path):
    engine = create_engine('sqlite:///{0}'.format(db_path), echo=False,
                           connect_args={'timeout': 30})
    event.listen(engine, "connect", _set_app_db_pragmas)
    return engine


def init_db_thread():
    global app_DB_path
    engine = _create_app_db_engine(app_DB_path)

    Session = scoped_session(sessionmaker())
    Session.configure(bind=engine)
    return Session()


def init_db(app_db_path):
    # Open session for database connection
    global session
    global app_DB_path

    app_DB_path = app_db_path
    engine = _create_app_db_engine(app_db_path)

    Session = scoped_session(sessionmaker())
    Session.configure(bind=engine)
    session = Session()

    _healthcheck_app_db(app_db_path)

    if os.path.exists(app_db_path):
        Base.metadata.create_all(engine)
        migrate_Database(session)
    else:
        Base.metadata.create_all(engine)
        create_admin_user(session)
        create_anonymous_user(session)


def _healthcheck_app_db(app_db_path: str) -> None:
    """Basic startup checks for app.db path, permissions, and integrity."""
    try:
        if not app_db_path:
            log.error("app.db path is empty; cannot validate settings database")
            return
        if os.path.isdir(app_db_path):
            log.error("app.db path points to a directory: %s", app_db_path)
            return
        if not os.path.exists(app_db_path):
            log.warning("app.db not found at %s; it will be created on first run", app_db_path)
            return
        if not os.access(app_db_path, os.W_OK):
            log.error("app.db is not writable: %s", app_db_path)
        network_share_mode = os.environ.get("NETWORK_SHARE_MODE", "false").lower() in ("1", "true", "yes")
        if network_share_mode:
            log.info("Skipping PRAGMA quick_check for app.db due to NETWORK_SHARE_MODE=true")
            return
        try:
            with sqlite3.connect(app_db_path, timeout=5) as con:
                con.execute("PRAGMA quick_check;")
        except sqlite3.OperationalError as e:
            log.error("app.db integrity/lock check failed for %s: %s", app_db_path, e)
    except Exception as e:
        log.error("app.db healthcheck failed for %s: %s", app_db_path, e)

def password_change(user_credentials=None):
    if user_credentials:
        username, password = user_credentials.split(':', 1)
        user = session.query(User).filter(func.lower(User.name) == username.lower()).first()
        if user:
            if not password:
                print("Empty password is not allowed")
                sys.exit(4)
            try:
                from .helper import valid_password
                user.password = generate_password_hash(valid_password(password))
            except Exception:
                print("Password doesn't comply with password validation rules")
                sys.exit(4)
            if session_commit() == "":
                print("Password for user '{}' changed".format(username))
                sys.exit(0)
            else:
                print("Failed changing password")
                sys.exit(3)
        else:
            print("Username '{}' not valid, can't change password".format(username))
            sys.exit(3)


def get_new_session_instance():
    new_engine = create_engine('sqlite:///{0}'.format(app_DB_path), echo=False,
                               connect_args={'timeout': 30})
    new_session = scoped_session(sessionmaker())
    new_session.configure(bind=new_engine)

    atexit.register(lambda: new_session.remove() if new_session else True)

    return new_session


def dispose():
    global session

    old_session = session
    session = None
    if old_session:
        try:
            old_session.close()
        except Exception:
            pass
        if old_session.bind:
            try:
                old_session.bind.dispose()
            except Exception:
                pass

def session_commit(success=None, _session=None):
    s = _session if _session else session
    try:
        s.commit()
        if success:
            log.info(success)
    except (exc.OperationalError, exc.InvalidRequestError) as e:
        s.rollback()
        log.error_or_exception(e)
    return ""
