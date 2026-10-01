# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import sys
import os
from collections import namedtuple

# APP_MODE - production, development, or test
APP_MODE            = os.environ.get('APP_MODE', 'production')

# if installed via pip this variable is set to true (empty file with name .HOMEDIR present)
HOME_CONFIG = os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.HOMEDIR'))


# Base dir is parent of current file, necessary if called from different folder
BASE_DIR            = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
# if executable file the files should be placed in the parent dir (parallel to the exe file)

STATIC_DIR          = os.path.join(BASE_DIR, 'cps', 'static')

# Cache dir - use CACHE_DIR environment variable, otherwise use the default directory: cps/cache
DEFAULT_CACHE_DIR   = os.path.join(BASE_DIR, 'cps', 'cache')
CACHE_DIR           = os.environ.get('CACHE_DIR', DEFAULT_CACHE_DIR)

if HOME_CONFIG:
    home_dir = os.path.join(os.path.expanduser("~"), ".calibre-web-automated")
    if not os.path.exists(home_dir):
        os.makedirs(home_dir)
    CONFIG_DIR = os.environ.get('CALIBRE_DBPATH', home_dir)
else:
    CONFIG_DIR = os.environ.get('CALIBRE_DBPATH', BASE_DIR)
    if getattr(sys, 'frozen', False):
        CONFIG_DIR = os.path.abspath(os.path.join(CONFIG_DIR, os.pardir))


DEFAULT_SETTINGS_FILE = "app.db"

ROLE_USER               = 0 << 0
ROLE_ADMIN              = 1 << 0
ROLE_DOWNLOAD           = 1 << 1
ROLE_UPLOAD             = 1 << 2
ROLE_EDIT               = 1 << 3
ROLE_PASSWD             = 1 << 4
ROLE_ANONYMOUS          = 1 << 5
ROLE_EDIT_SHELFS        = 1 << 6
ROLE_DELETE_BOOKS       = 1 << 7
ROLE_VIEWER             = 1 << 8

ALL_ROLES = {
                "admin_role": ROLE_ADMIN,
                "download_role": ROLE_DOWNLOAD,
                "upload_role": ROLE_UPLOAD,
                "edit_role": ROLE_EDIT,
                "passwd_role": ROLE_PASSWD,
                "edit_shelf_role": ROLE_EDIT_SHELFS,
                "delete_role": ROLE_DELETE_BOOKS,
                "viewer_role": ROLE_VIEWER,
            }

DETAIL_RANDOM           = 1 <<  0
SIDEBAR_LANGUAGE        = 1 <<  1
SIDEBAR_SERIES          = 1 <<  2
SIDEBAR_CATEGORY        = 1 <<  3
SIDEBAR_HOT             = 1 <<  4
SIDEBAR_RANDOM          = 1 <<  5
SIDEBAR_AUTHOR          = 1 <<  6
SIDEBAR_BEST_RATED      = 1 <<  7
SIDEBAR_READ_AND_UNREAD = 1 <<  8
SIDEBAR_RECENT          = 1 <<  9
SIDEBAR_SORTED          = 1 << 10
SIDEBAR_PUBLISHER       = 1 << 12
SIDEBAR_RATING          = 1 << 13
SIDEBAR_FORMAT          = 1 << 14
SIDEBAR_ARCHIVED        = 1 << 15
SIDEBAR_DOWNLOAD        = 1 << 16
SIDEBAR_LIST            = 1 << 17
SIDEBAR_DUPLICATES      = 1 << 18

sidebar_settings = {
                "detail_random": DETAIL_RANDOM,
                "sidebar_language": SIDEBAR_LANGUAGE,
                "sidebar_series": SIDEBAR_SERIES,
                "sidebar_category": SIDEBAR_CATEGORY,
                "sidebar_random": SIDEBAR_RANDOM,
                "sidebar_author": SIDEBAR_AUTHOR,
                "sidebar_best_rated": SIDEBAR_BEST_RATED,
                "sidebar_read_and_unread": SIDEBAR_READ_AND_UNREAD,
                "sidebar_recent": SIDEBAR_RECENT,
                "sidebar_sorted": SIDEBAR_SORTED,
                "sidebar_publisher": SIDEBAR_PUBLISHER,
                "sidebar_rating": SIDEBAR_RATING,
                "sidebar_format": SIDEBAR_FORMAT,
                "sidebar_archived": SIDEBAR_ARCHIVED,
                "sidebar_download": SIDEBAR_DOWNLOAD,
                "sidebar_list": SIDEBAR_LIST,
                "sidebar_duplicates": SIDEBAR_DUPLICATES,
            }


