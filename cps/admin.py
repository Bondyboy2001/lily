# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Admin pages: users and their restrictions, plus the admin-only maintenance endpoints."""

import os
import json
from functools import wraps

from flask import Blueprint, flash, redirect, url_for, abort, request, make_response, g, Response, jsonify
from flask import session as flask_session
from .cw_login import current_user
from flask_babel import gettext as _
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.exc import IntegrityError, OperationalError, InvalidRequestError
from sqlalchemy.sql.expression import func, text

from . import constants, logger, helper, cli_param
from . import db, calibre_db, ub, web_server, config
from werkzeug.security import generate_password_hash
from .helper import check_username
from .render_template import render_title_template, get_sidebar_config
from .services.worker import WorkerThread
from .usermanagement import user_login_required
from .cw_babel import get_available_translations, get_available_locale, get_user_locale_language
from . import debug_info
from .string_helper import strip_whitespaces

log = logger.create()

admi = Blueprint('admin', __name__)


def admin_required(f):
    """
    Checks if current_user.role == 1
    """

    @wraps(f)
    def inner(*args, **kwargs):
        if current_user.role_admin():
            return f(*args, **kwargs)
        abort(403)

    return inner


@admi.before_app_request
def before_request():
    # Safety net: if not configured but metadata.db now exists at default location, auto-set without redirect loop
    if not config.db_configured:
        try:
            default_metadata = '/calibre-library/metadata.db'
            if (not config.config_calibre_dir or not os.path.isfile(os.path.join(config.config_calibre_dir, 'metadata.db'))) \
                    and os.path.isfile(default_metadata):
                config.config_calibre_dir = os.path.dirname(default_metadata)
                log.info('[autoconfig] Late-detected calibre library at %s; updating config and rebuilding db session', config.config_calibre_dir)
                try:
                    config.save()
                except Exception as e:
                    log.error('Failed to save late autoconfig: %s', e)
                # Re-run calibre db setup so subsequent handlers see a configured DB
                from . import db as _db, cli_param as _cli_param
                _db.CalibreDB.update_config(config)
                _db.CalibreDB.setup_db(config.config_calibre_dir, _cli_param.settings_path)
        except Exception as e:
            log.error('Autoconfig safety net error: %s', e)
    # If config says DB is configured but session is unavailable, try to recover and force reconfig on failure
    if config.db_configured:
        try:
            calibre_db.ensure_session()
        except Exception as e:
            log.error("ensure_session failed in before_request: %s", e)
        if calibre_db.session is None:
            log.error("Calibre DB session unavailable")
            config.db_configured = False
    g.constants = constants
    g.google_site_verification = os.getenv('GOOGLE_SITE_VERIFICATION', '')
    g.allow_anonymous = config.config_anonbrowse
    g.allow_upload = config.config_uploading
    g.config_authors_max = config.config_authors_max
    # The library lives at /calibre-library (autoconfigured above); there is no page to point
    # Lily elsewhere, so without a usable metadata.db every page but login says so.
    if '/static/' not in request.path and not config.db_configured and \
            request.endpoint not in ('web.login', 'web.login_post', 'web.logout'):
        abort(503, description=_("No Calibre library found at /calibre-library. Mount a library "
                                 "folder containing metadata.db there and restart Lily."))


@admi.route("/shutdown", methods=["POST"])
@user_login_required
@admin_required
def shutdown():
    task = request.get_json().get('parameter', -1)
    show_text = {}
    if task in (0, 1):  # valid commandos received
        # close all database connections
        calibre_db.dispose()
        ub.dispose()

        if task == 0:
            show_text['text'] = _('Server restarted, please reload page.')
        else:
            show_text['text'] = _('Performing Server shutdown, please close window.')
        # stop gevent/tornado server
        web_server.stop(task == 0)
        return json.dumps(show_text)

    if task == 2:
        log.warning("reconnecting to calibre database")
        calibre_db.reconnect_db(config, ub.app_DB_path)
        show_text['text'] = _('Success! Database Reconnected')
        return json.dumps(show_text)

    show_text['text'] = _('Unknown command')
    return json.dumps(show_text), 400


@admi.route("/metadata_backup", methods=["POST"])
@user_login_required
@admin_required
def queue_metadata_backup():
    show_text = {}
    log.warning("Queuing all books for metadata backup")
    helper.set_all_metadata_dirty()
    show_text['text'] = _('Success! Books queued for Metadata Backup, please check Tasks for result')
    return json.dumps(show_text)


