# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The authors list page.

Routes are attached to the web blueprint; web.py imports this module at its end."""


from flask import abort
from .cw_login import current_user
from sqlalchemy.sql.expression import text, func

from . import constants
from . import db
from . import calibre_db
from .usermanagement import login_required_if_no_ano
from .render_template import render_title_template

from .web import web


@web.route("/author")
@login_required_if_no_ano
def author_list():
    if current_user.check_visibility(constants.SIDEBAR_AUTHOR):
        # By first name, as displayed ("Jane Austen"), not Calibre's "Austen, Jane" sort key
        if current_user.get_view_property('author', 'dir') == 'desc':
            order = func.lower(db.Authors.name).desc()
            order_no = 0
        else:
            order = func.lower(db.Authors.name).asc()
            order_no = 1
        entries = calibre_db.session.query(db.Authors, func.count('books_authors_link.book').label('count')) \
            .join(db.books_authors_link).join(db.Books).filter(calibre_db.common_filters()) \
            .filter(func.lower(db.Authors.name) != constants.UNKNOWN_AUTHOR.lower()) \
            .group_by(text('books_authors_link.author')).order_by(order).all()
        # No initials filter on the authors page: the list is sorted, so the letter menu only adds noise
        return render_title_template('list.html', entries=entries, charlist=[],
                                     title="Authors", page="authorlist", data='author', order=order_no)
    abort(404)
