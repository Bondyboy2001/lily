# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

__package__ = "cps"

import sys
import os
import mimetypes

from flask import Flask, g
from .MyLoginManager import MyLoginManager
from flask_principal import Principal
from werkzeug.middleware.proxy_fix import ProxyFix

from . import logger
from . import constants
from .cli import CliParameter
from .reverseproxy import ReverseProxied
from .server import WebServer
from .dep_check import dependency_check
from . import config_sql
from . import cache_buster
from . import ub, db

try:
    from flask_limiter import Limiter
    limiter_present = True
except ImportError:
    limiter_present = False
try:
    from flask_wtf.csrf import CSRFProtect
    wtf_present = True
except ImportError:
    wtf_present = False


mimetypes.init()
mimetypes.add_type('application/xhtml+xml', '.xhtml')
mimetypes.add_type('application/epub+zip', '.epub')
mimetypes.add_type('application/epub+zip', '.kepub')
mimetypes.add_type('text/xml', '.fb2')
mimetypes.add_type('application/x-mobipocket-ebook', '.mobi')
mimetypes.add_type('application/x-mobipocket-ebook', '.prc')
mimetypes.add_type('application/vnd.amazon.ebook', '.azw')
mimetypes.add_type('application/x-mobi8-ebook', '.azw3')
mimetypes.add_type('application/vnd.comicbook-rar', '.cbr')
mimetypes.add_type('application/vnd.comicbook+zip', '.cbz')
mimetypes.add_type('application/x-cbt', '.cbt')
mimetypes.add_type('application/x-7z-compressed', '.cb7')
mimetypes.add_type('image/vnd.djv', '.djv')
mimetypes.add_type('image/vnd.djv', '.djvu')
mimetypes.add_type('application/mpeg', '.mpeg')
mimetypes.add_type('audio/mpeg', '.mp3')
mimetypes.add_type('audio/x-m4a', '.m4a')
mimetypes.add_type('audio/x-m4a', '.m4b')
mimetypes.add_type('audio/x-hx-aac-adts', '.aac')
mimetypes.add_type('audio/vnd.dolby.dd-raw', '.ac3')
mimetypes.add_type('video/x-ms-asf', '.asf')
mimetypes.add_type('audio/ogg', '.ogg')
mimetypes.add_type('application/ogg', '.oga')
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('application/x-ms-reader', '.lit')
mimetypes.add_type('text/javascript; charset=UTF-8', '.js')
mimetypes.add_type('application/vnd.adobe.adept+xml', '.acsm')
mimetypes.add_type('application/vnd.amazon.ebook', '.kfx')
mimetypes.add_type('application/zip', '.kfx-zip')

log = logger.create()

app = Flask(__name__)
# Set SESSION_COOKIE_SECURE=true when Lily is served over HTTPS so the session and
# remember-me cookies are never sent over plain HTTP.
_secure_cookies = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=_secure_cookies,
    SESSION_COOKIE_SAMESITE='Lax',
    REMEMBER_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_SECURE=_secure_cookies,
    REMEMBER_COOKIE_SAMESITE='Strict',
    WTF_CSRF_SSL_STRICT=False,
    SESSION_COOKIE_NAME=os.environ.get('COOKIE_PREFIX', "") + "session",
    REMEMBER_COOKIE_NAME=os.environ.get('COOKIE_PREFIX', "") + "remember_token",
    TEMPLATES_AUTO_RELOAD=os.environ.get('DEVELOP_ON', 'False').lower() == 'true',
)

# Fix for running behind reverse proxy (e.g. nginx, apache, caddy, ...)
# Without it, url_for will generate http:// urls even if https:// is used.
# Set TRUSTED_PROXY_COUNT to the number of proxies in your chain. The default is 0
# (X-Forwarded-* headers are ignored), because trusting them when Lily is exposed
# directly lets any client spoof its IP address (defeating rate limiting / logging).
# Behind a single reverse proxy use TRUSTED_PROXY_COUNT=1; for CF Tunnel + reverse proxy use 2.
try:
    num_proxies = max(0, int(os.environ.get('TRUSTED_PROXY_COUNT', '0')))