@admi.route("/hardcover_auto_fetch", methods=["POST"])
@user_login_required
@admin_required
def trigger_hardcover_auto_fetch():
    """Manually trigger Hardcover auto-fetch task"""
    show_text = {}

    try:
        # Check if token is available
        from os import getenv
        token_available = bool(
            getattr(config, "config_hardcover_token", None) or
            getenv("HARDCOVER_TOKEN")
        )

        if not token_available:
            show_text['text'] = _('Error: No Hardcover token available. Set the HARDCOVER_TOKEN environment variable.')
            return json.dumps(show_text), 400

        # Get settings
        import sys as _sys
        if '/app/calibre-web-automated/scripts/' not in _sys.path:
            _sys.path.insert(1, '/app/calibre-web-automated/scripts/')
        from cwa_db import CWA_DB
        from cps.tasks.auto_hardcover_id import TaskAutoHardcoverID
        from cps.services.worker import WorkerThread

        cwa_db = CWA_DB()
        cwa_settings = cwa_db.get_cwa_settings()

        min_confidence = float(cwa_settings.get('hardcover_auto_fetch_min_confidence', 0.85))
        batch_size = int(cwa_settings.get('hardcover_auto_fetch_batch_size', 50))
        rate_limit = float(cwa_settings.get('hardcover_auto_fetch_rate_limit', 5.0))

        # Create and enqueue task
        task = TaskAutoHardcoverID(
            min_confidence=min_confidence,
            batch_size=batch_size,
            rate_limit_delay=rate_limit
        )

        WorkerThread.add(current_user.name, task, hidden=False)

        log.info(f"Hardcover auto-fetch task manually triggered by {current_user.name}")
        show_text['text'] = _('Success! Hardcover auto-fetch task started. Check Tasks panel for progress.')
        return json.dumps(show_text)

    except Exception as e:
        log.error(f"Error triggering Hardcover auto-fetch: {e}")
        show_text['text'] = _('Error starting Hardcover auto-fetch task: %(error)s', error=str(e))
        return json.dumps(show_text), 500


# method is available without login and not protected by CSRF to make it easy reachable, is per default switched off
# needed for docker applications, as changes on metadata.db from host are not visible to application
@admi.route("/reconnect", methods=['GET'])
def reconnect():
    if cli_param.reconnect_enable:
        calibre_db.reconnect_db(config, ub.app_DB_path)
        return json.dumps({})
    else:
        log.debug("'/reconnect' was accessed but is not enabled")
        abort(404)


@admi.route("/ajax/updateThumbnails", methods=['POST'])
@user_login_required
@admin_required
def update_thumbnails():
    # Always allow manual thumbnail cache updates
    log.info("Update of Cover cache requested")

    try:
        from .tasks.thumbnail import TaskGenerateCoverThumbnails
        task_id = helper.update_thumbnail_cache()

        # Check if there are any books to process
        book_count = TaskGenerateCoverThumbnails.count_books_with_covers()

        if book_count > 0:
            message = _('Thumbnail cache refresh started for {} book(s). This may take a few minutes.').format(book_count)
        else:
            message = _('No books with covers found to process.')

        return jsonify({
            'success': True,
            'message': message,
            'book_count': book_count,
            'task_id': str(task_id) if task_id else None
        })
    except Exception as e:
        log.error(f"Error starting thumbnail refresh: {e}")
        return jsonify({
            'success': False,
            'message': _('Failed to start thumbnail refresh: {}').format(str(e))
        })


@admi.route("/admin/usertable")
@user_login_required
@admin_required
def edit_user_table():
    all_user = ub.session.query(ub.User)
    if not config.config_anonbrowse:
        all_user = all_user.filter(ub.User.role.op('&')(constants.ROLE_ANONYMOUS) != constants.ROLE_ANONYMOUS)
    return render_title_template("user_table.html", users=all_user.order_by(ub.User.name).all(),
                                 title=_("Users"), page="usertable")


@admi.route("/ajax/listusers")
@user_login_required
@admin_required
def list_users():
    off = int(request.args.get("offset") or 0)
    limit = int(request.args.get("limit") or 10)
    search = request.args.get("search")
    sort = request.args.get("sort", "id")
    state = None
    if sort == "state":
        state = json.loads(request.args.get("state", "[]"))
    else:
        if sort not in ub.User.__table__.columns.keys():
            sort = "id"
    order = request.args.get("order", "").lower()
    if order not in ("asc", "desc"):
        order = ""

    if sort != "state" and order:
        column = ub.User.__table__.columns[sort]
        order = column.asc() if order == "asc" else column.desc()
    elif not state:
        order = ub.User.id.asc()

    all_user = ub.session.query(ub.User)
    if not config.config_anonbrowse:
        all_user = all_user.filter(ub.User.role.op('&')(constants.ROLE_ANONYMOUS) != constants.ROLE_ANONYMOUS)

    total_count = filtered_count = all_user.count()

    if search:
        all_user = all_user.filter(func.lower(ub.User.name).ilike("%" + search + "%"))
    if state:
        users = calibre_db.get_checkbox_sorted(all_user.all(), state, off, limit, request.args.get("order", "").lower())
    else:
        users = all_user.order_by(order).offset(off).limit(limit).all()
    if search:
        filtered_count = len(users)

    for user in users:
        if user.default_language == "all":
            user.default = _("All")
        else:
            user.default = get_user_locale_language(user.default_language)

    table_entries = {'totalNotFiltered': total_count, 'total': filtered_count, "rows": users}
    js_list = json.dumps(table_entries, cls=db.AlchemyEncoder)
    response = make_response(js_list)
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    return response


