# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Shared test environment: a real app.db + Calibre metadata.db in a temp dir, wired into
the global cps singletons (ub.session, config, calibre_db) and a throwaway Flask app with
the production blueprints registered.

Everything that is swapped in is restored on teardown so other unit tests see the same
(uninitialised) globals they always did. Used by most page and route tests.
"""

import os
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask

REPO = Path(__file__).resolve().parents[2]
EMPTY_LIBRARY_DB = REPO / "empty_library" / "metadata.db"

ADMIN_PASSWORD = "admin-test-pw"
# Test passwords get one PBKDF2 round: the default scrypt hash costs ~70 ms to make and
# again to check at login, which was most of every environment's setup time.
FAST_HASH = "pbkdf2:sha256:1"


def _admin_name():
    from cps import constants
    return constants.DEFAULT_ADMIN_NAME



class LilyEnv:
    """Handle for a live test environment (see ``lily_env`` below)."""

    def __init__(self, app, library_dir, app_db_path):
        self.app = app
        self.library_dir = library_dir
        self.app_db_path = app_db_path

    # -- users -----------------------------------------------------------------
    @property
    def ub(self):
        from cps import ub
        return ub

    def admin(self):
        ub = self.ub
        return ub.session.query(ub.User).filter(ub.User.name == _admin_name()).one()

    def add_user(self, name, password="pw", role=None, **fields):
        from werkzeug.security import generate_password_hash
        from cps import constants
        ub = self.ub
        user = ub.User()
        user.name = name
        user.email = f"{name}@example.org"
        user.role = constants.ROLE_USER | constants.ROLE_DOWNLOAD if role is None else role
        user.sidebar_view = constants.ADMIN_USER_SIDEBAR
        user.password = generate_password_hash(password, method=FAST_HASH)
        for key, value in fields.items():
            setattr(user, key, value)
        ub.session.add(user)
        ub.session.commit()
        return user

    # -- books -----------------------------------------------------------------
    def add_book(self, title, *, author="Test Author", fmt="EPUB", timestamp=None,
                 tags=(), lang=None):
        """Insert a book straight into metadata.db (Calibre triggers need title_sort/uuid4,
        which are registered on this plain sqlite3 connection)."""
        timestamp = timestamp or datetime(2026, 1, 1, tzinfo=timezone.utc)
        ts = timestamp.strftime("%Y-%m-%d %H:%M:%S.%f+00:00")
        con = sqlite3.connect(self.library_dir / "metadata.db")
        con.create_function("title_sort", 1, lambda t: t)
        con.create_function("uuid4", 0, lambda: str(uuid.uuid4()))
        try:
            cur = con.cursor()
            path = f"{author}/{title}"
            cur.execute(
                "INSERT INTO books (title, sort, author_sort, timestamp, pubdate, series_index, "
                "last_modified, path, has_cover, uuid) VALUES (?,?,?,?,?,1.0,?,?,0,?)",
                (title, title, author, ts, ts, ts, path, str(uuid.uuid4())))
            book_id = cur.lastrowid
            cur.execute("INSERT OR IGNORE INTO authors (name, sort) VALUES (?, ?)", (author, author))
            author_id = cur.execute("SELECT id FROM authors WHERE name=?", (author,)).fetchone()[0]
            cur.execute("INSERT INTO books_authors_link (book, author) VALUES (?, ?)", (book_id, author_id))
            if fmt:
                cur.execute("INSERT INTO data (book, format, uncompressed_size, name) VALUES (?,?,?,?)",
                            (book_id, fmt, 1234, title))
            for tag in tags:
                cur.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
                tag_id = cur.execute("SELECT id FROM tags WHERE name=?", (tag,)).fetchone()[0]
                cur.execute("INSERT INTO books_tags_link (book, tag) VALUES (?, ?)", (book_id, tag_id))
            if lang:
                cur.execute("INSERT OR IGNORE INTO languages (lang_code) VALUES (?)", (lang,))
                lang_id = cur.execute("SELECT id FROM languages WHERE lang_code=?", (lang,)).fetchone()[0]
                cur.execute("INSERT INTO books_languages_link (book, lang_code) VALUES (?, ?)",
                            (book_id, lang_id))
            con.commit()
        finally:
            con.close()
        return book_id


def _build_app():
    import cps
    from cps import lm, ub
    from cps.reverseproxy import ReverseProxied
    from cps.cw_babel import babel, get_locale

    app = Flask("cps", root_path=os.path.dirname(cps.__file__))
    app.config.update(cps.app.config)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False,
                      SERVER_NAME=None)
    app.secret_key = "lily-unit-tests"
    app.wsgi_app = ReverseProxied(app.wsgi_app)

    lm.anonymous_user = ub.Anonymous
    lm.init_app(app)
    if hasattr(babel, "localeselector"):
        babel.init_app(app)
    else:
        babel.init_app(app, locale_selector=get_locale)
    cps.limiter.init_app(app)

    from cps.cwa_functions import library_refresh, cwa_settings, cwa_internal
    from cps.jinjia import jinjia
    from cps.web import web
    from cps.opds import opds
    from cps.shelf import shelf
    from cps.search import search
    from cps.admin import admi
    from cps.editbooks import editbook
    from cps.search_metadata import meta
    from cps.duplicates import duplicates
    from cps.logs import logs
    from cps.offline import offline
    for bp in (library_refresh, cwa_settings, cwa_internal,
               admi, jinjia, web, opds, shelf, search, meta, editbook,
               duplicates, logs, offline):
        app.register_blueprint(bp)

    @app.teardown_appcontext
    def _remove_calibre_session(exception=None):
        if cps.calibre_db.session_factory:
            cps.calibre_db.session_factory.remove()

    return app


@contextmanager
def lily_env(tmp_path, **config_overrides):
    """Yield a LilyEnv backed by fresh databases under ``tmp_path``."""
    import cps
    from cps import ub, db, config, config_sql

    library_dir = Path(tmp_path) / "library"
    library_dir.mkdir()
    shutil.copy(EMPTY_LIBRARY_DB, library_dir / "metadata.db")
    app_db_path = str(Path(tmp_path) / "app.db")

    saved_ub = (ub.session, ub.app_DB_path)
    saved_config = dict(config.__dict__)
    cdb = db.CalibreDB
    saved_cdb = {k: cdb.__dict__[k] for k in ("_init", "engine", "config", "session_factory")}
    saved_session = cps.calibre_db.session
    had_instance = cps.calibre_db in cdb.instances

    try:
        ub.init_db(app_db_path)
        # Make the default admin password known.
        from werkzeug.security import generate_password_hash
        admin = ub.session.query(ub.User).filter(ub.User.name == _admin_name()).one()
        admin.password = generate_password_hash(ADMIN_PASSWORD, method=FAST_HASH)
        ub.session.commit()

        config_sql.load_configuration(ub.session)
        config.init_config(ub.session, None)
        config.config_calibre_dir = str(library_dir)
        config.config_anonbrowse = 0
        config.config_books_per_page = 60
        for name, value in config_overrides.items():
            setattr(config, name, value)

        cdb.config = config
        cdb.setup_db(str(library_dir), app_db_path)
        cps.calibre_db.init_db()

        app = _build_app()
        yield LilyEnv(app, library_dir, app_db_path)
    finally:
        try:
            cdb.dispose()
        except Exception:
            pass
        for k, v in saved_cdb.items():
            setattr(cdb, k, v)
        cps.calibre_db.session = saved_session
        if not had_instance:
            cdb.instances.discard(cps.calibre_db)
        try:
            if ub.session is not None and ub.session is not saved_ub[0]:
                ub.session.close()
                ub.session.bind.dispose()
        except Exception:
            pass
        ub.session, ub.app_DB_path = saved_ub
        config.__dict__.clear()
        config.__dict__.update(saved_config)


def in_progress_rows(session, user_id, limit=None, library_uuid=None):
    """[(book_id, percent)] from web._in_progress_rows, the in-progress books query."""
    from cps.web import IN_PROGRESS_LIMIT, _in_progress_rows
    return [(book_id, percent) for book_id, percent, __ in
            _in_progress_rows(session, user_id, limit or IN_PROGRESS_LIMIT, library_uuid)]
