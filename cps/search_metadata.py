# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import concurrent.futures
import importlib
import inspect
import json
import os
import re
import sys

from flask import Blueprint, request, url_for, make_response, jsonify, copy_current_request_context
from .cw_login import current_user
from flask_babel import get_locale
from sqlalchemy.exc import InvalidRequestError, OperationalError
from sqlalchemy.orm.attributes import flag_modified

from cps.services.Metadata import Metadata
from . import constants, logger, ub, web_server
from .usermanagement import user_login_required


meta = Blueprint("metadata", __name__)

log = logger.create()

try:
    from dataclasses import asdict
except ImportError:
    log.info('*** "dataclasses" is needed for calibre-web automated to run. Please install it using pip: "pip install dataclasses" ***')
    print('*** "dataclasses" is needed for calibre-web automated to run. Please install it using pip: "pip install dataclasses" ***')
    web_server.stop(True)
    sys.exit(6)

new_list = list()
meta_dir = os.path.join(constants.BASE_DIR, "cps", "metadata_provider")
modules = os.listdir(os.path.join(constants.BASE_DIR, "cps", "metadata_provider"))
for f in modules:
    if os.path.isfile(os.path.join(meta_dir, f)) and not f.endswith("__init__.py"):
        a = os.path.basename(f)[:-3]
        try:
            importlib.import_module("cps.metadata_provider." + a)
            new_list.append(a)
        except (IndentationError, SyntaxError) as e:
            log.error("Syntax error for metadata source: {} - {}".format(a, e))
        except ImportError as e:
            log.debug("Import error for metadata source: {} - {}".format(a, e))


def list_classes(provider_list):
    classes = list()
    for element in provider_list:
        for name, obj in inspect.getmembers(
            sys.modules["cps.metadata_provider." + element]
        ):
            if (
                inspect.isclass(obj)
                and name != "Metadata"
                and issubclass(obj, Metadata)
            ):
                classes.append(obj())
    return classes


cl = list_classes(new_list)
# Alphabetises the list of Metadata providers
cl.sort(key=lambda x: x.__class__.__name__)


# Helper to load global provider enablement map from CWA settings
def _get_global_provider_enabled_map() -> dict:
    try:
        # Import here to avoid circular import issues and keep startup fast
        sys.path.insert(1, '/app/calibre-web-automated/scripts/')
        from cwa_db import CWA_DB  # type: ignore
        cwa_db = CWA_DB()
        settings = cwa_db.get_cwa_settings()

        if not settings:
            log.warning("Could not get CWA settings for provider enabled map")
            return {}

        from cps.cwa_functions import parse_metadata_providers_enabled
        return parse_metadata_providers_enabled(
            settings.get('metadata_providers_enabled', '{}')
        )
    except Exception as e:
        # On any failure, treat as all enabled (empty dict = all default to enabled)
        log.warning(f"Error loading provider enabled map: {e}")
        return {}
    # Remove redundant return

@meta.route("/metadata/provider")
@user_login_required
def metadata_provider():
    active = current_user.view_settings.get("metadata", {})
    global_enabled = _get_global_provider_enabled_map()
    provider = list()
    for c in cl:
        ac = active.get(c.__id__, True)
        provider.append(
            {
                "name": c.__name__,
                "active": ac,
                "initial": ac,
                "id": c.__id__,
                "globally_enabled": c.is_globally_enabled(global_enabled),
            }
        )
    return make_response(jsonify(provider))


@meta.route("/metadata/provider", methods=["POST"])
@meta.route("/metadata/provider/<prov_name>", methods=["POST"])
@user_login_required
def metadata_change_active_provider(prov_name):
    new_state = request.get_json()
    active = current_user.view_settings.get("metadata", {})
    active[new_state["id"]] = new_state["value"]
    current_user.view_settings["metadata"] = active
    try:
        try:
            flag_modified(current_user, "view_settings")
        except AttributeError:
            pass
        ub.session.commit()
    except (InvalidRequestError, OperationalError):
        log.error("Invalid request received: {}".format(request))
        return "Invalid request", 400
    if "initial" in new_state and prov_name:
        data = []
        provider = next((c for c in cl if c.__id__ == prov_name), None)
        # Respect global disablement for preview search as well
        global_enabled = _get_global_provider_enabled_map()
        if provider is not None:
            if provider.is_globally_enabled(global_enabled):
                try:
                    data = provider.search(new_state.get("query", ""))
                except Exception as exc:
                    log.warning("Metadata provider %s failed: %s", provider.__class__.__name__, exc)
                    data = []
            else:
                data = []
        if not data:
            return make_response(jsonify([]))
        return make_response(jsonify([asdict(x) for x in data if x]))
    return ""


ISBN_RE = re.compile(r"^(97[89])?\d{9}[\dX]$")


def _normalise_identifiers(raw_json, query):
    """Identifiers worth an exact lookup, from the edit form's identifier table plus the
    query itself when it is a bare ISBN. Keys are lower-case identifier types."""
    identifiers = {}
    try:
        raw = json.loads(raw_json) if raw_json else {}
    except (TypeError, ValueError):
        raw = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(key, str) and isinstance(value, str) and value.strip():
                identifiers[key.strip().lower()] = value.strip()
    compact = re.sub(r"[\s-]", "", query or "").upper()
    if ISBN_RE.match(compact):
        identifiers.setdefault("isbn", compact)
    if "isbn" in identifiers:
        identifiers["isbn"] = re.sub(r"[\s-]", "", identifiers["isbn"]).upper()
    return identifiers


@meta.route("/metadata/search", methods=["POST"])
@user_login_required
def metadata_search():
    form = request.form.to_dict()
    query = form.get("query")
    identifiers = _normalise_identifiers(form.get("identifiers"), query)
    active = current_user.view_settings.get("metadata", {})
    locale = get_locale()
    global_enabled = _get_global_provider_enabled_map()
    exact, fuzzy = [], []
    if query or identifiers:
        static_cover = url_for("static", filename="generic_cover.svg")
        providers = [c for c in cl if active.get(c.__id__, True) and c.is_globally_enabled(global_enabled)]
        # Every provider's text search and identifier lookup run at once; the slowest sets the pace
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            jobs = {}
            for c in providers:
                if query:
                    jobs[executor.submit(copy_current_request_context(c.search), query, static_cover, locale)] = (c, fuzzy)
                if identifiers:
                    jobs[executor.submit(copy_current_request_context(c.search_identifiers),
                                         identifiers, static_cover, locale)] = (c, exact)
            for future in concurrent.futures.as_completed(jobs):
                provider, bucket = jobs[future]
                try:
                    result = future.result()
                except Exception as exc:
                    log.warning("Metadata provider %s failed: %s", provider.__class__.__name__, exc)
                    continue
                bucket.extend(x for x in (result or []) if x)
    # Exact identifier matches first, then text results they don't already cover
    data, seen = [], set()
    for record in exact + fuzzy:
        key = (record.source.description, str(record.id))
        if key in seen:
            continue
        seen.add(key)
        item = asdict(record)
        item["exact_match"] = record in exact
        data.append(item)
    return make_response(jsonify(data))