@admi.route("/ajax/deleteuser", methods=['POST'])
@user_login_required
@admin_required
def delete_user():
    user_ids = request.form.to_dict(flat=False)
    users = None
    message = ""
    if "userid[]" in user_ids:
        users = ub.session.query(ub.User).filter(ub.User.id.in_(user_ids['userid[]'])).all()
    elif "userid" in user_ids:
        users = ub.session.query(ub.User).filter(ub.User.id == user_ids['userid'][0]).all()
    count = 0
    errors = list()
    success = list()
    if not users:
        log.error("User not found")
        return Response(json.dumps({'type': "danger", 'message': _("User not found")}), mimetype='application/json')
    for user in users:
        try:
            message = _delete_user(user)
            count += 1
        except Exception as ex:
            log.error(ex)
            errors.append({'type': "danger", 'message': str(ex)})

    if count == 1:
        log.info("User {} deleted".format(user_ids))
        success = [{'type': "success", 'message': message}]
    elif count > 1:
        log.info("Users {} deleted".format(user_ids))
        success = [{'type': "success", 'message': _("{} users deleted successfully").format(count)}]
    success.extend(errors)
    return Response(json.dumps(success), mimetype='application/json')


@admi.route("/ajax/editlistusers/<param>", methods=['POST'])
@user_login_required
@admin_required
def edit_list_user(param):
    vals = request.form.to_dict(flat=False)
    all_user = ub.session.query(ub.User)
    if not config.config_anonbrowse:
        all_user = all_user.filter(ub.User.role.op('&')(constants.ROLE_ANONYMOUS) != constants.ROLE_ANONYMOUS)
    # only one user is posted
    if "pk" in vals:
        users = [all_user.filter(ub.User.id == vals['pk'][0]).one_or_none()]
    else:
        if "pk[]" in vals:
            users = all_user.filter(ub.User.id.in_(vals['pk[]'])).all()
        else:
            return _("Malformed request"), 400
    if 'field_index' in vals:
        vals['field_index'] = vals['field_index'][0]
    if 'value' in vals:
        vals['value'] = vals['value'][0]
    elif not ('value[]' in vals):
        return _("Malformed request"), 400
    for user in users:
        try:
            if param in ['denied_tags', 'allowed_tags', 'allowed_column_value', 'denied_column_value']:
                if 'value[]' in vals:
                    setattr(user, param, prepare_tags(user, vals['action'][0], param, vals['value[]']))
                else:
                    setattr(user, param, strip_whitespaces(vals['value']))
            else:
                vals['value'] = strip_whitespaces(vals['value'])
                if param == 'name':
                    if user.name == "Guest":
                        raise Exception(_("Guest Name can't be changed"))
                    user.name = check_username(vals['value'])
                elif param.endswith('role'):
                    value = int(vals['field_index'])
                    if user.name == "Guest" and value in \
                      [constants.ROLE_ADMIN, constants.ROLE_PASSWD, constants.ROLE_EDIT_SHELFS]:
                        raise Exception(_("Guest can't have this role"))
                    # check for valid value, last on checks for power of 2 value
                    if value > 0 and value <= constants.ROLE_VIEWER and (value & value - 1 == 0 or value == 1):
                        if vals['value'] == 'true':
                            user.role |= value
                        elif vals['value'] == 'false':
                            if value == constants.ROLE_ADMIN:
                                if not ub.session.query(ub.User). \
                                    filter(ub.User.role.op('&')(constants.ROLE_ADMIN) == constants.ROLE_ADMIN,
                                           ub.User.id != user.id).count():
                                    return Response(
                                        json.dumps([{'type': "danger",
                                                     'message': _("No admin user remaining, can't remove admin role",
                                                                  nick=user.name)}]), mimetype='application/json')
                            user.role &= ~value
                        else:
                            raise Exception(_("Value has to be true or false"))
                    else:
                        raise Exception(_("Invalid role"))
                elif param.startswith('sidebar'):
                    value = int(vals['field_index'])
                    if user.name == "Guest" and value == constants.SIDEBAR_READ_AND_UNREAD:
                        raise Exception(_("Guest can't have this view"))
                    # check for valid value, last on checks for power of 2 value
                    if value > 0 and value <= constants.SIDEBAR_DUPLICATES and (value & value - 1 == 0 or value == 1):
                        if vals['value'] == 'true':
                            user.sidebar_view |= value
                        elif vals['value'] == 'false':
                            user.sidebar_view &= ~value
                        else:
                            raise Exception(_("Value has to be true or false"))
                    else:
                        raise Exception(_("Invalid view"))
                elif param == 'locale':
                    if user.name == "Guest":
                        raise Exception(_("Guest's Locale is determined automatically and can't be set"))
                    if vals['value'] in get_available_translations():
                        user.locale = vals['value']
                    else:
                        raise Exception(_("No Valid Locale Given"))
                elif param == 'default_language':
                    languages = calibre_db.session.query(db.Languages) \
                        .join(db.books_languages_link) \
                        .join(db.Books) \
                        .filter(calibre_db.common_filters()) \
                        .group_by(text('books_languages_link.lang_code')).all()
                    lang_codes = [lang.lang_code for lang in languages] + ["all"]
                    if vals['value'] in lang_codes:
                        user.default_language = vals['value']
                    else:
                        raise Exception(_("No Valid Book Language Given"))
                else:
                    return _("Parameter not found"), 400
        except Exception as ex:
            log.error_or_exception(ex)
            return str(ex), 400
    ub.session_commit()
    return ""


