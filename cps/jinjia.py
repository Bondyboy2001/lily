# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

# custom jinja filters

import datetime
import mimetypes

from flask import Blueprint, request, url_for, g
from flask_babel import format_date
from .cw_login import current_user

from . import constants, logger

jinjia = Blueprint('jinjia', __name__)
log = logger.create()


# pagination links in jinja
@jinjia.app_template_filter('url_for_other_page')
def url_for_other_page(page):
    args = request.view_args.copy()
    args['page'] = page
    for get, val in request.args.items():
        args[get] = val
    return url_for(request.endpoint, **args)


# shortentitles to at longest nchar, shorten longer words if necessary
@jinjia.app_template_filter('shortentitle')
def shortentitle_filter(s, nchar=20):
    text = s.split()
    res = ""  # result
    suml = 0  # overall length
    for line in text:
        if suml >= 60:
            res += '...'
            break
        # if word longer than 20 chars truncate line and append '...', otherwise add whole word to result
        # string, and summarize total length to stop at chars given by nchar
        if len(line) > nchar:
            res += line[:(nchar-3)] + '[..] '
            suml += nchar+3
        else:
            res += line + ' '
            suml += len(line) + 1
    return res.strip()


@jinjia.app_template_filter('mimetype')
def mimetype_filter(val):
    return mimetypes.types_map.get('.' + val, 'application/octet-stream')


@jinjia.app_template_filter('formatdate')
def formatdate_filter(val):
    try:
        if isinstance(val, datetime.datetime):
            # Avoid timezone-based day shifts by formatting date-only values.
            val = val.date()
        return format_date(val, format='medium')
    except AttributeError as e:
        log.error('Babel error: %s, Current User: %s', e, current_user.name)
        return val


@jinjia.app_template_filter('formatpubdate')
def formatpubdate_filter(val):
    """A published date: just the year for 1 January, which is how a year alone is stored
    (Open Library and most files give only the year)."""
    day = val.date() if isinstance(val, datetime.datetime) else val
    if isinstance(day, datetime.date) and day.month == 1 and day.day == 1:
        return str(day.year)
    return formatdate_filter(val)


@jinjia.app_template_filter('formatdateinput')
def format_date_input(val):
    if isinstance(val, datetime.datetime):
        val = val.date()
    input_date = val.isoformat().split('T', 1)[0]  # Hack to support dates <1900
    return '' if input_date == "0101-01-01" else input_date


@jinjia.app_template_filter('yesno')
def yesno(value, yes, no):
    return yes if value else no


@jinjia.app_template_filter('ordinal')
def ordinal(number):
    """1 → "1st", 2 → "2nd", 11 → "11th", 23 → "23rd": an edition as people write it."""
    number = int(number)
    suffix = "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


@jinjia.app_template_filter('readable_formats')
def readable_formats_filter(book):
    from .helper import check_read_formats
    return check_read_formats(book)


@jinjia.app_template_filter('last_modified')
def book_last_modified(book):
    return str(int(book.last_modified.timestamp()))


# Grid covers show up to ~425px tall (a 300px card, A4), so each screen density gets the
# thumbnail at least that many device pixels tall: medium (510px) on 1x screens, large (1020px)
# on 2x and up. The small one (255px) was stretched ~1.4x and looked soft.
COVER_SRCSET = (('md', '1x'), ('lg', '2x'))


@jinjia.app_template_filter('get_cover_srcset')
def get_cover_srcset(book):
    return ', '.join(
        f"{url_for('web.get_cover', book_id=book.id, resolution=size, c=book_last_modified(book))} {density}"
        for size, density in COVER_SRCSET)


@jinjia.app_template_filter('filesizeformat_binary')
def filesizeformat_binary(num_bytes):
    """
    Format bytes to human-readable binary (power-of-2) file size.
    Uses KiB, MiB, GiB notation (1024-based) to match internal storage.
    This ensures consistency with email size limits and file system reporting.
    """
    if num_bytes is None:
        return '0 B'

    try:
        num_bytes = float(num_bytes)
    except (ValueError, TypeError):
        return '0 B'

    # Binary (power-of-2) units
    units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']
    unit_index = 0
    size = float(num_bytes)

    while size >= 1024.0 and unit_index < len(units) - 1:
        size /= 1024.0
        unit_index += 1

    # Format with 1 decimal place, but remove if .0
    if unit_index == 0:  # Bytes - no decimal
        return f"{int(size)} {units[unit_index]}"
    formatted = f"{size:.1f}"
    if formatted.endswith('.0'):
        formatted = formatted[:-2]
    return f"{formatted} {units[unit_index]}"


# a book's authors without calibre's "Unknown" stand-in, so a book with no author shows none
@jinjia.app_template_filter('named_authors')
def named_authors_filter(authors):
    return [author for author in authors or [] if not constants.is_unknown_author(author.name)]


# True once a metadata lookup has matched the book, for an editor's green check on its cover;
# the matched ids are read once per request
@jinjia.app_template_filter('metadata_fetched')
def metadata_fetched_filter(book_id):
    if not current_user.is_authenticated or not current_user.role_edit():
        return False
    fetched = g.get('_lily_fetched_ids')
    if fetched is None:
        try:
            from .render_template import get_request_cwa_db
            fetched = set(get_request_cwa_db().metadata_lookup_ids('matched'))
        except Exception as e:
            log.debug("Could not read the matched books: %s", e)
            fetched = set()
        g._lily_fetched_ids = fetched
    return book_id in fetched
