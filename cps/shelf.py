# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Shelves: create, edit, delete, order and add books."""

from datetime import datetime, UTC

from flask import Blueprint, flash, redirect, request, url_for, abort, jsonify
from flask_babel import gettext as _
from .cw_login import current_user
from sqlalchemy.exc import InvalidRequestError, OperationalError
from sqlalchemy.sql.expression import func, true

from . import calibre_db, config, db, logger, ub
from .render_template import render_title_template
from .usermanagement import login_required_if_no_ano, user_login_required
log = logger.create()

shelf = Blueprint('shelf', __name__)


@shelf.route("/shelf/create", methods=["GET", "POST"])
@user_login_required
def create_shelf():
    shelf = ub.Shelf()
    return create_edit_shelf(shelf, page_title=_("Create a Shelf"), page="shelfcreate")


@shelf.route("/shelf/edit/<int:shelf_id>", methods=["GET", "POST"])
@user_login_required
def edit_shelf(shelf_id):
    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.id == shelf_id).first()
    if not check_shelf_edit_permissions(shelf):
        flash(_("Sorry you are not allowed to edit this shelf"), category="error")
        return redirect(url_for('web.index'))
    return create_edit_shelf(shelf, page_title=_("Edit a Shelf"), page="shelfedit", shelf_id=shelf_id)


@shelf.route("/shelf/delete/<int:shelf_id>", methods=["POST"])
@user_login_required
def delete_shelf(shelf_id):
    cur_shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.id == shelf_id).first()
    try:
        if not delete_shelf_helper(cur_shelf):
            flash(_("Couldn't delete the shelf. Try again; if it keeps failing, check Logs in Settings."),
                  category="error")
        else:
            flash(_("Shelf successfully deleted"), category="success")
    except InvalidRequestError as e:
        ub.session.rollback()
        log.error_or_exception(f"Settings Database error: {e}")
        flash(_("Couldn't delete the shelf. Try again; if it keeps failing, check Logs in Settings."),
              category="error")
    return redirect(url_for('web.index'))


@shelf.route("/shelf/<int:shelf_id>", defaults={"sort_param": "stored", 'page': 1})
@shelf.route("/shelf/<int:shelf_id>/<sort_param>", defaults={'page': 1})
@shelf.route("/shelf/<int:shelf_id>/<sort_param>/<int:page>")
@login_required_if_no_ano
def show_shelf(shelf_id, sort_param, page):
    return render_show_shelf(shelf_id, page, sort_param)


@shelf.route("/shelf/order/<int:shelf_id>", methods=["GET", "POST"])
@user_login_required
def order_shelf(shelf_id):
    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.id == shelf_id).first()
    if shelf and check_shelf_view_permissions(shelf):
        if request.method == "POST":
            to_save = request.form.to_dict()
            books_in_shelf = ub.session.query(ub.BookShelf).filter(ub.BookShelf.shelf == shelf_id).order_by(
                ub.BookShelf.order.asc()).all()
            counter = 0
            for book in books_in_shelf:
                setattr(book, 'order', to_save[str(book.book_id)])
                counter += 1
                # if order different from before -> shelf.last_modified = datetime.now(timezone.utc)
            try:
                ub.session.commit()
            except (OperationalError, InvalidRequestError) as e:
                ub.session.rollback()
                log.error_or_exception(f"Settings Database error: {e}")
                flash(_("Couldn't save the shelf order. Try again; if it keeps failing, check Logs in Settings."),
                      category="error")

        result = []
        if shelf:
            result = calibre_db.session.query(db.Books) \
                .join(ub.BookShelf, ub.BookShelf.book_id == db.Books.id, isouter=True) \
                .add_columns(calibre_db.common_filters().label("visible")) \
                .filter(ub.BookShelf.shelf == shelf_id).order_by(ub.BookShelf.order.asc()).all()
        return render_title_template('shelf_order.html', entries=result,
                                     title=_("Change Order of Shelf: %(name)s", name=shelf.name),
                                     shelf=shelf, page="shelforder")
    abort(404)


def check_shelf_edit_permissions(cur_shelf):
    if not cur_shelf.is_public and not cur_shelf.user_id == int(current_user.id):
        log.error(f"User {current_user.id} not allowed to edit shelf: {cur_shelf.name}")
        return False
    if cur_shelf.is_public and not current_user.role_edit_shelfs():
        log.info(f"User {current_user.id} not allowed to edit public shelves")
        return False
    return True


def check_shelf_view_permissions(cur_shelf):
    try:
        if cur_shelf.is_public:
            return True
        if current_user.is_anonymous or cur_shelf.user_id != current_user.id:
            log.error(f"User is unauthorized to view non-public shelf: {cur_shelf.name}")
            return False
    except Exception as e:
        log.error(e)
    return True