@admi.route("/ajax/user_table_settings", methods=['POST'])
@user_login_required
@admin_required
def update_table_settings():
    current_user.view_settings['useredit'] = json.loads(request.data)
    try:
        try:
            flag_modified(current_user, "view_settings")
        except AttributeError:
            pass
        ub.session.commit()
    except (InvalidRequestError, OperationalError):
        log.error("Invalid request received: {}".format(request))
        return "Invalid request", 400
    return ""


@admi.route("/ajax/loaddialogtexts/<element_id>", methods=['POST'])
@user_login_required
def load_dialogtexts(element_id):
    texts = {"header": "", "main": "", "valid": 1}
    if element_id == "btndeluser":
        texts["main"] = _('Do you really want to delete this user?')
    elif element_id == "delete_shelf":
        texts["main"] = _('Are you sure you want to delete this shelf?')
    elif element_id == "select_locale":
        texts["main"] = _('Are you sure you want to change locales of selected user(s)?')
    elif element_id == "select_default_language":
        texts["main"] = _('Are you sure you want to change visible book languages for selected user(s)?')
    elif element_id == "role":
        texts["main"] = _('Are you sure you want to change the selected role for the selected user(s)?')
    elif element_id == "restrictions":
        texts["main"] = _('Are you sure you want to change the selected restrictions for the selected user(s)?')
    elif element_id == "sidebar_view":
        texts["main"] = _('Are you sure you want to change the selected visibility restrictions '
                          'for the selected user(s)?')
    elif element_id == "db_submit":
        texts["main"] = _('Are you sure you want to change Calibre library location?')
    return json.dumps(texts)


@admi.route("/ajax/editrestriction/<int:res_type>", defaults={"user_id": 0}, methods=['POST'])
@admi.route("/ajax/editrestriction/<int:res_type>/<int:user_id>", methods=['POST'])
@user_login_required
@admin_required
def edit_restriction(res_type, user_id):
    element = request.form.to_dict()
    if element['id'].startswith('a'):
        if res_type == 0:  # Tags as template
            elementlist = config.list_allowed_tags()
            elementlist[int(element['id'][1:])] = element['Element']
            config.config_allowed_tags = ','.join(elementlist)
            config.save()
        if res_type == 1:  # CustomC
            elementlist = config.list_allowed_column_values()
            elementlist[int(element['id'][1:])] = element['Element']
            config.config_allowed_column_value = ','.join(elementlist)
            config.save()
        if res_type == 2:  # Tags per user
            if isinstance(user_id, int):
                usr = ub.session.query(ub.User).filter(ub.User.id == int(user_id)).first()
            else:
                usr = current_user
            elementlist = usr.list_allowed_tags()
            elementlist[int(element['id'][1:])] = element['Element']
            usr.allowed_tags = ','.join(elementlist)
            ub.session_commit("Changed allowed tags of user {} to {}".format(usr.name, usr.allowed_tags))
        if res_type == 3:  # CColumn per user
            if isinstance(user_id, int):
                usr = ub.session.query(ub.User).filter(ub.User.id == int(user_id)).first()
            else:
                usr = current_user
            elementlist = usr.list_allowed_column_values()
            elementlist[int(element['id'][1:])] = element['Element']
            usr.allowed_column_value = ','.join(elementlist)
            ub.session_commit("Changed allowed columns of user {} to {}".format(usr.name, usr.allowed_column_value))
    if element['id'].startswith('d'):
        if res_type == 0:  # Tags as template
            elementlist = config.list_denied_tags()
            elementlist[int(element['id'][1:])] = element['Element']
            config.config_denied_tags = ','.join(elementlist)
            config.save()
        if res_type == 1:  # CustomC
            elementlist = config.list_denied_column_values()
            elementlist[int(element['id'][1:])] = element['Element']
            config.config_denied_column_value = ','.join(elementlist)
            config.save()
        if res_type == 2:  # Tags per user
            if isinstance(user_id, int):
                usr = ub.session.query(ub.User).filter(ub.User.id == int(user_id)).first()
            else:
                usr = current_user
            elementlist = usr.list_denied_tags()
            elementlist[int(element['id'][1:])] = element['Element']
            usr.denied_tags = ','.join(elementlist)
            ub.session_commit("Changed denied tags of user {} to {}".format(usr.name, usr.denied_tags))
        if res_type == 3:  # CColumn per user
            if isinstance(user_id, int):
                usr = ub.session.query(ub.User).filter(ub.User.id == int(user_id)).first()
            else:
                usr = current_user
            elementlist = usr.list_denied_column_values()
            elementlist[int(element['id'][1:])] = element['Element']
            usr.denied_column_value = ','.join(elementlist)
            ub.session_commit("Changed denied columns of user {} to {}".format(usr.name, usr.denied_column_value))
    return ""


@admi.route("/ajax/addrestriction/<int:res_type>", methods=['POST'])
@user_login_required
@admin_required
def add_user_0_restriction(res_type):
    return add_restriction(res_type, 0)


