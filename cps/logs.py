# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Admin Logs page: the latest metadata lookups, each with what it changed, over a live,
bounded tail of the app log and the captured service output."""

import hashlib
import json
import logging
import os
import re
import stat
from datetime import datetime

from flask import Blueprint, jsonify, make_response, request
from flask_babel import format_datetime, gettext as _

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
    """An s6-log dir oldest first: rotated @timestamp.s/.u files, then `current` last."""
    try:
        names = os.listdir(log_dir)
    except OSError:
        return []
    rotated = sorted(n for n in names if n.startswith('@') and (n.endswith(('.s', '.u'))))
    entries = [(os.path.join(log_dir, n), _("%(prefix)s %(name)s", prefix=prefix, name=n))
               for n in rotated]
    if 'current' in names:
        entries.append((os.path.join(log_dir, 'current'), _("%(prefix)s (current)", prefix=prefix)))
    return entries


def _s6_uncaught_files(log_dir=None):
    return _rotated_dir_files(log_dir or S6_UNCAUGHT_LOG_DIR, _("Service output"))


def _retained_log_files(log_dir=None):
    """Per-service s6-log dirs under /config/logs, labelled with the service's readable name."""
    log_dir = log_dir or RETAINED_LOG_DIR
    try:
        names = sorted(os.listdir(log_dir))
    except OSError:
        return []
    entries = []
    for name in names:
        sub = os.path.join(log_dir, name)
        if os.path.isdir(sub):
            entries.extend(_rotated_dir_files(sub, _service_name(name)))
    return entries


def _configured_log_files():
    """Real files the configured loggers write to, each after its …/.2/.1 rotations (oldest first)."""
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
        archives = []
        index = 1
        while os.path.exists('%s.%d' % (base, index)):
            rotated = '%s.%d' % (base, index)
            archives.append((rotated, _("Log archive %(name)s", name=os.path.basename(rotated))))
            index += 1
        entries.extend(reversed(archives))
        if os.path.exists(base):
            entries.append((base, _("Log file %(name)s", name=os.path.basename(base))))
    return entries


# s6 service dirs whose names still carry the upstream project's name.
SERVICE_NAMES = {'svc-calibre-web-automated': 'Lily web app'}


def _service_name(dir_name):
    """'cwa-auto-library' -> 'Auto library'; the web app's own dir gets its real name."""
    if dir_name in SERVICE_NAMES:
        return _(SERVICE_NAMES[dir_name])
    name = re.sub(r'^(cwa|lily|svc)[-_]', '', dir_name).replace('-', ' ').replace('_', ' ').strip()
    return name[:1].upper() + name[1:] if name else dir_name


def _discover_sources():
    """Allowlisted sources as {id, label, path}, each service's files oldest first so new
    lines land at the end of its section. Paths come from discovery, never from the client."""
    entries = _s6_uncaught_files() + _retained_log_files() + _configured_log_files()
    return [{'id': 'src-' + hashlib.sha256(os.path.realpath(path).encode('utf-8')).hexdigest()[:16],
             'label': label, 'path': path}
            for path, label in entries]


def _sources_version(sources):
    """A cheap fingerprint of the sources' sizes and mtimes, so an unchanged poll reads nothing."""
    digest = hashlib.sha256()
    for source in sources:
        try:
            st = os.stat(source['path'])
        except OSError:
            continue
        digest.update(('%s\0%d\0%d\n' % (source['path'], st.st_size, st.st_mtime_ns)).encode('utf-8'))
    return digest.hexdigest()[:16]


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
    """Concatenate tails in the given (oldest-first) order within a global raw-byte budget.
    The budget is spent from the last source backwards, so the newest lines always fit."""
    parts = []
    truncated = False
    remaining = max_bytes
    for source in reversed(sources):
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
    return '\n'.join(reversed(parts)), truncated


def _no_store(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


# Lookups listed at first, and how many more "Show more" adds
LOOKUPS_SHOWN = 100


def _field_labels():
    return {'title': _('Title'), 'authors': _('Authors'), 'description': _('Description'),
            'publisher': _('Publisher'), 'tags': _('Tags'), 'pubdate': _('Published'),
            'identifiers': _('Identifiers'), 'cover': _('Cover')}


def _lookup_rows(limit):
    """The newest lookups for the page, newest first, and how many there are in all. Each is
    {book_id, title, exists, status, source, when, iso, changes: [(label, before, after)]}."""
    import sys
    if '/app/calibre-web-automated/scripts/' not in sys.path:
        sys.path.insert(1, '/app/calibre-web-automated/scripts/')
    from cwa_db import CWA_DB
    from . import calibre_db, db
    try:
        store = CWA_DB()
        entries, total = store.recent_metadata_lookups(limit), store.count_metadata_lookups()
    except Exception as e:
        log.debug("No metadata lookups to list: %s", e)
        return [], 0
    ids = {entry['book_id'] for entry in entries}
    present = {row[0] for row in calibre_db.session.query(db.Books.id).filter(db.Books.id.in_(ids))} if ids else set()
    labels = _field_labels()
    rows = []
    for entry in entries:
        try:
            changes = json.loads(entry['changes'] or '{}')
        except ValueError:
            changes = {}
        try:
            when = datetime.fromisoformat(entry['checked_at'])
            shown = format_datetime(when, 'd MMM, HH:mm')
        except (TypeError, ValueError):
            shown = entry['checked_at']
        rows.append({
            'book_id': entry['book_id'], 'title': entry['title'] or _('Book %(id)s', id=entry['book_id']),
            'exists': entry['book_id'] in present, 'status': entry['status'], 'source': entry['source'],
            'when': shown, 'iso': entry['checked_at'],
            'changes': [(labels.get(field, field), old, new) for field, (old, new) in changes.items()],
        })
    return rows, total


@logs.route("/logs")
@user_login_required
@admin_required
def show_logs():
    try:
        limit = min(max(int(request.args.get('lookups', LOOKUPS_SHOWN)), LOOKUPS_SHOWN), 5000)
    except ValueError:
        limit = LOOKUPS_SHOWN
    lookups, total = _lookup_rows(limit)
    return _no_store(make_response(render_title_template(
        'logs.html', title=_('Logs'), page='logs', lookups=lookups, lookups_total=total,
        more=min(limit + LOOKUPS_SHOWN * 4, 5000) if total > limit else None)))


@logs.route("/logs/data")
@user_login_required
@admin_required
def logs_data():
    """Every source combined. `since` is the last version the page saw; if nothing has
    changed the reply skips the read and says so."""
    sources = _discover_sources()
    version = _sources_version(sources)
    if request.args.get('since') == version:
        return _no_store(jsonify({'success': True, 'version': version, 'unchanged': True}))
    text, truncated = _read_sources(sources)
    return _no_store(jsonify({
        'success': True,
        'version': version,
        'text': text,
        'truncated': truncated,
    }))
