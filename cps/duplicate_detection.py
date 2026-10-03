# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Who sees which duplicate groups: dismissed-group filtering and the library visibility
filters (language, tags, restricted column) for a given user. Grouping itself lives in
cps/duplicate_index.py."""

from sqlalchemy import and_
from sqlalchemy.sql.expression import true, false

from . import db, calibre_db, logger, ub, config
from .cw_login import current_user

log = logger.create()


def _is_dismissed(group, dismissed_hashes):
    # legacy_group_hash: groups dismissed before groups were keyed by their books
    return group.get('group_hash') in dismissed_hashes or group.get('legacy_group_hash') in dismissed_hashes


def filter_dismissed_groups(duplicate_groups, user_id=None):
    """Drop the groups `user_id` dismissed.

    With no user (a scheduled or after-import scan feeding auto-resolve), a group
    any user dismissed is dropped, and a failed lookup drops every group: books
    someone chose to keep must never be resolved away.
    """
    if not duplicate_groups:
        return []
    if user_id is None:
        try:
            user_id = current_user.id
        except Exception:
            user_id = None

    try:
        query = ub.session.query(ub.DismissedDuplicateGroup.group_hash)
        if user_id:
            query = query.filter(ub.DismissedDuplicateGroup.user_id == user_id)
        dismissed_hashes = {row[0] for row in query.all()}
    except Exception as e:
        log.error("[cwa-duplicates] Error filtering dismissed groups: %s", str(e))
        return duplicate_groups if user_id else []
    if not dismissed_hashes:
        return duplicate_groups
    return [group for group in duplicate_groups if not _is_dismissed(group, dismissed_hashes)]


def get_common_filters(user_id=None):
    """Build common filters using either current_user or a specific user_id.

    Falls back to no-op filters if user context is unavailable.
    """
    try:
        if user_id is None:
            return calibre_db.common_filters()
    except Exception:
        # No request context; fall back to permissive filter
        return true()

    try:
        user = ub.session.query(ub.User).filter(ub.User.id == int(user_id)).first()
        if not user:
            return true()

        negtags_list = user.list_denied_tags()
        postags_list = user.list_allowed_tags()
        neg_content_tags_filter = false() if negtags_list == [''] else db.Books.tags.any(db.Tags.name.in_(negtags_list))
        pos_content_tags_filter = true() if postags_list == [''] else db.Books.tags.any(db.Tags.name.in_(postags_list))

        if config.config_restricted_column:
            try:
                pos_cc_list = (user.allowed_column_value or '').split(',')
                pos_content_cc_filter = true() if pos_cc_list == [''] else \
                    getattr(db.Books, 'custom_column_' + str(config.config_restricted_column)). \
                    any(db.cc_classes[config.config_restricted_column].value.in_(pos_cc_list))
                neg_cc_list = (user.denied_column_value or '').split(',')
                neg_content_cc_filter = false() if neg_cc_list == [''] else \
                    getattr(db.Books, 'custom_column_' + str(config.config_restricted_column)). \
                    any(db.cc_classes[config.config_restricted_column].value.in_(neg_cc_list))
            except Exception:
                pos_content_cc_filter = false()
                neg_content_cc_filter = true()
        else:
            pos_content_cc_filter = true()
            neg_content_cc_filter = false()

        return and_(pos_content_tags_filter, ~neg_content_tags_filter,
                    pos_content_cc_filter, ~neg_content_cc_filter)
    except Exception:
        return true()