@admi.route("/ajax/addrestriction/<int:res_type>/<int:user_id>", methods=['POST'])
@user_login_required
@admin_required
def add_restriction(res_type, user_id):
    element = request.form.to_dict()
    if res_type == 0:  # Tags as template
        if 'submit_allow' in element:
            config.config_allowed_tags = restriction_addition(element, config.list_allowed_tags)
            config.save()
        elif 'submit_deny' in element:
            config.config_denied_tags = restriction_addition(element, config.list_denied_tags)
            config.save()
    if res_type == 1:  # CCustom as template
        if 'submit_allow' in element:
            config.config_allowed_column_value = restriction_addition(element, config.list_allowed_column_values)
            config.save()
        elif 'submit_deny' in element:
            config.config_denied_column_value = restriction_addition(element, config.list_denied_column_values)
            config.save()
    if res_type == 2:  # Tags per user
        if isinstance(user_id, int):
            usr = ub.session.query(ub.User).filter(ub.User.id == int(user_id)).first()
        else:
            usr = current_user
        if 'submit_allow' in element:
            usr.allowed_tags = restriction_addition(element, usr.list_allowed_tags)
            ub.session_commit("Changed allowed tags of user {} to {}".format(usr.name, usr.list_allowed_tags()))
        elif 'submit_deny' in element:
            usr.denied_tags = restriction_addition(element, usr.list_denied_tags)
            ub.session_commit("Changed denied tags of user {} to {}".format(usr.name, usr.list_denied_tags()))
    if res_type == 3:  # CustomC per user
        if isinstance(user_id, int):
            usr = ub.session.query(ub.User).filter(ub.User.id == int(user_id)).first()
        else:
            usr = current_user
        if 'submit_allow' in element:
            usr.allowed_column_value = restriction_addition(element, usr.list_allowed_column_values)
            ub.session_commit("Changed allowed columns of user {} to {}".format(usr.name, usr.list_allowed_column_values()))
        elif 'submit_deny' in element:
            usr.denied_column_value = restriction_addition(element, usr.list_denied_column_values)
            ub.session_commit("Changed denied columns of user {} to {}".format(usr.name, usr.list_denied_column_values()))
    return ""


@admi.route("/ajax/deleterestriction/<int:res_type>", methods=['POST'])
@user_login_required
@admin_required
def delete_user_0_restriction(res_type):
    return delete_restriction(res_type, 0)


@admi.route("/ajax/deleterestriction/<int:res_type>/<int:user_id>", methods=['POST'])
@user_login_required
@admin_required
def delete_restriction(res_type, user_id):
    element = request.form.to_dict()
    if res_type == 0:  # Tags as template
        if element['id'].startswith('a'):
            config.config_allowed_tags = restriction_deletion(element, config.list_allowed_tags)
            config.save()
        elif element['id'].startswith('d'):
            config.config_denied_tags = restriction_deletion(element, config.list_denied_tags)
            config.save()
    elif res_type == 1:  # CustomC as template
        if element['id'].startswith('a'):
            config.config_allowed_column_value = restriction_deletion(element, config.list_allowed_column_values)
            config.save()
        elif element['id'].startswith('d'):
            config.config_denied_column_value = restriction_deletion(element, config.list_denied_column_values)
            config.save()
    elif res_type == 2:  # Tags per user
        if isinstance(user_id, int):
            usr = ub.session.query(ub.User).filter(ub.User.id == int(user_id)).first()
        else:
            usr = current_user
        if element['id'].startswith('a'):
            usr.allowed_tags = restriction_deletion(element, usr.list_allowed_tags)
            ub.session_commit("Deleted allowed tags of user {}: {}".format(usr.name, element['Element']))
        elif element['id'].startswith('d'):
            usr.denied_tags = restriction_deletion(element, usr.list_denied_tags)
            ub.session_commit("Deleted denied tag of user {}: {}".format(usr.name, element['Element']))
    elif res_type == 3:  # Columns per user
        if isinstance(user_id, int):
            usr = ub.session.query(ub.User).filter(ub.User.id == int(user_id)).first()
        else:
            usr = current_user
        if element['id'].startswith('a'):
            usr.allowed_column_value = restriction_deletion(element, usr.list_allowed_column_values)
            ub.session_commit("Deleted allowed columns of user {}: {}".format(usr.name, usr.list_allowed_column_values()))

        elif element['id'].startswith('d'):
            usr.denied_column_value = restriction_deletion(element, usr.list_denied_column_values)
            ub.session_commit("Deleted denied columns of user {}: {}".format(usr.name, usr.list_denied_column_values()))
    return ""


