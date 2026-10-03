# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The admin "Get started" card on the home page: which first-run steps are still open.

Everything is computed from config on the server; dismissal is per user in localStorage (index.html).
"""
from flask import url_for
from flask_babel import gettext as _
from werkzeug.security import check_password_hash

from . import constants, logger, ub
from .cw_login import current_user

log = logger.create()

# check_password_hash is deliberately slow, so remember the answer per stored hash:
# a new password means a new hash, which is re-checked once.
_default_password_cache = {}


def _uses_default_password(user):
    stored = user.password or ""
    if not stored:
        return False
    cached = _default_password_cache.get(stored)
    if cached is None:
        try:
            cached = any(check_password_hash(stored, pw) for pw in constants.DEFAULT_PASSWORDS)
        except (ValueError, TypeError):
            cached = False
        if len(_default_password_cache) > 64:
            _default_password_cache.clear()
        _default_password_cache[stored] = cached
    return cached


def _admin_on_default_password():
    """The first admin account that still signs in with the default password, else None."""
    candidates = [current_user]
    default_admin = ub.session.query(ub.User).filter(ub.User.name == constants.DEFAULT_ADMIN_NAME).first()
    if default_admin is not None and default_admin.id != current_user.id:
        candidates.append(default_admin)
    for user in candidates:
        if user.role_admin() and _uses_default_password(user):
            return user
    return None


def setup_checklist():
    """[{id, label, hint, done, href}] for admins while any step is open, else None."""
    if not current_user.is_authenticated or not current_user.role_admin():
        return None
    try:
        default_pw_user = _admin_on_default_password()
        if default_pw_user is None or default_pw_user.id == current_user.id:
            password_href = url_for("web.profile")
        else:
            password_href = url_for("admin.edit_user", user_id=default_pw_user.id)
        items = [
            {"id": "password", "label": _("Default admin password changed"),
             "hint": _("The admin account still accepts the default password."),
             "done": default_pw_user is None, "href": password_href},
        ]
    except Exception as ex:  # the card is a convenience; never break the home page over it
        log.debug("Could not build the setup checklist: %s", ex)
        return None
    if all(item["done"] for item in items):
        return None
    return items
