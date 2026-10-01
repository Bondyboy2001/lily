# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Admin Logs page: a bounded tail of the app log and the captured service output."""

import hashlib
import logging
import os
import re
import stat

from flask import Blueprint, jsonify, make_response, request
from flask_babel import gettext as _

from . import logger
from .admin import admin_required
from .render_template import render_title_template
from .usermanagement import user_login_required

logs = Blueprint('logs', __name__)
log = logger.create()

S6_UNCAUGHT_LOG_DIR = os.environ.get('S6_UNCAUGHT_LOG_DIR', '/run/uncaught-logs')
RETAINED_LOG_DIR = os.environ.get('LILY_RETAINED_LOG_DIR', '/config/logs')
MAX_LOG_BYTES = 1024 * 1024


def _rotated_dir_files(log_dir, prefix):
    """An s6-log dir: `current` first, then rotated @timestamp.s/.u files newest first."""
    try:
        names = os.listdir(log_dir)
    except OSError:
        return []
    entries = []
    if 'current' in names:
        entries.append((os.path.join(log_dir, 'current'), _("%(prefix)s (current)", prefix=prefix)))
    rotated = sorted(
        (n for n in names if n.startswith('@') and (n.endswith('.s') or n.endswith('.u'))),
        reverse=True)
    entries.extend((os.path.join(log_dir, n), _("%(prefix)s %(name)s", prefix=prefix, name=n))
                   for n in rotated)
    return entries


def _s6_uncaught_files(log_dir=None):
    return _rotated_dir_files(log_dir or S6_UNCAUGHT_LOG_DIR, _("Service output"))


def _retained_log_files(log_dir=None):
    """Per-service s6-log dirs under /config/logs, one entry set per service."""
    log_dir = log_dir or RETAINED_LOG_DIR
    try:
        names = sorted(os.listdir(log_dir))
    except OSError:
        return []
    entries = []
    for name in names:
        sub = os.path.join(log_dir, name)
        if os.path.isdir(sub):
            entries.extend(_rotated_dir_files(sub, name))
    return entries


def _configured_log_files():
    """Real files the configured loggers write to, each followed by its .1/.2… rotations."""
    loggers = [logging.root]
    for obj in logging.Logger.manager.loggerDict.values():
        if isinstance(obj, logging.Logger) and obj.handlers:
            loggers.append(obj)
    bases = []
    for logger_ in loggers:
        for handler in logger_.handlers:
            base = getattr(handler, 'baseFilename', None)
            if not base or base in (logger.LOG_TO_STDOUT, logger.LOG_TO_STDERR) or base.startswith('/dev/'):
                continue
            base = os.path.abspath(base)
            if base not in bases:
                bases.append(base)
    entries = []
    for base in bases:
        if os.path.exists(base):
            entries.append((base, _("Log file %(name)s", name=os.path.basename(base))))
        index = 1
        while os.path.exists('%s.%d' % (base, index)):
            rotated = '%s.%d' % (base, index)
            entries.append((rotated, _("Log archive %(name)s", name=os.path.basename(rotated))))
            index += 1
    return entries


# s6 service dirs whose names still carry the upstream project's name.
SERVICE_NAMES = {'svc-calibre-web-automated': 'Lily web app'}


def _service_name(dir_name):
    """'cwa-auto-zipper' -> 'Auto zipper'; the web app's own dir gets its real name."""
    if dir_name in SERVICE_NAMES:
        return _(SERVICE_NAMES[dir_name])
    name = re.sub(r'^(cwa|lily|svc)[-_]', '', dir_name).replace('-', ' ').replace('_', ' ').strip()
    return name[:1].upper() + name[1:] if name else dir_name


def _discover_sources():
    """Allowlisted sources as {id, label, path, group}; ids are opaque, never client-supplied paths.
    `group` names the service a file belongs to: a log dir's current file and its rotations, or a
    log file and its .1/.2… archives, share one."""
    entries = [(path, label, _("Service output")) for path, label in _s6_uncaught_files()]
    entries += [(path, label, _service_name(os.path.basename(os.path.dirname(path))))
                for path, label in _retained_log_files()]
    entries += [(path, label, re.sub(r'\.\d+$', '', os.path.basename(path)))
                for path, label in _configured_log_files()]
    sources = []
    for path, label, group in entries:
        source_id = 'src-' + hashlib.sha256(os.path.realpath(path).encode('utf-8')).hexdigest()[:16]
        sources.append({'id': source_id, 'label': label, 'path': path, 'group': group})
    return sources


def _source_menu(sources):
    """One menu entry per group, in discovery order, with its files newest first."""
    menu = {}
    for source in sources:
        group = source.get('group')
        if group is None:
            key, entry = source['id'], {'id': source['id'], 'label': source['label']}
        else:
            key = 'grp-' + hashlib.sha256(group.encode('utf-8')).hexdigest()[:16]
            entry = {'id': key, 'label': group}
        menu.setdefault(key, dict(entry, members=[]))['members'].append(source)
    return list(menu.values())


def _resolve_file(path):
    """Realpath of a discovered file, or None when it escapes its own directory,
    is missing mid-rotation, or is not a regular file."""
    allowed_root = os.path.realpath(os.path.dirname(path))
    real = os.path.realpath(path)
    if os.path.dirname(real) != allowed_root:
        return None
    try:
        st = os.stat(real)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return real


def _tail_file(path, budget):
    """Last ``budget`` bytes of ``path`` as text. Returns (text, was_truncated, bytes_read)."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        size = st.st_size
        amount = min(size, budget)
        if amount < size:
            os.lseek(fd, size - amount, os.SEEK_SET)
        data = os.read(fd, amount)
    finally:
        os.close(fd)
    truncated = len(data) < size
    text = data.decode('utf-8', errors='replace')
    if truncated:
        newline = text.find('\n')
        text = '' if newline == -1 else text[newline + 1:]
    return text, truncated, len(data)


def _read_sources(sources, max_bytes=MAX_LOG_BYTES):
    """Concatenate tails newest-file first within a global raw-byte budget."""
    parts = []
    truncated = False
    remaining = max_bytes
    for source in sources:
        if remaining <= 0:
            truncated = True
            break
        real = _resolve_file(source['path'])
        if real is None:
            continue
        try:
            result = _tail_file(real, remaining)
        except OSError:
            continue
        if result is None:
            continue
        text, cut, bytes_read = result
        truncated = truncated or cut
        remaining -= bytes_read
        if text:
            parts.append('===== %s =====\n%s' % (source['label'], text))
    return '\n'.join(parts), truncated


def _no_store(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


@logs.route("/logs")
@user_login_required
@admin_required
def show_logs():
    return _no_store(make_response(render_title_template('logs.html', title=_('Logs'), page='logs')))


@logs.route("/logs/data")
@user_login_required
@admin_required
def logs_data():
    source = request.args.get('source', 'all')
    sources = _discover_sources()
    menu = _source_menu(sources)
    if source == 'all':
        selected = sources
    else:
        selected = next((m['members'] for m in menu if m['id'] == source), None) \
            or [s for s in sources if s['id'] == source]
        if not selected:
            return _no_store(jsonify({'success': False, 'error': 'Unknown log source'})), 400
    text, truncated = _read_sources(selected)
    return _no_store(jsonify({
        'success': True,
        'sources': [{'id': m['id'], 'label': m['label']} for m in menu],
        'text': text,
        'truncated': truncated,
    }))