@admi.route("/ajax/listrestriction/<int:res_type>", defaults={"user_id": 0})
@admi.route("/ajax/listrestriction/<int:res_type>/<int:user_id>")
@user_login_required
@admin_required
def list_restriction(res_type, user_id):
    if res_type == 0:  # Tags as template
        restrict = [{'Element': x, 'type': _('Deny'), 'id': 'd' + str(i)}
                    for i, x in enumerate(config.list_denied_tags()) if x != '']
        allow = [{'Element': x, 'type': _('Allow'), 'id': 'a' + str(i)}
                 for i, x in enumerate(config.list_allowed_tags()) if x != '']
        json_dumps = restrict + allow
    elif res_type == 1:  # CustomC as template
        restrict = [{'Element': x, 'type': _('Deny'), 'id': 'd' + str(i)}
                    for i, x in enumerate(config.list_denied_column_values()) if x != '']
        allow = [{'Element': x, 'type': _('Allow'), 'id': 'a' + str(i)}
                 for i, x in enumerate(config.list_allowed_column_values()) if x != '']
        json_dumps = restrict + allow
    elif res_type == 2:  # Tags per user
        if isinstance(user_id, int):
            usr = ub.session.query(ub.User).filter(ub.User.id == user_id).first()
        else:
            usr = current_user
        restrict = [{'Element': x, 'type': _('Deny'), 'id': 'd' + str(i)}
                    for i, x in enumerate(usr.list_denied_tags()) if x != '']
        allow = [{'Element': x, 'type': _('Allow'), 'id': 'a' + str(i)}
                 for i, x in enumerate(usr.list_allowed_tags()) if x != '']
        json_dumps = restrict + allow
    elif res_type == 3:  # CustomC per user
        if isinstance(user_id, int):
            usr = ub.session.query(ub.User).filter(ub.User.id == user_id).first()
        else:
            usr = current_user
        restrict = [{'Element': x, 'type': _('Deny'), 'id': 'd' + str(i)}
                    for i, x in enumerate(usr.list_denied_column_values()) if x != '']
        allow = [{'Element': x, 'type': _('Allow'), 'id': 'a' + str(i)}
                 for i, x in enumerate(usr.list_allowed_column_values()) if x != '']
        json_dumps = restrict + allow
    else:
        json_dumps = ""
    js = json.dumps(json_dumps)
    response = make_response(js)
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    return response


def restriction_addition(element, list_func):
    elementlist = list_func()
    if elementlist == ['']:
        elementlist = []
    if not element['add_element'] in elementlist:
        elementlist += [element['add_element']]
    return ','.join(elementlist)


def restriction_deletion(element, list_func):
    elementlist = list_func()
    if element['Element'] in elementlist:
        elementlist.remove(element['Element'])
    return ','.join(elementlist)


def prepare_tags(user, action, tags_name, id_list):
    if "tags" in tags_name:
        tags = calibre_db.session.query(db.Tags).filter(db.Tags.id.in_(id_list)).all()
        if not tags:
            raise Exception(_("Tag not found"))
        new_tags_list = [x.name for x in tags]
    else:
        try:
            tags = calibre_db.session.query(db.cc_classes[config.config_restricted_column]) \
                .filter(db.cc_classes[config.config_restricted_column].id.in_(id_list)).all()
        except (KeyError, AttributeError, IndexError):
            log.error("Custom Column No.{} does not exist in calibre database".format(
                config.config_restricted_column))
            raise Exception(_("Custom Column No.%(column)d does not exist in calibre database",
                    column=config.config_restricted_column))
        new_tags_list = [x.value for x in tags]
    saved_tags_list = user.__dict__[tags_name].split(",") if len(user.__dict__[tags_name]) else []
    if action == "remove":
        saved_tags_list = [x for x in saved_tags_list if x not in new_tags_list]
    elif action == "add":
        saved_tags_list.extend(x for x in new_tags_list if x not in saved_tags_list)
    else:
        raise Exception(_("Invalid Action"))
    return ",".join(saved_tags_list)


@admi.route("/admin/user/new", methods=["GET", "POST"])
@user_login_required
@admin_required
def new_user():
    content = ub.User()
    languages = calibre_db.speaking_language()
    translations = get_available_locale()
    if request.method == "POST":
        to_save = request.form.to_dict()
        # A redirect on success or the re-rendered form on a validation error
        response = _handle_new_user(to_save, content, languages, translations)
        if response:
            return response
    else:
        content.role = config.config_default_role
        content.sidebar_view = config.config_default_show
        content.locale = config.config_default_locale
        content.default_language = config.config_default_language
    opds_context = _build_opds_context(content)
    return render_title_template("user_edit.html", new_user=1, content=content,
                                 config=config, translations=translations,
                                 languages=languages, title=_("Add New User"), page="newuser",
                                 opds_root_order_string=opds_context["opds_root_order_string"],
                                 opds_hidden_entries_string=opds_context["opds_hidden_entries_string"],
                                 opds_root_labels=opds_context["opds_root_labels"])


def _build_opds_context(user):
    from .opds import (
        get_opds_root_order_for_user,
        get_opds_hidden_entries_for_user,
        OPDS_ROOT_ENTRY_DEFS,
        OPDS_ROOT_ORDER_DEFAULT,
    )
    opds_root_order = get_opds_root_order_for_user(user)
    opds_root_order_string = ",".join(opds_root_order)
    opds_hidden_entries = list(get_opds_hidden_entries_for_user(user))
    opds_hidden_entries_string = ",".join(opds_hidden_entries)
    opds_root_labels = [
        {
            "key": key,
            "label": _(OPDS_ROOT_ENTRY_DEFS[key]['title']),
        }
        for key in OPDS_ROOT_ORDER_DEFAULT
        if key in OPDS_ROOT_ENTRY_DEFS
    ]
    return {
        "opds_root_order_string": opds_root_order_string,
        "opds_hidden_entries_string": opds_hidden_entries_string,
        "opds_root_labels": opds_root_labels,
    }