ADMIN_USER_ROLES        = sum(r for r in ALL_ROLES.values()) & ~ROLE_ANONYMOUS
# Lily's default sidebar: the core browse views only. The other entries stay available
# to switch on per user in the profile's sidebar settings.
DEFAULT_SIDEBAR         = (SIDEBAR_RECENT | SIDEBAR_CATEGORY | SIDEBAR_SERIES | SIDEBAR_AUTHOR
                           | SIDEBAR_READ_AND_UNREAD | SIDEBAR_ARCHIVED)
ADMIN_USER_SIDEBAR      = DEFAULT_SIDEBAR

DEFAULT_ADMIN_NAME  = "harry"
DEFAULT_PASSWORD    = "harry10"  # nosec
DEFAULT_PORT        = 8083
env_CWA_PORT_OVERRIDE = os.environ.get("CWA_PORT_OVERRIDE")
if env_CWA_PORT_OVERRIDE:
    try:
        DEFAULT_PORT = int(env_CWA_PORT_OVERRIDE)
    except (ValueError, TypeError):
        print(f"Environment variable CWA_PORT_OVERRIDE has invalid value ('{env_CWA_PORT_OVERRIDE}'), falling back to default (8083)")
        DEFAULT_PORT = 8083


EXTENSIONS_AUDIO = {'mp3', 'mp4', 'ogg', 'opus', 'wav', 'flac', 'm4a', 'm4b'}
EXTENSIONS_BOOK = {'epub', 'pdf', 'djvu', 'djv'}
EXTENSIONS_UPLOAD = EXTENSIONS_BOOK | EXTENSIONS_AUDIO

_extension = ""
if sys.platform == "win32":
    _extension = ".exe"
SUPPORTED_CALIBRE_BINARIES = {binary: binary + _extension for binary in ["ebook-convert", "calibredb"]}


def has_flag(value, bit_flag):
    return bit_flag == (bit_flag & (value or 0))


def selected_roles(dictionary):
    return sum(v for k, v in ALL_ROLES.items() if k in dictionary)


# :rtype: BookMeta
BookMeta = namedtuple('BookMeta', 'file_path, extension, title, author, cover, description, tags, series, '
                                  'series_id, languages, publisher, pubdate, identifiers')

def _read_text(path: str, default: str = "") -> str:
    try:
        with open(path, 'r') as f:
            return f.read().strip()
    except Exception:
        return default

# Versions are resolved at container startup by cwa-init and provided via env and persisted files.
# Avoid any network or slow I/O during module import.
INSTALLED_VERSION = os.environ.get("CWA_INSTALLED_VERSION") or _read_text("/app/CWA_RELEASE", "v0.0.0")
STABLE_VERSION = os.environ.get("CWA_STABLE_VERSION") or _read_text("/app/CWA_STABLE_RELEASE", "v0.0.0")

USER_AGENT = f"Lily/{INSTALLED_VERSION}"

NIGHTLY_VERSION = dict()
NIGHTLY_VERSION[0] = '0af52f205358b0147ee3430f9e6c8fe007c0ea77'
NIGHTLY_VERSION[1] = '2024-11-16T07:21:28+01:00'

# CACHE
CACHE_TYPE_THUMBNAILS    = 'thumbnails'

# Thumbnail Types
THUMBNAIL_TYPE_COVER     = 1
THUMBNAIL_TYPE_SERIES    = 2

# Thumbnails Sizes
COVER_THUMBNAIL_ORIGINAL = 0
COVER_THUMBNAIL_SMALL    = 1
COVER_THUMBNAIL_MEDIUM   = 2
COVER_THUMBNAIL_LARGE    = 4

# clean-up the module namespace
del sys, os, namedtuple
