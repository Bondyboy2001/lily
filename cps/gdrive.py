# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Google Drive endpoints: authentication, change-watch subscription and callbacks."""

import os
import hashlib
import hmac
import json
import secrets
from uuid import uuid4
from time import time
from shutil import move, copyfile

from flask import Blueprint, flash, request, redirect, url_for, abort
from flask import session as flask_session
from flask_babel import gettext as _

from . import logger, gdriveutils, config, ub, calibre_db, csrf
from .admin import admin_required
from .file_helper import get_temp_dir
from .usermanagement import user_login_required

gdrive = Blueprint('gdrive', __name__, url_prefix='/gdrive')
log = logger.create()

try:
    from googleapiclient.errors import HttpError
except ImportError as err:
    log.debug("Cannot import googleapiclient, using GDrive will not work: %s", err)

current_milli_time = lambda: int(round(time() * 1000))

# Shared secret Google echoes back in X-Goog-Channel-Token on every change notification.
# Generated once per install and kept next to the Drive credentials (it used to be a
# constant shared by every install, so anyone could forge notifications).
WATCH_TOKEN_FILE = os.path.join(os.path.dirname(gdriveutils.CREDENTIALS), 'gdrive_watch_token')
LEGACY_WATCH_TOKEN = 'target=calibreweb-watch_files'  # nosec - only used to warn about old channels
OAUTH_STATE_KEY = 'gdrive_oauth_state'


def get_watch_callback_token():
    """Return this install's webhook secret, creating it (mode 0600) on first use."""
    try:
        with open(WATCH_TOKEN_FILE, 'r') as f:
            token = f.read().strip()
        if token:
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_urlsafe(32)
    fd = os.open(WATCH_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(token)
    return token


def file_md5(path):
    """Hex MD5 of a file's contents, comparable with Drive's md5Checksum."""
    digest = hashlib.md5()  # nosec - matches Google Drive's checksum, not used for security
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


@gdrive.route("/authenticate")
@user_login_required
@admin_required
def authenticate_google_drive():
    try:
        auth = gdriveutils.Gauth.Instance().auth
        if auth.flow is None:
            auth.GetFlow()
        # Bind the OAuth round trip to this admin session (checked in the callback)
        state = secrets.token_urlsafe(32)
        flask_session[OAUTH_STATE_KEY] = state
        authUrl = auth.flow.step1_get_authorize_url(state=state)
    except gdriveutils.InvalidConfigError:
        flash(_('Google Drive setup not completed, try to deactivate and activate Google Drive again'),
              category="error")
        return redirect(url_for('web.index'))
    return redirect(authUrl)


@gdrive.route("/callback")
@user_login_required
@admin_required
def google_drive_callback():
    auth_code = request.args.get('code')
    expected_state = flask_session.pop(OAUTH_STATE_KEY, None)
    received_state = request.args.get('state') or ''
    if not auth_code or not expected_state or not hmac.compare_digest(expected_state, received_state):
        log.warning("Rejected Google Drive OAuth callback with a missing or mismatched state")
        abort(403)
    try:
        credentials = gdriveutils.Gauth.Instance().auth.flow.step2_exchange(auth_code)
        with open(gdriveutils.CREDENTIALS, 'w') as f:
            f.write(credentials.to_json())
    except (ValueError, AttributeError) as error:
        log.error(error)
    return redirect(url_for('admin.db_configuration'))


@gdrive.route("/watch/subscribe")
@user_login_required
@admin_required
def watch_gdrive():
    if not config.config_google_drive_watch_changes_response:
        with open(gdriveutils.CLIENT_SECRETS, 'r') as settings:
            filedata = json.load(settings)
        address = filedata['web']['redirect_uris'][0].rstrip('/').replace('/gdrive/callback', '/gdrive/watch/callback')
        notification_id = str(uuid4())
        try:
            result = gdriveutils.watchChange(gdriveutils.Gdrive.Instance().drive, notification_id,
                                 'web_hook', address, get_watch_callback_token(), current_milli_time() + 604800*1000)

            config.config_google_drive_watch_changes_response = result
            config.save()
        except HttpError as e:
            reason = json.loads(e.content)['error']['errors'][0]
            if reason['reason'] == 'push.webhookUrlUnauthorized':
                flash(_('Callback domain is not verified, '
                        'please follow steps to verify domain in google developer console'), category="error")
            else:
                flash(reason['message'], category="error")

    return redirect(url_for('admin.db_configuration'))


@gdrive.route("/watch/revoke")
@user_login_required
@admin_required
def revoke_watch_gdrive():
    last_watch_response = config.config_google_drive_watch_changes_response
    if last_watch_response:
        try:
            gdriveutils.stopChannel(gdriveutils.Gdrive.Instance().drive, last_watch_response['id'],
                                    last_watch_response['resourceId'])
        except (HttpError, AttributeError):
            pass
        config.config_google_drive_watch_changes_response = {}
        config.save()
    return redirect(url_for('admin.db_configuration'))


try:
    @csrf.exempt
    @gdrive.route("/watch/callback", methods=['GET', 'POST'])
    def on_received_watch_confirmation():
        if not config.config_google_drive_watch_changes_response:
            return ''
        channel_token = request.headers.get('X-Goog-Channel-Token') or ''
        if not hmac.compare_digest(channel_token.encode(), get_watch_callback_token().encode()):
            if channel_token == LEGACY_WATCH_TOKEN:
                log.warning('Ignoring Google Drive change notification for a channel created with the old '
                            'shared token; revoke and re-enable "watch metadata.db" in the database settings')
            return ''
        if request.headers.get('X-Goog-Resource-State') != 'change' or not request.data:
            return ''

        log.debug('%r', request.headers)
        log.debug('%r', request.data)
        log.info('Change received from gdrive')

        try:
            j = json.loads(request.data)
            log.info('Getting change details')
            response = gdriveutils.getChangeById(gdriveutils.Gdrive.Instance().drive, j['id'])
            log.debug('%r', response)
            if response:
                dbpath = os.path.join(config.config_calibre_dir, "metadata.db").encode()
                if not response['deleted'] and response['file']['title'] == 'metadata.db' \
                  and response['file']['md5Checksum'] != file_md5(dbpath):
                    tmp_dir = get_temp_dir()

                    log.info('Database file updated')
                    copyfile(dbpath, os.path.join(tmp_dir, "metadata.db_" + str(current_milli_time())))
                    log.info('Backing up existing and downloading updated metadata.db')
                    gdriveutils.downloadFile(None, "metadata.db", os.path.join(tmp_dir, "tmp_metadata.db"))
                    log.info('Setting up new DB')
                    # prevent error on windows, as os.rename does on existing files, also allow cross hdd move
                    move(os.path.join(tmp_dir, "tmp_metadata.db"), dbpath)
                    calibre_db.reconnect_db(config, ub.app_DB_path)
        except Exception as ex:
            log.error_or_exception(ex)
        return ''
except AttributeError:
    pass