@admi.route("/admin/user/<int:user_id>", methods=["GET", "POST"])
@user_login_required
@admin_required
def edit_user(user_id):
    content = ub.session.query(ub.User).filter(ub.User.id == int(user_id)).first()  # type: ub.User
    if not content or (not config.config_anonbrowse and content.name == "Guest"):
        flash(_("User not found"), category="error")
        return redirect(url_for('admin.edit_user_table'))
    languages = calibre_db.speaking_language(return_all_languages=True)
    translations = get_available_locale()

    if request.method == "POST":
        to_save = request.form.to_dict()
        resp = _handle_edit_user(to_save, content, languages, translations)
        if resp:
            return resp
    opds_context = _build_opds_context(content)
    return render_title_template("user_edit.html",
                                 translations=translations,
                                 languages=languages,
                                 new_user=0,
                                 content=content,
                                 config=config,
                                 opds_root_order_string=opds_context["opds_root_order_string"],
                                 opds_hidden_entries_string=opds_context["opds_hidden_entries_string"],
                                 opds_root_labels=opds_context["opds_root_labels"],
                                 title=_("Edit User %(nick)s", nick=content.name),
                                 page="edituser")


@admi.route("/admin/debug")
@user_login_required
@admin_required
def download_debug():
    return debug_info.send_debug()


@admi.route("/ajax/canceltask", methods=['POST'])
@user_login_required
@admin_required
def cancel_task():
    task_id = request.get_json().get('task_id', None)
    worker = WorkerThread.get_instance()
    worker.end_task(task_id)
    return ""


def _handle_new_user(to_save, content, languages, translations):
    content.default_language = to_save.get("default_language", config.config_default_language)
    content.locale = to_save.get("locale", content.locale)

    shown = [int(key[5:]) for key in to_save if key.startswith('show_')]
    content.sidebar_view = sum(shown) if shown else config.config_default_show

    content.role = constants.selected_roles(to_save)
    try:
        if not to_save["name"] or not to_save["password"]:
            log.info("Missing entries on new user")
            raise Exception(_("Oops! Please complete all fields."))
        content.password = generate_password_hash(helper.valid_password(to_save.get("password", "")))
        # Query username, if not existing, change
        content.name = check_username(to_save["name"])
    except Exception as ex:
        flash(str(ex), category="error")
        opds_context = _build_opds_context(content)
        return render_title_template("user_edit.html", new_user=1, content=content,
                                     config=config,
                                     translations=translations,
                                     languages=languages, title=_("Add New User"), page="newuser",
                                     opds_root_order_string=opds_context["opds_root_order_string"],
                                     opds_hidden_entries_string=opds_context["opds_hidden_entries_string"],
                                     opds_root_labels=opds_context["opds_root_labels"])
    try:
        content.allowed_tags = config.config_allowed_tags
        content.denied_tags = config.config_denied_tags
        content.allowed_column_value = config.config_allowed_column_value
        content.denied_column_value = config.config_denied_column_value
        ub.session.add(content)
        ub.session.commit()
        flash(_("User '%(user)s' created", user=content.name), category="success")
        log.debug("User {} created".format(content.name))
        return redirect(url_for('admin.edit_user_table'))
    except IntegrityError:
        ub.session.rollback()
        log.error("Found an existing account for {}".format(content.name))
        flash(_("This username is already taken"), category="error")
    except OperationalError as e:
        ub.session.rollback()
        log.error_or_exception("Settings Database error: {}".format(e))
        flash(_("Oops! Database Error: %(error)s.", error=e.orig), category="error")


def _delete_user(content):
    if ub.session.query(ub.User).filter(ub.User.role.op('&')(constants.ROLE_ADMIN) == constants.ROLE_ADMIN,
                                        ub.User.id != content.id).count():
        if content.name != "Guest":
            # Delete all books in shelfs belonging to user, all shelfs of user, downloadstat of user, read status
            # and user itself
            ub.session.query(ub.ReadBook).filter(content.id == ub.ReadBook.user_id).delete()
            ub.session.query(ub.Downloads).filter(content.id == ub.Downloads.user_id).delete()
            for us in ub.session.query(ub.Shelf).filter(content.id == ub.Shelf.user_id):
                ub.session.query(ub.BookShelf).filter(us.id == ub.BookShelf.shelf).delete()
            ub.session.query(ub.Shelf).filter(content.id == ub.Shelf.user_id).delete()
            ub.session.query(ub.Bookmark).filter(content.id == ub.Bookmark.user_id).delete()
            ub.session.query(ub.User).filter(ub.User.id == content.id).delete()
            ub.session.query(ub.ArchivedBook).filter(ub.ArchivedBook.user_id == content.id).delete()
            ub.session.query(ub.User_Sessions).filter(ub.User_Sessions.user_id == content.id).delete()
            ub.session_commit()
            log.info("User {} deleted".format(content.name))
            return _("User '%(nick)s' deleted", nick=content.name)
        else:
            raise Exception(_("Can't delete Guest User"))
    else:
        raise Exception(_("No admin user remaining, can't delete user"))


