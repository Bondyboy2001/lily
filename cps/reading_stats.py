# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Per-user reading summary: what you finished in a year, by month, author and tag.

"Finished" is the read-status flag a user sets (or the reader sets at the end of
a book); its timestamp is the finish date, so editing a status later moves it.
"""

from collections import Counter
from datetime import datetime

from flask import Blueprint, request
from flask_babel import gettext as _

from . import calibre_db, db, logger, ub
from .cw_login import current_user
from .render_template import render_title_template
from .usermanagement import user_login_required

reading = Blueprint('reading', __name__)
log = logger.create()

TOP_N = 5


def summarise(finished, year):
    """finished: [{'when': datetime, 'title': str, 'authors': [str], 'tags': [str]}] for one year.

    Returns totals, a 12-slot monthly list, and the top authors and tags."""
    months = [0] * 12
    authors, tags = Counter(), Counter()
    for item in finished:
        months[item['when'].month - 1] += 1
        authors.update(item.get('authors') or [])
        tags.update(item.get('tags') or [])
    busiest = max(range(12), key=lambda m: months[m]) if finished else None
    return {
        'year': year,
        'total': len(finished),
        'months': months,
        'max_month': max(months) if finished else 0,
        'busiest_month': busiest + 1 if busiest is not None else None,
        'top_authors': authors.most_common(TOP_N),
        'top_tags': tags.most_common(TOP_N),
        'recent': [i['title'] for i in sorted(finished, key=lambda i: i['when'], reverse=True)[:TOP_N]],
        # The same books as (id, title), so the page can link them; id is None when unknown.
        'recent_books': [(i.get('id'), i['title'])
                         for i in sorted(finished, key=lambda i: i['when'], reverse=True)[:TOP_N]],
    }


def _finished_in_year(user_id, year):
    start, end = datetime(year, 1, 1), datetime(year + 1, 1, 1)
    rows = ub.session.query(ub.ReadBook).filter(
        ub.ReadBook.user_id == user_id,
        ub.ReadBook.read_status == ub.ReadBook.STATUS_FINISHED,
        ub.ReadBook.last_modified >= start, ub.ReadBook.last_modified < end).all()
    when = {r.book_id: r.last_modified for r in rows}
    if not when:
        return []
    calibre_db.ensure_session()
    books = (calibre_db.session.query(db.Books).filter(db.Books.id.in_(list(when)))
             .filter(calibre_db.common_filters(allow_show_archived=True)).all())
    return [{'when': when[b.id], 'id': b.id, 'title': b.title,
             'authors': [a.name.replace('|', ',') for a in b.authors],
             'tags': [t.name for t in b.tags]} for b in books]


def _years_with_activity(user_id):
    from sqlalchemy import func
    rows = ub.session.query(func.strftime('%Y', ub.ReadBook.last_modified)).filter(
        ub.ReadBook.user_id == user_id,
        ub.ReadBook.read_status == ub.ReadBook.STATUS_FINISHED).distinct().all()
    return sorted({int(r[0]) for r in rows if r[0]}, reverse=True)


@reading.route('/reading', methods=['GET'])
@user_login_required
def reading_summary():
    if current_user.role_anonymous():
        return render_title_template('reading_stats.html', title=_("My Reading"), page="reading",
                                     summary=None, years=[], month_names=[])
    this_year = datetime.now().year
    year = request.args.get('year', default=this_year, type=int)
    year = year if 1970 <= year <= this_year + 1 else this_year
    summary = summarise(_finished_in_year(current_user.id, year), year)
    years = _years_with_activity(current_user.id) or [this_year]
    month_names = [datetime(2000, m, 1).strftime('%b') for m in range(1, 13)]
    return render_title_template('reading_stats.html', title=_("My Reading"), page="reading",
                                 summary=summary, years=years, month_names=month_names)