# if shelf ID is set, we are editing a shelf
def create_edit_shelf(shelf, page_title, page, shelf_id=False):
    if request.method == "POST":
        to_save = request.form.to_dict()
        if not current_user.role_edit_shelfs() and to_save.get("is_public") == "on":
            flash(_("Sorry you are not allowed to create a public shelf"), category="error")
            return redirect(url_for('web.index'))
        is_public = 1 if to_save.get("is_public") == "on" else 0
        shelf_title = to_save.get("title", "")
        if check_shelf_is_unique(shelf_title, is_public, shelf_id):
            shelf.name = shelf_title
            shelf.is_public = is_public
            if not shelf_id:
                shelf.user_id = int(current_user.id)
                ub.session.add(shelf)
                shelf_action = "created"
                flash_text = _("Shelf %(title)s created", title=shelf_title)
            else:
                shelf_action = "changed"
                flash_text = _("Shelf %(title)s changed", title=shelf_title)
            try:
                ub.session.commit()
                log.info(f"Shelf {shelf_title} {shelf_action}")
                flash(flash_text, category="success")
                return redirect(url_for('shelf.show_shelf', shelf_id=shelf.id))
            except (OperationalError, InvalidRequestError) as ex:
                ub.session.rollback()
                log.error_or_exception(ex)
                log.error_or_exception(f"Settings Database error: {ex}")
                flash(_("Couldn't save the shelf. Try again; if it keeps failing, check Logs in Settings."),
                      category="error")
            except Exception as ex:
                ub.session.rollback()
                log.error_or_exception(ex)
                flash(_("Couldn't save the shelf. Try again; if it keeps failing, check Logs in Settings."),
                      category="error")
    book_count = ub.session.query(ub.BookShelf).filter(ub.BookShelf.shelf == shelf_id).count() if shelf_id else 0
    return render_title_template('shelf_edit.html',
                                 shelf=shelf,
                                 book_count=book_count,
                                 title=page_title,
                                 page=page)


def check_shelf_is_unique(title, is_public, shelf_id=False):
    if shelf_id:
        ident = ub.Shelf.id != shelf_id
    else:
        ident = true()
    if is_public == 1:
        is_shelf_name_unique = ub.session.query(ub.Shelf) \
                                   .filter((ub.Shelf.name == title) & (ub.Shelf.is_public == 1)) \
                                   .filter(ident) \
                                   .first() is None

        if not is_shelf_name_unique:
            log.error(f"A public shelf with the name '{title}' already exists.")
            flash(_("A public shelf with the name '%(title)s' already exists.", title=title),
                  category="error")
    else:
        is_shelf_name_unique = ub.session.query(ub.Shelf) \
                                   .filter((ub.Shelf.name == title) & (ub.Shelf.is_public == 0) &
                                           (ub.Shelf.user_id == int(current_user.id))) \
                                   .filter(ident) \
                                   .first() is None

        if not is_shelf_name_unique:
            log.error(f"A private shelf with the name '{title}' already exists.")
            flash(_("A private shelf with the name '%(title)s' already exists.", title=title),
                  category="error")
    return is_shelf_name_unique


def delete_shelf_helper(cur_shelf):
    if not cur_shelf or not check_shelf_edit_permissions(cur_shelf):
        return False
    shelf_id = cur_shelf.id
    ub.session.delete(cur_shelf)
    ub.session.query(ub.BookShelf).filter(ub.BookShelf.shelf == shelf_id).delete()
    ub.session_commit(f"successfully deleted Shelf {cur_shelf.name}")
    return True


def change_shelf_order(shelf_id, order):
    result = calibre_db.session.query(db.Books).outerjoin(db.books_series_link,
                                                          db.Books.id == db.books_series_link.c.book)\
        .outerjoin(db.Series).join(ub.BookShelf, ub.BookShelf.book_id == db.Books.id) \
        .filter(ub.BookShelf.shelf == shelf_id).order_by(*order).all()
    for index, entry in enumerate(result):
        book = ub.session.query(ub.BookShelf).filter(ub.BookShelf.shelf == shelf_id) \
            .filter(ub.BookShelf.book_id == entry.id).first()
        book.order = index
    ub.session_commit(f"Shelf-id:{shelf_id} - Order changed")