def _handle_edit_user(to_save, content, languages, translations):
    if to_save.get("delete"):
        try:
            flash(_delete_user(content), category="success")
        except Exception as ex:
            log.error(ex)
            flash(str(ex), category="error")
        return redirect(url_for('admin.edit_user_table'))
    if not ub.session.query(ub.User).filter(ub.User.role.op('&')(constants.ROLE_ADMIN) == constants.ROLE_ADMIN,
                                            ub.User.id != content.id).count() and 'admin_role' not in to_save:
        log.warning("No admin user remaining, can't remove admin role from {}".format(content.name))
        flash(_("No admin user remaining, can't remove admin role"), category="error")
        return redirect(url_for('admin.edit_user_table'))

    # The user form no longer shows sidebar or OPDS options, so those keep their values.
    val = [int(k[5:]) for k in to_save if k.startswith('show_')]
    if val:
        sidebar, __ = get_sidebar_config()
        for element in sidebar:
            value = element['visibility']
            if value in val and not content.check_visibility(value):
                content.sidebar_view |= value
            elif value not in val and content.check_visibility(value):
                content.sidebar_view &= ~value

    if "auto_metadata_fetch" in to_save:
        content.auto_metadata_fetch = to_save.get("auto_metadata_fetch") == "on"

    # OPDS root order
    opds_order_raw = to_save.get("opds_root_order", "").strip()
    if "opds_root_order" not in to_save:
        pass
    elif opds_order_raw:
        from .opds import normalize_opds_root_order
        opds_order_list = [item.strip() for item in opds_order_raw.split(',') if item.strip()]
        normalized_order = normalize_opds_root_order(opds_order_list)
        if content.view_settings is None:
            content.view_settings = {}
        content.view_settings.setdefault('opds', {})['root_order'] = normalized_order
        flag_modified(content, "view_settings")
    else:
        if content.view_settings and content.view_settings.get('opds', {}).get('root_order'):
            content.view_settings['opds'].pop('root_order', None)
            if not content.view_settings['opds']:
                content.view_settings.pop('opds', None)
            flag_modified(content, "view_settings")

    # OPDS hidden entries
    opds_hidden_raw = to_save.get("opds_hidden_entries", "").strip()
    if "opds_hidden_entries" not in to_save:
        pass
    elif opds_hidden_raw:
        from .opds import OPDS_ROOT_ENTRY_DEFS
        hidden_entries = [item.strip() for item in opds_hidden_raw.split(',') if item.strip()]
        hidden_entries = [key for key in hidden_entries if key in OPDS_ROOT_ENTRY_DEFS]
        if content.view_settings is None:
            content.view_settings = {}
        content.view_settings.setdefault('opds', {})['hidden_entries'] = hidden_entries
        flag_modified(content, "view_settings")
    else:
        if content.view_settings and content.view_settings.get('opds', {}).get('hidden_entries'):
            content.view_settings['opds'].pop('hidden_entries', None)
            if not content.view_settings['opds']:
                content.view_settings.pop('opds', None)
            flag_modified(content, "view_settings")

    if to_save.get("default_language"):
        content.default_language = to_save["default_language"]
    if to_save.get("locale"):
        content.locale = to_save["locale"]
    try:
        anonymous = content.is_anonymous
        content.role = constants.selected_roles(to_save)
        if anonymous:
            content.role |= constants.ROLE_ANONYMOUS
        else:
            content.role &= ~constants.ROLE_ANONYMOUS
            if to_save.get("password", ""):
                content.password = generate_password_hash(helper.valid_password(to_save.get("password", "")))

        # Query username, if not existing, change
        if to_save.get("name", content.name) != content.name:
            if to_save.get("name") == "Guest":
                raise Exception(_("Guest Name can't be changed"))
            content.name = check_username(to_save["name"])

    except Exception as ex:
        log.error(ex)
        flash(str(ex), category="error")
        opds_context = _build_opds_context(content)
        return render_title_template("user_edit.html",
                                     translations=translations,
                                     languages=languages,
                                     new_user=0,
                                     content=content,
                                     config=config,
                                     opds_root_order_string=opds_context["opds_root_order_string"],
                                     opds_hidden_entries_string=opds_context["opds_hidden_entries_string"],
                                     opds_root_labels=opds_context["opds_root_labels"],
                                     title=_("Edit User %(nick)s", nick=content.name),
                                     page="edituser")
    try:
        ub.session_commit()
        if to_save.get("password", "") and not anonymous:
            # A new password signs the user out everywhere (but not the admin doing this)
            ub.delete_other_user_sessions(
                content.id, flask_session.get('_random', '') if content.id == current_user.id else '')
        flash(_("User '%(nick)s' updated", nick=content.name), category="success")
    except IntegrityError as ex:
        ub.session.rollback()
        log.error("An unknown error occurred while changing user: {}".format(str(ex)))
        flash(_("Oops! An unknown error occurred. Please try again later."), category="error")
    except OperationalError as e:
        ub.session.rollback()
        log.error_or_exception("Settings Database error: {}".format(e))
        flash(_("Oops! Database Error: %(error)s.", error=e.orig), category="error")
    return ""