except (TypeError, ValueError):
    log.warning('Invalid TRUSTED_PROXY_COUNT value %r, falling back to 0', os.environ.get('TRUSTED_PROXY_COUNT'))
    num_proxies = 0
if num_proxies > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=num_proxies, x_proto=num_proxies, x_host=num_proxies, x_prefix=num_proxies)
    log.info(f'ProxyFix configured to trust {num_proxies} proxy(ies) for X-Forwarded-* headers')
else:
    log.info('TRUSTED_PROXY_COUNT=0: X-Forwarded-* headers are ignored (set TRUSTED_PROXY_COUNT=1 when running behind a reverse proxy)')

lm = MyLoginManager()

cli_param = CliParameter()

config = config_sql.ConfigSQL()

if wtf_present:
    csrf = CSRFProtect()
else:
    csrf = None

calibre_db = db.CalibreDB()

web_server = WebServer()

if limiter_present:
    limiter = Limiter(key_func=True, headers_enabled=True, auto_check=False, swallow_errors=False)
else:
    limiter = None


def create_app():
    if csrf:
        csrf.init_app(app)

    cli_param.init()

    ub.init_db(cli_param.settings_path)
    # pylint: disable=no-member
    encrypt_key, error = config_sql.get_encryption_key(os.path.dirname(cli_param.settings_path))

    config_sql.load_configuration(ub.session, encrypt_key)
    config.init_config(ub.session, encrypt_key, cli_param)

    # Intelligent Security Configuration
    # Force SESSION_COOKIE_SECURE if OAuth is enabled OR if "Use via HTTPS" is checked
    # This ensures OAuth works (requires Secure cookies) while allowing HTTP for standard login if desired
    if config.config_login_type == constants.LOGIN_OAUTH or getattr(config, 'config_use_https', False):
        app.config['SESSION_COOKIE_SECURE'] = True
        app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
        log.info("Enforcing SESSION_COOKIE_SECURE=True (OAuth enabled or HTTPS enforced)")
    else:
        # Fallback to environment variable or False
        app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
        log.info(f"SESSION_COOKIE_SECURE set to {app.config['SESSION_COOKIE_SECURE']} (Standard/LDAP login)")

    # Set OAuth redirect host consistency
    if hasattr(config, 'config_oauth_redirect_host') and config.config_oauth_redirect_host:
        from urllib.parse import urlparse
        parsed = urlparse(config.config_oauth_redirect_host)
        if parsed.netloc:
            app.config['FORCE_HOST_FOR_REDIRECTS'] = parsed.netloc

    if error:
        log.error(error)

    ub.password_change(cli_param.user_credentials)

    lm.login_view = 'web.login'
    lm.anonymous_user = ub.Anonymous
    lm.session_protection = 'strong' if config.config_session == 1 else "basic"

    from .calibre_init import init_calibre_db_from_config
    init_calibre_db_from_config(config, cli_param.settings_path)
    calibre_db.init_db()
    # -d: the databases now exist, so stop here (the Docker first run creates app.db this way)
    if cli_param.dry_run:
        sys.exit(0)

    requirements = dependency_check()
    for res in requirements:
        if res['found'] == "not installed":
            message = ('Cannot import {name} module, it is needed to run calibre-web, '
                       'please install it using "pip install {name}"').format(name=res["name"])
            log.info(message)
            print("*** " + message + " ***")
            web_server.stop(True)
            sys.exit(8)
    for res in requirements + dependency_check(True):
        log.info('*** "{}" version does not meet the requirements. '
                 'Should: {}, Found: {}, please consider installing required version ***'
                 .format(res['name'],
                         res['target'],
                         res['found']))
    app.wsgi_app = ReverseProxied(app.wsgi_app)

    cache_buster.init_cache_busting(app)
    log.info('Starting Calibre Web...')
    Principal(app)
    lm.init_app(app)
    app.secret_key = os.getenv('SECRET_KEY', config_sql.get_flask_session_key(ub.session))

    web_server.init_app(app, config)
    from .cw_babel import babel, get_locale
    if hasattr(babel, "localeselector"):
        babel.init_app(app)
        babel.localeselector(get_locale)
    else:
        babel.init_app(app, locale_selector=get_locale)

    # Initialize OAuth blueprints AFTER babel to ensure translations are loaded
    # Issue: OAuth blueprint generation was happening during module import (before babel init),
    # causing babel.list_translations() to return empty list and hiding language options
    if ub.oauth_support:
        try:
            from . import oauth_bb
            oauth_bb.init_oauth_blueprints()
            log.info("OAuth blueprints initialized successfully")
        except Exception as e:
            log.error("Failed to initialize OAuth blueprints: %s", e)

    from . import services

    if services.ldap:
        services.ldap.init_app(app, config)
    config.store_calibre_uuid(calibre_db, db.Library_Id)
    # Configure rate limiter
    # https://limits.readthedocs.io/en/stable/storage.html
    app.config.update(RATELIMIT_ENABLED=config.config_ratelimiter)
    if config.config_limiter_uri != "" and not cli_param.memory_backend:
        app.config.update(RATELIMIT_STORAGE_URI=config.config_limiter_uri)
        if config.config_limiter_options != "":
            app.config.update(RATELIMIT_STORAGE_OPTIONS=config.config_limiter_options)
    try:
        limiter.init_app(app)
    except Exception as e:
        log.error('Wrong Flask Limiter configuration, falling back to default: {}'.format(e))
        app.config.update(RATELIMIT_STORAGE_URI=None)
        limiter.init_app(app)

    if num_proxies == 0:
        # The TRUSTED_PROXY_COUNT default changed from 1 to 0. Warn once if requests arrive
        # through a proxy, so upgraded installs notice broken https URLs / shared rate limits.
        _proxy_warning = {'shown': False}

        @app.before_request
        def _warn_untrusted_proxy_headers():
            if _proxy_warning['shown']:
                return
            from flask import request
            if request.headers.get('X-Forwarded-For') or request.headers.get('X-Forwarded-Proto'):
                _proxy_warning['shown'] = True
                log.warning('Request contains X-Forwarded-* headers but TRUSTED_PROXY_COUNT=0, so they are '
                            'ignored: generated URLs may use http:// and all clients share the proxy IP for '
                            'rate limiting. Set TRUSTED_PROXY_COUNT to the number of reverse proxies in front of Lily.')

    # Register scheduled tasks
    # Ensure a valid calibre_db session exists before handling each request
    @app.before_request
    def _cwa_ensure_db_session():
        from flask import request

        if config.config_allow_reverse_proxy_header_login:
            """
            Load user from reverse proxy authentication header if configured.
            Sets g.flask_httpauth_user early so that current_user proxy resolves correctly
            for user-specific settings like theme preferences.

            This must run before any blueprint before_request handlers that access current_user.
            """

            from . import usermanagement
            user = usermanagement.load_user_from_reverse_proxy_header(request)
            if user:
                g.flask_httpauth_user = user
            else:
                # Explicitly set to None to indicate we checked but found nothing
                g.flask_httpauth_user = None

        try:
            calibre_db.ensure_session()
        except Exception:
            # Failsafe: let route-level code handle specific DB errors
            pass

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        if calibre_db.session_factory:
            calibre_db.session_factory.remove()

    from .render_template import close_request_cwa_db
    app.teardown_appcontext(close_request_cwa_db)

    from .schedule import register_scheduled_tasks, register_startup_tasks
    register_scheduled_tasks(config.schedule_reconnect)
    register_startup_tasks()

    return app