def render_show_shelf(shelf_id, page_no, sort_param):
    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.id == shelf_id).first()
    # The shelf page no longer offers the "Change order" lock, so a lock saved
    # before it went must not freeze the sort menu.
    status = 'off'
    # check user is allowed to access shelf
    if shelf and check_shelf_view_permissions(shelf):
        if status != 'on':
            if sort_param == 'stored':
                sort_param = current_user.get_view_property("shelf", 'stored')
            else:
                current_user.set_view_property("shelf", 'stored', sort_param)
            if sort_param == 'pubnew':
                change_shelf_order(shelf_id, [db.Books.pubdate.desc()])
            if sort_param == 'pubold':
                change_shelf_order(shelf_id, [db.Books.pubdate])
            if sort_param == 'shelfnew':
                change_shelf_order(shelf_id, [ub.BookShelf.date_added.desc()])
            if sort_param == 'shelfold':
                change_shelf_order(shelf_id, [ub.BookShelf.date_added])
            if sort_param == 'abc':
                change_shelf_order(shelf_id, [db.Books.sort])
            if sort_param == 'zyx':
                change_shelf_order(shelf_id, [db.Books.sort.desc()])
            if sort_param == 'new':
                change_shelf_order(shelf_id, [db.Books.timestamp.desc()])
            if sort_param == 'old':
                change_shelf_order(shelf_id, [db.Books.timestamp])
            if sort_param == 'authaz':
                change_shelf_order(shelf_id, [db.Books.author_sort.asc(), db.Series.name, db.Books.series_index])
            if sort_param == 'authza':
                change_shelf_order(shelf_id, [db.Books.author_sort.desc(),
                                              db.Series.name.desc(),
                                              db.Books.series_index.desc()])

        result, pagination = calibre_db.fill_indexpage(page_no, 0,
                                                           db.Books,
                                                           ub.BookShelf.shelf == shelf_id,
                                                           [ub.BookShelf.order.asc()],
                                                           True, config.config_read_column,
                                                           ub.BookShelf, ub.BookShelf.book_id == db.Books.id,
                                                           cards_only=True)
        # delete shelf entries where book is not existent anymore, can happen if book is deleted outside calibre-web
        wrong_entries = calibre_db.session.query(ub.BookShelf) \
            .join(db.Books, ub.BookShelf.book_id == db.Books.id, isouter=True) \
            .filter(db.Books.id == None).all()
        for entry in wrong_entries:
            log.info(f'Not existing book {entry.book_id} in {shelf} deleted')
            try:
                ub.session.query(ub.BookShelf).filter(ub.BookShelf.book_id == entry.book_id).delete()
                ub.session.commit()
            except (OperationalError, InvalidRequestError) as e:
                ub.session.rollback()
                log.error_or_exception(f"Settings Database error: {e}")
                flash(_("Couldn't tidy up books missing from this shelf. If it keeps failing, check Logs in Settings."),
                      category="error")

        return render_title_template("shelf.html",
                                     entries=result,
                                     pagination=pagination,
                                     title=_("Shelf: %(name)s", name=shelf.name),
                                     shelf=shelf,
                                     page="shelf",
                                     status=status,
                                     order=sort_param)
    flash(_("Error opening shelf. Shelf does not exist or is not accessible"), category="error")
    return redirect(url_for("web.index"))


@shelf.route("/shelf/<int:shelf_id>/book/<int:book_id>", methods=["POST"])
@user_login_required
def set_book_on_shelf(shelf_id, book_id):
    """Put one book on a shelf or take it off: JSON {"on": true|false}. Used by the remove
    button on shelf pages. Answers {"on": bool, "count": int}."""
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get("on"), bool):
        return jsonify({"message": _("Say whether the book goes on the shelf or comes off it.")}), 400
    cur_shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.id == shelf_id).first()
    if cur_shelf is None:
        return jsonify({"message": _("That shelf doesn't exist any more.")}), 404
    if not check_shelf_edit_permissions(cur_shelf):
        return jsonify({"message": _("You can't change this shelf.")}), 403
    if not calibre_db.session.query(db.Books.id).filter(db.Books.id == book_id).first():
        return jsonify({"message": _("That book isn't in your library any more.")}), 404
    link = ub.session.query(ub.BookShelf).filter(ub.BookShelf.shelf == shelf_id,
                                                 ub.BookShelf.book_id == book_id).first()
    try:
        if data["on"] and not link:
            max_order = ub.session.query(func.max(ub.BookShelf.order)).filter(
                ub.BookShelf.shelf == shelf_id).scalar()
            cur_shelf.books.append(ub.BookShelf(shelf=shelf_id, book_id=book_id, order=(max_order or 0) + 1))
            cur_shelf.last_modified = datetime.now(UTC)
        elif not data["on"] and link:
            ub.session.delete(link)
            cur_shelf.last_modified = datetime.now(UTC)
        ub.session.commit()
    except (OperationalError, InvalidRequestError) as e:
        ub.session.rollback()
        log.error_or_exception("Could not change shelf %s for book %s: %s", shelf_id, book_id, e)
        return jsonify({"message": _("Couldn't change the shelf. Try again; if it keeps failing, "
                                     "check Logs in Settings.")}), 500
    count = ub.session.query(func.count(ub.BookShelf.id)).filter(ub.BookShelf.shelf == shelf_id).scalar()
    return jsonify({"on": data["on"], "count": count or 0})
